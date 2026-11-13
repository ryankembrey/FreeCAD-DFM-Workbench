# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Is a part symmetric, and if not, what isn't?

The part is mirrored in a plane (or turned about an axis by one step of
its copies) and the copy is subtracted from it. What is left is material
with no partner: each connected piece is a region. Regions that touch the
same feature are one difference: a hole shifted 1.5 mm leaves a sliver on
each side, but it is one hole.

Each difference is one of three kinds:

* EXTRA: material on one side only (a boss with no partner). Its faces are
  the part's own; its partner would be its mirror image.
* CUT: a cut on one side only (a pocket with no partner). The region left
  over is plain material where the partner pocket would be; the pocket's
  own faces are the mirror images of the faces that bound it.
* MISMATCH: something on both sides that doesn't line up (a hole shifted
  or a different size).

Pieces closer than the tolerance to their partner don't count.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from OCP.TopoDS import TopoDS_Shape

from . import geometry as G
from . import matching as MT
from .models import CUT, EXTRA, MISMATCH, Difference, SymmetryResult
from .naming import FaceFeatures, feature_name, fmt_mm, same_feature

MIRROR, ROTATIONAL = "mirror", "rotational"
BEST, CENTRE, BOX_CENTRE, ORIGIN, POINT = "best", "centre", "box", "origin", "point"

AXES = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
#: A mirror plane by name, and the axis it is normal to.
PLANES = {"YZ": "X", "XZ": "Y", "XY": "Z"}

Progress = Optional[Callable[[str, float], None]]


@dataclass
class SymmetrySpec:
    kind: str = MIRROR
    #: Plane normal (mirror) or axis (rotational); None to find the best one.
    direction: Optional[G.Vec] = None
    #: How the plane or axis is placed: BEST (where the part matches its
    #: mirror image best), CENTRE (of mass), BOX_CENTRE, ORIGIN or POINT.
    through: str = BEST
    #: The point, for POINT (a picked face's).
    point: Optional[G.Vec] = None
    #: Moves a mirror plane along its normal (mm). Not used for an axis.
    offset: float = 0.0
    copies: int = 6
    tolerance: float = 0.01


def axis_letter(direction: Sequence[float], tol_deg: float = 0.01) -> str:
    """"X", "Y" or "Z" for a direction along an axis (either way), else ""."""
    d = G.vunit(direction)
    for name, a in AXES.items():
        if abs(abs(G.vdot(d, a)) - 1.0) < 1.0 - math.cos(math.radians(tol_deg)) + 1e-12:
            return name
    return ""


def plane_name(normal: Sequence[float]) -> str:
    letter = axis_letter(normal)
    return {v: k for k, v in PLANES.items()}.get(letter, "")


def through_point(shape: TopoDS_Shape, through: str, point: Optional[Sequence[float]] = None) -> G.Vec:
    if through in (BEST, BOX_CENTRE):
        return G.bounding_box(shape).centre
    if through == CENTRE:
        return G.centre(shape)
    if through == POINT and point is not None:
        return tuple(point)  # type: ignore[return-value]
    return (0.0, 0.0, 0.0)


def midplane(first: tuple[Sequence[float], Sequence[float]], second: tuple[Sequence[float], Sequence[float]],
             max_angle_deg: float = 0.5) -> Optional[tuple[G.Vec, G.Vec]]:
    """The plane half way between two parallel flat faces, as (point, normal).

    Each face is given as (a point on it, its normal). None when the faces
    aren't parallel or lie in the same plane: there is no plane between them.
    """
    p1, n1 = tuple(first[0]), G.vunit(first[1])
    p2, n2 = tuple(second[0]), G.vunit(second[1])
    if abs(G.vdot(n1, n2)) < math.cos(math.radians(max_angle_deg)):
        return None
    gap = G.vdot(G.vsub(p2, p1), n1)
    if abs(gap) < 1e-6:
        return None
    return G.vadd(p1, G.vmul(n1, gap / 2.0)), n1


def transform_for(kind: str, origin: Sequence[float], direction: Sequence[float], copies: int = 2):
    if kind == ROTATIONAL:
        return G.rotation(origin, direction, 360.0 / max(int(copies), 2))
    return G.mirror(origin, direction)


def candidates(shape: TopoDS_Shape) -> list[tuple[G.Vec, str]]:
    """Directions worth trying: the three axes, then the part's principal axes."""
    out = [(AXES[k], k) for k in ("X", "Y", "Z")]
    try:
        for axis in G.mass_props(shape).axes:
            a = G.vunit(axis)
            if all(abs(G.vdot(a, d)) < math.cos(math.radians(1.0)) for d, _ in out):
                out.append((a, ""))
    except Exception:
        pass
    return out


def asymmetry(shape: TopoDS_Shape, kind: str, origin, direction, copies: int, tolerance: float,
              sigs=None) -> float:
    """Area of the faces with no partner (mm²): how far from symmetric, fast."""
    sigs = sigs if sigs is not None else MT.signatures(shape)
    return MT.unmatched_area(sigs, transform_for(kind, origin, direction, copies), tolerance)


def auto_direction(shape: TopoDS_Shape, kind: str, origin, copies: int, tolerance: float,
                   progress: Progress = None, best: bool = False, sigs=None) -> tuple[G.Vec, str]:
    """The plane normal (or axis) the part is most symmetric about, and a note.

    With *best*, each candidate plane is also placed where it fits best
    (:func:`best_origin`) before it is scored."""
    sigs = sigs if sigs is not None else MT.signatures(shape)
    total = max(sum(s.area for s in sigs), 1e-9)
    scored = []
    cands = candidates(shape)
    for i, (direction, letter) in enumerate(cands):
        if progress:
            progress("Finding the best plane" if kind == MIRROR else "Finding the best axis", 0.1 * i / len(cands))
        point = best_origin(shape, kind, direction, copies, tolerance, sigs=sigs) if best else origin
        scored.append((asymmetry(shape, kind, point, direction, copies, tolerance, sigs), i, direction, letter))
    top = min(scored, key=lambda s: (s[0], s[1]))
    # An axis that does as well as a slightly better tilted one is the clearer answer.
    for s in scored:
        if s[3] and s[0] <= top[0] + 0.001 * total:
            top = s
            break
    _score, _i, direction, letter = top
    if kind == MIRROR:
        name = PLANES_BY_AXIS.get(letter)
        what = f"Auto chose {name}" if name else "Auto chose a tilted plane"
        note = f"{what}, the best fit of the part\u2019s main planes"
    else:
        note = f"Auto chose {letter}" if letter else "Auto chose a tilted axis"
        note += ", the best fit of the part\u2019s main axes"
    return direction, note


PLANES_BY_AXIS = {v: k for k, v in PLANES.items()}


def resolve(shape: TopoDS_Shape, spec: SymmetrySpec, progress: Progress = None,
            sigs=None) -> tuple[G.Vec, G.Vec, str]:
    """(origin, direction, note) for *spec* on *shape*."""
    sigs = sigs if sigs is not None else MT.signatures(shape)
    origin = through_point(shape, spec.through, spec.point)
    note = ""
    if spec.direction is None:
        direction, note = auto_direction(shape, spec.kind, origin, spec.copies, spec.tolerance, progress,
                                         best=spec.through == BEST, sigs=sigs)
    else:
        direction = G.vunit(spec.direction)
    if spec.through == BEST:
        origin = best_origin(shape, spec.kind, direction, spec.copies, spec.tolerance, progress, sigs)
    if spec.kind == MIRROR and spec.offset:
        origin = G.vadd(origin, G.vmul(direction, spec.offset))
    return origin, direction, note


def best_origin(shape: TopoDS_Shape, kind: str, direction, copies: int, tolerance: float,
                progress: Progress = None, sigs=None) -> G.Vec:
    """Where the plane (or axis) fits best.

    A mirror plane goes where the most face area maps onto matching faces:
    every pair of like faces whose centres differ only along the normal
    votes for the plane half way between them. (The centre of mass is
    pulled sideways by whatever isn't symmetric, so on its own it would
    make every symmetric feature look slightly off.) An axis goes through
    the bounding box centre or the centre of mass, whichever fits better.
    """
    sigs = sigs if sigs is not None else MT.signatures(shape)
    box_c, mass_c = G.bounding_box(shape).centre, G.centre(shape)
    cands = [box_c, mass_c]
    if kind == MIRROR:
        n = G.vunit(direction)
        voted = [G.vadd(box_c, G.vmul(n, p - G.vdot(box_c, n)))
                 for p in MT.vote_plane_offsets(sigs, n, tolerance)]
        cands = voted + cands
    scored = [(asymmetry(shape, kind, c, direction, copies, tolerance, sigs), i, c) for i, c in enumerate(cands)]
    best_score = min(s[0] for s in scored)
    # Among equals, the one nearest the bounding box centre (usually exactly it).
    ties = [s for s in scored if s[0] <= best_score + 1e-9]
    return min(ties, key=lambda s: (G.vlen(G.vsub(s[2], box_c)), s[1]))[2]


def check(shape: TopoDS_Shape, spec: SymmetrySpec, recognition=None, ignored: Sequence[str] = (),
          progress: Progress = None) -> SymmetryResult:
    """Check *shape* for the symmetry in *spec*. *recognition* (optional) names features."""
    tol = max(float(spec.tolerance), 1e-6)
    sigs = MT.signatures(shape)
    origin, direction, note = resolve(shape, spec, progress, sigs)
    trsf = transform_for(spec.kind, origin, direction, spec.copies)
    inverse = trsf.Inverted()
    if progress:
        progress("Comparing with the mirror image" if spec.kind == MIRROR else "Comparing with a turned copy", 0.2)
    moved = G.transformed(shape, trsf)
    back = {j: i for i, j in moved.face_map.items()}
    regions = _regions(shape, sigs, moved, trsf, tol)
    ff = FaceFeatures(shape, recognition)

    kept = []
    for i, r in enumerate(regions):
        if progress:
            progress("Measuring the differences", 0.6 + 0.3 * i / max(len(regions), 1))
        dev = G.max_distance(G.spread_points(r.shape), moved.shape, r.box.grown(10 * tol + 1e-3))
        if dev < tol:
            continue
        own = ff.key_faces(r.area_from(G.OWN))
        partner_areas: dict[int, float] = {}
        for j, a in r.area_from(G.TOOL).items():
            i0 = back.get(j)
            if i0:
                partner_areas[i0] = partner_areas.get(i0, 0.0) + a
        partner = ff.key_faces(partner_areas)
        kept.append((r, dev, own, partner))

    groups = _group(kept, ff)
    angle = 360.0 / max(int(spec.copies), 2)
    diffs = []
    for members in groups:
        diffs.append(_describe(members, ff, spec.kind, origin, direction, trsf, inverse, angle))
    diffs.sort(key=lambda d: -abs(d.volume))
    wanted = set(ignored)
    for n, d in enumerate(diffs, 1):
        d.number = n
        d.ignored = d.key in wanted
    if progress:
        progress("Done", 1.0)
    return SymmetryResult(spec.kind, origin, direction, int(spec.copies), tol, G.volume(shape), diffs, note)


def _regions(shape: TopoDS_Shape, sigs, moved: G.Moved, trsf, tol: float) -> list[G.Region]:
    """Material of *shape* that isn't in its moved copy: none at all when
    every face has a partner, else the boolean near the faces that don't."""
    _pairs, lost_image, lost_own = MT.match(sigs, sigs, trsf, tol)
    if not lost_own and not lost_image:
        return []
    tool_sigs = []
    for sig in sigs:
        m = MT.moved(sig, trsf)
        m.index = moved.face_map.get(sig.index, 0)
        m.box = G.bounding_box(G.face_map_of(moved.shape).FindKey(m.index), optimal=False) if m.index else m.box
        tool_sigs.append(m)
    lost_tool = [moved.face_map[i] for i in lost_image if i in moved.face_map]
    boxes = MT.local_boxes(shape, sigs, lost_own, moved.shape, tool_sigs, lost_tool, tol)
    if boxes is None:
        return G.difference(shape, moved.shape, tol, min_volume=tol ** 3)
    return G.local_difference(shape, moved.shape, boxes, tol, min_volume=tol ** 3)


def _group(kept, ff: FaceFeatures) -> list[list]:
    """Regions that share a face or a feature are one difference."""
    parent = list(range(len(kept)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def tags(item):
        _r, _dev, own, partner = item
        faces = set(own) | set(partner)
        out = {("f", f) for f in faces}
        out |= {("feature", ff.owner[f].id) for f in faces if f in ff.owner}
        return out

    seen: dict = {}
    for i, item in enumerate(kept):
        for tag in tags(item):
            if tag in seen:
                parent[find(i)] = find(seen[tag])
            else:
                seen[tag] = i
    groups: dict[int, list] = {}
    for i, item in enumerate(kept):
        groups.setdefault(find(i), []).append(item)
    return list(groups.values())


def _side(point, origin, normal) -> str:
    letter = axis_letter(normal)
    d = G.vdot(G.vsub(point, origin), normal)
    if letter:
        sign = 1.0 if G.vdot(normal, AXES[letter]) > 0 else -1.0
        return ("+" if d * sign >= 0 else "−") + letter
    return "front" if d >= 0 else "back"


def _describe(members, ff: FaceFeatures, kind, origin, direction, trsf, inverse, angle) -> Difference:
    own: dict[int, float] = {}
    partner: dict[int, float] = {}
    for _r, _dev, o, p in members:
        for f, a in o.items():
            own[f] = own.get(f, 0.0) + a
        for f, a in p.items():
            partner[f] = partner.get(f, 0.0) + a
    regions = [m[0] for m in members]
    vol = sum(r.volume for r in regions)
    dev = max(m[1] for m in members)
    box = _union_box([r.box for r in regions])
    region_centre = box.centre

    if own and not partner:
        dkind, faces = EXTRA, sorted(own)
    elif partner and not own:
        dkind, faces = CUT, sorted(partner)
    elif own and partner:
        dkind, faces = MISMATCH, sorted(set(own) | set(partner))
    else:
        dkind, faces = EXTRA, []

    feature = ff.main_feature(faces, {**partner, **own})
    if dkind == EXTRA:
        solids = [r.shape for r in regions]
        ghosts = [G.transformed(r.shape, inverse).shape for r in regions]
    else:
        solids = []
        ghosts = [r.shape for r in regions]
    # Around the regions, their ghosts and their partners' places (a pocket
    # with no partner lies at the mirror image of the region it leaves).
    partners = [G.bounding_box(G.transformed(r.shape, inverse).shape) for r in regions]
    box = _union_box([box] + [G.bounding_box(g) for g in ghosts] + partners)

    centre = ff.centre_of(faces) if faces else region_centre
    if feature is not None:
        name = feature_name(feature, ff)
    else:
        name = {EXTRA: "Material", CUT: "Recess", MISMATCH: "Feature"}[dkind]

    if dkind in (EXTRA, CUT):
        if kind == MIRROR:
            detail = f"only on the {_side(centre or region_centre, origin, direction)} side"
        else:
            detail = f"no matching copy {fmt_mm(angle)}° round"
    else:
        detail = _mismatch_detail(faces, ff, trsf, kind)

    anchor = ff.point_on(faces) if faces else None
    if anchor is None:
        anchor = G.sample_points(regions[0].shape)[0] if regions else region_centre
    c = centre or region_centre
    key = f"{dkind}:{name}:{c[0]:.1f},{c[1]:.1f},{c[2]:.1f}:{vol:.1f}"
    return Difference(0, dkind, name, detail, vol, dev, faces=list(faces), solids=solids, ghosts=ghosts,
                      anchor=anchor, box=box, key=key)


def _mismatch_detail(faces, ff: FaceFeatures, trsf, kind) -> str:
    found = []
    for f in faces:
        feature = ff.owner.get(f)
        if feature is not None and all(feature is not g for g in found):
            found.append(feature)
    if len(found) == 2:
        a, b = found
        ca, cb = ff.centre_of(a.faces), ff.centre_of(b.faces)
        if same_feature(a, b) and ca and cb:
            gap = min(G.vlen(G.vsub(G.apply(trsf, ca), cb)), G.vlen(G.vsub(G.apply(trsf, cb), ca)))
            return f"{fmt_mm(gap)} mm off its partner"
        return f"its partner is {feature_name(b, ff)}"
    return "doesn’t match its mirror image" if kind == MIRROR else "doesn’t match its next copy"


def _union_box(boxes) -> G.Box:
    boxes = list(boxes)
    lo = tuple(min(b.lo[i] for b in boxes) for i in range(3))
    hi = tuple(max(b.hi[i] for b in boxes) for i in range(3))
    return G.Box(lo, hi)  # type: ignore[arg-type]


def partner_shape(shape: TopoDS_Shape, result: SymmetryResult) -> TopoDS_Shape:
    """The mirrored (or turned) part that *result* compared *shape* with."""
    trsf = transform_for(result.kind, result.origin, result.direction, result.copies)
    return G.transformed(shape, trsf).shape
