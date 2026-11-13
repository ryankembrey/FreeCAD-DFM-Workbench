# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Matching faces between two shapes, fast, before any boolean.

A solid boolean between a part and its mirror image (or two revisions of
a part) is slow: nearly every face coincides with another, the hardest
case for the boolean engine. But a face whose image is an identical face
of the other shape can't be where they differ. So:

* Each face gets a signature: surface type, area, centroid, and its
  normal or axis and radius. Matching signatures under a transform takes
  milliseconds.
* Where every face matches, the shapes are the same: no boolean at all.
* Where some don't, :func:`local_boxes` puts a box round each mismatch, and
  the boolean runs only inside the boxes. A large face that differs only
  in part (the top of a plate with a boss on one side) is compared with
  its partner face by a cheap face boolean, so its box covers just the
  part that differs.
* :func:`vote_plane_offsets` finds where a mirror plane fits best from the
  face pairs it would map onto each other, without trying positions one
  by one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_Torus
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopoDS import TopoDS_Shape
from OCP.gp import gp_Trsf

from . import geometry as G

_PLANE, _CYL, _CONE, _SPHERE, _TORUS, _OTHER = "plane", "cylinder", "cone", "sphere", "torus", "other"


@dataclass
class FaceSig:
    index: int  # 1-based
    kind: str
    area: float
    centre: G.Vec
    #: Outward normal (plane) or axis (cylinder, cone, torus), or None.
    direction: Optional[G.Vec]
    #: Radius (cylinder, sphere, torus major), or 0.
    radius: float
    box: G.Box
    #: False once the box has been turned off-axis (it is then only a bound).
    exact_box: bool = True
    #: Inertia of the face's area about its centroid, rows of a 3 x 3 matrix.
    inertia: Optional[tuple] = None


def signatures(shape: TopoDS_Shape) -> list[FaceSig]:
    out = []
    fm = G.face_map_of(shape)
    for i in range(1, fm.Extent() + 1):
        face = fm.FindKey(i)
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        c = props.CentreOfMass()
        direction, radius, kind = None, 0.0, _OTHER
        try:
            ad = BRepAdaptor_Surface(_as_face(face))
            t = ad.GetType()
            if t == GeomAbs_Plane:
                kind = _PLANE
                n = ad.Plane().Axis().Direction()
                direction = (n.X(), n.Y(), n.Z())
                if face.Orientation() == TopAbs_REVERSED:
                    direction = G.vmul(direction, -1.0)
            elif t == GeomAbs_Cylinder:
                kind = _CYL
                cyl = ad.Cylinder()
                a = cyl.Axis().Direction()
                direction, radius = (a.X(), a.Y(), a.Z()), cyl.Radius()
            elif t == GeomAbs_Cone:
                kind = _CONE
                a = ad.Cone().Axis().Direction()
                direction = (a.X(), a.Y(), a.Z())
            elif t == GeomAbs_Sphere:
                kind, radius = _SPHERE, ad.Sphere().Radius()
            elif t == GeomAbs_Torus:
                kind = _TORUS
                tor = ad.Torus()
                a = tor.Axis().Direction()
                direction, radius = (a.X(), a.Y(), a.Z()), tor.MajorRadius()
        except Exception:
            pass
        inertia = None
        try:
            m = props.MatrixOfInertia()
            inertia = tuple(tuple(m.Value(r, k) for k in (1, 2, 3)) for r in (1, 2, 3))
        except Exception:
            pass
        out.append(FaceSig(i, kind, abs(props.Mass()), (c.X(), c.Y(), c.Z()), direction, radius,
                           G.bounding_box(face), True, inertia))
    return out


def _as_face(shape):
    from OCP.TopoDS import TopoDS

    return TopoDS.Face_s(shape)


def moved(sig: FaceSig, trsf: gp_Trsf) -> FaceSig:
    d = G.apply_dir(trsf, sig.direction) if sig.direction is not None else None
    corners = [G.apply(trsf, c) for c in sig.box.corners()]
    box = G.Box(tuple(min(c[i] for c in corners) for i in range(3)),  # type: ignore[arg-type]
                tuple(max(c[i] for c in corners) for i in range(3)))
    return FaceSig(sig.index, sig.kind, sig.area, G.apply(trsf, sig.centre), d, sig.radius, box,
                   sig.exact_box and _axis_aligned(trsf), _turned(sig.inertia, trsf))


def _turned(inertia, trsf: gp_Trsf):
    """R I R^T: the inertia matrix after *trsf* (a rotation or a mirror)."""
    if inertia is None:
        return None
    r = [[trsf.Value(i, j) for j in (1, 2, 3)] for i in (1, 2, 3)]
    ri = [[sum(r[i][k] * inertia[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    return tuple(tuple(sum(ri[i][k] * r[j][k] for k in range(3)) for j in range(3)) for i in range(3))


def _axis_aligned(trsf: gp_Trsf) -> bool:
    """True when *trsf* maps axis-aligned boxes onto axis-aligned boxes."""
    for col in range(1, 4):
        big = sum(1 for row in range(1, 4) if abs(abs(trsf.Value(row, col)) - 1.0) < 1e-9)
        small = sum(1 for row in range(1, 4) if abs(trsf.Value(row, col)) < 1e-9)
        if big != 1 or small != 2:
            return False
    return True


def _same(a: FaceSig, b: FaceSig, tol: float) -> bool:
    if a.kind != b.kind:
        return False
    if abs(a.area - b.area) > max(1e-4 * max(a.area, b.area), tol * tol):
        return False
    if G.vlen(G.vsub(a.centre, b.centre)) > tol:
        return False
    if a.direction is not None and b.direction is not None:
        dot = G.vdot(a.direction, b.direction)
        if a.kind == _PLANE:
            if dot < 1.0 - 1e-6:
                return False
        elif abs(dot) < 1.0 - 1e-6:
            return False
    if abs(a.radius - b.radius) > max(tol, 1e-7):
        return False
    # How its area is spread: a face can keep its area and centroid and still
    # differ (a flange face with one bolt hole missing, turned one step).
    if a.inertia is not None and b.inertia is not None:
        reach = max(a.box.diagonal, b.box.diagonal) if a.box is not None and b.box is not None else 1.0
        scale = max(abs(v) for row in a.inertia for v in row)
        slack = 4.0 * a.area * reach * tol + 1e-6 * scale
        for i in range(3):
            for j in range(3):
                if abs(a.inertia[i][j] - b.inertia[i][j]) > slack:
                    return False
    # The extents too.
    # (Only where the transform keeps boxes exact enough: checked by the caller.)
    if a.box is not None and b.box is not None and a.exact_box and b.exact_box:
        slack = 1.5 * tol
        for i in range(3):
            if abs(a.box.lo[i] - b.box.lo[i]) > slack or abs(a.box.hi[i] - b.box.hi[i]) > slack:
                return False
    return True


class _Grid:
    """Signatures bucketed by centroid, for finding neighbours fast."""

    def __init__(self, sigs: Sequence[FaceSig], cell: float):
        self.cell = max(cell, 1e-9)
        self.cells: dict[tuple, list[FaceSig]] = {}
        for s in sigs:
            self.cells.setdefault(self._key(s.centre), []).append(s)

    def _key(self, p):
        return tuple(int(math.floor(v / self.cell)) for v in p)

    def near(self, p):
        kx, ky, kz = self._key(p)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    yield from self.cells.get((kx + dx, ky + dy, kz + dz), ())


def match(a: Sequence[FaceSig], b: Sequence[FaceSig], trsf: Optional[gp_Trsf], tol: float):
    """Faces of *a* whose image under *trsf* is a face of *b*:
    (pairs {a index: b index}, unmatched a indices, unmatched b indices)."""
    tol = max(tol, 1e-6)
    grid = _Grid(b, tol * 4.0)
    pairs: dict[int, int] = {}
    used: set[int] = set()
    for s in a:
        image = moved(s, trsf) if trsf is not None else s
        for cand in grid.near(image.centre):
            if cand.index not in used and _same(image, cand, tol):
                pairs[s.index] = cand.index
                used.add(cand.index)
                break
    return pairs, [s.index for s in a if s.index not in pairs], [s.index for s in b if s.index not in used]


def unmatched_area(sigs: Sequence[FaceSig], trsf: gp_Trsf, tol: float) -> float:
    """Area of the faces with no partner under *trsf* (a shape against itself)."""
    _pairs, lost, _ = match(sigs, sigs, trsf, tol)
    by = {s.index: s for s in sigs}
    return sum(by[i].area for i in lost)


def vote_plane_offsets(sigs: Sequence[FaceSig], normal: Sequence[float], tol: float,
                       top: int = 5) -> list[float]:
    """Positions along *normal* where a mirror plane maps much face area
    onto matching faces, best first: each pair of like faces whose
    centroids differ only along the normal votes for the plane half way
    between them. The caller scores the few it returns."""
    n = G.vunit(normal)
    buckets: dict[tuple, list[FaceSig]] = {}
    for s in sigs:
        buckets.setdefault((s.kind, round(s.area / max(tol * tol, 1e-9))), []).append(s)
    votes: dict[int, list[float]] = {}  # cell: [weight, weighted position]
    cell = max(tol, 1e-6)
    for group in buckets.values():
        if len(group) > 60:
            group = sorted(group, key=lambda s: -s.area)[:60]
        for i, a in enumerate(group):
            for b in group[i:]:
                d = G.vsub(b.centre, a.centre)
                along = G.vdot(d, n)
                if G.vlen(G.vsub(d, G.vmul(n, along))) > tol:
                    continue
                p = G.vdot(G.vmul(G.vadd(a.centre, b.centre), 0.5), n)
                w = a.area if a is b else a.area + b.area
                slot = votes.setdefault(int(round(p / cell)), [0.0, 0.0])
                slot[0] += w
                slot[1] += w * p
    ranked = sorted(votes.values(), key=lambda v: -v[0])[:top]
    return [v[1] / v[0] for v in ranked if v[0] > 0]


def local_boxes(own: TopoDS_Shape, own_sigs: Sequence[FaceSig], lost_own: Sequence[int],
                tool: TopoDS_Shape, tool_sigs: Sequence[FaceSig], lost_tool: Sequence[int],
                tol: float) -> Optional[list[G.Box]]:
    """Boxes round where *own* and *tool* differ, from their unmatched faces;
    None when they'd cover most of the part (do the whole boolean then)."""
    own_box = G.bounding_box(own, optimal=False)
    margin = max(10.0 * tol, 0.01 * own_box.diagonal, 1e-3)
    big = 0.35 * own_box.diagonal
    boxes: list[G.Box] = []
    by_own = {s.index: s for s in own_sigs}
    by_tool = {s.index: s for s in tool_sigs}
    own_faces, tool_faces = G.face_map_of(own), G.face_map_of(tool)

    def partial(face, sig, others, other_faces):
        """Where a big face differs from the like face it lies on."""
        for o in others:
            if o.kind != sig.kind or o.kind != _PLANE or sig.direction is None or o.direction is None:
                continue
            if G.vdot(sig.direction, o.direction) < 1.0 - 1e-6:
                continue
            if abs(G.vdot(G.vsub(o.centre, sig.centre), sig.direction)) > tol:
                continue
            if not o.box.grown(tol).overlaps(sig.box):
                continue
            try:
                pieces = _face_difference(face, other_faces.FindKey(o.index), tol)
                pieces += _face_difference(other_faces.FindKey(o.index), face, tol)
            except Exception:
                return None
            return [G.bounding_box(p, optimal=False) for p in pieces]
        return None

    for lost, by, faces, others, other_faces in (
            (lost_own, by_own, own_faces, [by_tool[i] for i in lost_tool], tool_faces),
            (lost_tool, by_tool, tool_faces, [by_own[i] for i in lost_own], own_faces)):
        for i in lost:
            sig = by[i]
            if sig.box.diagonal > big:
                found = partial(faces.FindKey(i), sig, others, other_faces)
                if found is None:
                    return None
                boxes.extend(found)
            else:
                boxes.append(sig.box)
    if not boxes:
        return []
    merged = _merge([b.grown(margin) for b in boxes])
    total = sum(_vol(b) for b in merged)
    if total > 0.6 * _vol(own_box.grown(margin)):
        return None
    return merged


def _face_difference(a, b, tol: float) -> list:
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.TopTools import TopTools_ListOfShape

    args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    args.Append(a)
    tools.Append(b)
    op = BRepAlgoAPI_Cut()
    op.SetArguments(args)
    op.SetTools(tools)
    op.SetFuzzyValue(max(tol, 1e-7))
    op.SetNonDestructive(True)
    op.Build()
    if not op.IsDone():
        raise RuntimeError("face boolean failed")
    return [f for f in G.faces(op.Shape()) if G.face_area(f) > tol * tol]


def _vol(b: G.Box) -> float:
    s = b.size
    return max(s[0], 0.0) * max(s[1], 0.0) * max(s[2], 0.0)


def _merge(boxes: list[G.Box]) -> list[G.Box]:
    boxes = list(boxes)
    changed = True
    while changed:
        changed = False
        out: list[G.Box] = []
        for b in boxes:
            for k, o in enumerate(out):
                if o.overlaps(b):
                    out[k] = G.Box(tuple(min(o.lo[i], b.lo[i]) for i in range(3)),  # type: ignore[arg-type]
                                   tuple(max(o.hi[i], b.hi[i]) for i in range(3)))
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes
