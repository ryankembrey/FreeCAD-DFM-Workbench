# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""annotate_directional(): what a pull or print direction adds.

Recognition itself does not depend on any direction, so its result is
cached once per shape. This pass is cheap and runs per direction:

* draft of every face (signed, degrees: asin(n . pull)), and for each
  feature the draft of its walls;
* DRAFT features: connected faces deliberately drafted (between
  ``min_draft`` and ``max_draft`` off the pull);
* UNDERCUT features: connected faces trapped both ways along the pull
  (the same rule as the workbench's undercut analysis), with the feature
  that holds them (a side hole, a side boss) or the side action that
  releases them (after Fu, Fuh & Nee's undercut features);
* FDM annotations for a print direction: horizontal holes (teardrop),
  holes and walls too small for the nozzle;
* resin annotations for a print direction: recesses that cup (seal against
  the tank film or the build platform while they print, so each peel pulls
  a vacuum in them).
"""

from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.gp import gp_Dir

from ..utils.geometry import FaceIndex
from .model import DirectionalReport, Feature, FeatureKind, RecognitionResult
from .options import RecognitionOptions
from ._topology import vec as V

_WALLED = (FeatureKind.HOLE, FeatureKind.BOSS, FeatureKind.RIB, FeatureKind.SLOT, FeatureKind.POCKET,
           FeatureKind.CUTOUT, FeatureKind.STEP, FeatureKind.PROTRUSION, FeatureKind.DEPRESSION)


def _dir(d) -> Optional[V.Vec]:
    if d is None:
        return None
    if isinstance(d, gp_Dir):
        return (d.X(), d.Y(), d.Z())
    return V.unit(tuple(float(c) for c in d))


def _adjacency(shape: TopoDS_Shape, faces: FaceIndex) -> dict[int, set[int]]:
    anc = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, anc)
    adj: dict[int, set[int]] = {i: set() for i in range(1, len(faces) + 1)}
    for i in range(1, anc.Extent() + 1):
        owners = [faces.index_of(TopoDS.Face_s(f)) for f in anc.FindFromIndex(i)]
        owners = [o for o in owners if o]
        for a in owners:
            for b in owners:
                if a != b:
                    adj[a].add(b)
    return adj


def _components(faces: Iterable[int], adj: dict[int, set[int]]) -> list[list[int]]:
    pool = set(faces)
    out = []
    while pool:
        s = min(pool)
        pool.discard(s)
        comp, stack = [s], [s]
        while stack:
            f = stack.pop()
            for g in adj.get(f, ()):
                if g in pool:
                    pool.discard(g)
                    comp.append(g)
                    stack.append(g)
        out.append(sorted(comp))
    return out


def _samples(face, n: int):
    """(point offset off the face, normal) on an n x n grid inside the face."""
    from ..utils.geometry import get_face_uv_normal, get_point_from_uv, yield_face_uv_grid

    out = []
    for u, v in yield_face_uv_grid(face, max(1, n), 0.05):
        nrm = get_face_uv_normal(face, u, v)
        if nrm is None:
            continue
        out.append((get_point_from_uv(face, nrm, u, v, 1e-3), nrm))
    if not out:
        from ..utils.geometry import get_face_uv_center
        u, v = get_face_uv_center(face)
        nrm = get_face_uv_normal(face, u, v)
        if nrm is not None:
            out.append((get_point_from_uv(face, nrm, u, v, 1e-3), nrm))
    return out


def annotate_directional(
    result: RecognitionResult,
    shape: TopoDS_Shape,
    pull=None,
    side_actions: Sequence = (),
    print_direction=None,
    nozzle_diameter: float = 0.4,
    options: Optional[RecognitionOptions] = None,
) -> DirectionalReport:
    """Draft, undercut and FDM annotations of *result* for a direction.

    *pull*, *print_direction* and *side_actions* take gp_Dir or (x, y, z).
    """
    from ..utils.mold import ray_is_clear

    options = options or RecognitionOptions.from_dict(result.options)
    d = _dir(pull)
    p = _dir(print_direction)
    sides = [_dir(s) for s in side_actions or ()]
    faces = FaceIndex(shape)
    if len(faces) != result.face_count:
        raise ValueError("The shape is not the one the result was recognised from.")
    adj = _adjacency(shape, faces)
    report = DirectionalReport(direction=d, print_direction=p)
    next_id = max([f.id for f in result.features] or [0]) + 1
    new_features: list[Feature] = []
    owner = {face: f for f in result.features for face in f.faces}

    if d is not None:
        from ._topology.descriptors import _interior_sample
        normals: dict[int, V.Vec] = {}
        for i in range(1, len(faces) + 1):
            _pt, n = _interior_sample(faces.face_at(i))
            if n is not None:
                normals[i] = n
                report.face_draft[i] = round(math.degrees(math.asin(max(-1.0, min(1.0, V.dot(n, d))))), 4)

        # Feature walls
        for f in result.features:
            if f.kind not in _WALLED:
                continue
            walls = [report.face_draft[i] for i in f.faces if i in report.face_draft
                     and abs(report.face_draft[i]) < options.max_draft + 1e-9]
            ann = report.annotations.setdefault(f.id, {})
            if walls:
                mags = [abs(w) for w in walls]
                ann["draft_min_deg"] = round(min(mags), 3)
                ann["draft_max_deg"] = round(max(mags), 3)
                ann["needs_draft"] = min(mags) < options.min_draft
            axis = f.params.get("axis") if isinstance(f.params.get("axis"), tuple) else (f.frame.axis if f.frame else None)
            if axis and f.kind in (FeatureKind.HOLE, FeatureKind.BOSS):
                a = V.line_angle(V.unit(axis), d)
                ann["axis_angle_to_pull_deg"] = round(a, 3)
                ann["along_pull"] = a <= options.parallel_angle

        # Drafted walls
        drafted = [i for i, a in report.face_draft.items() if options.min_draft <= abs(a) <= options.max_draft]
        for comp in _components(drafted, adj):
            angles = [report.face_draft[i] for i in comp]
            sub = "positive" if all(a > 0 for a in angles) else "negative" if all(a < 0 for a in angles) else "mixed"
            new_features.append(Feature(
                id=next_id, kind=FeatureKind.DRAFT, subtype=sub, faces=tuple(comp),
                params={"draft_min_deg": round(min(abs(a) for a in angles), 3),
                        "draft_max_deg": round(max(abs(a) for a in angles), 3),
                        "direction": tuple(round(c, 6) for c in d)},
                confidence=1.0, evidence=(f"+ {len(comp)} face(s) drafted "
                                          f"{min(abs(a) for a in angles):.2f}..{max(abs(a) for a in angles):.2f}°",),
            ))
            next_id += 1

        # Undercuts
        inter = IntCurvesFace_ShapeIntersector()
        inter.Load(shape, 1e-6)
        dp, dm = gp_Dir(*d), gp_Dir(*V.mul(d, -1.0))
        trapped_faces = {}
        for i in range(1, len(faces) + 1):
            pts = _samples(faces.face_at(i), options.undercut_samples)
            if not pts:
                continue
            trapped = [(pt, n) for pt, n in pts
                       if not ray_is_clear(inter, pt, dp, 0.05) and not ray_is_clear(inter, pt, dm, 0.05)]
            if len(trapped) * 2 >= len(pts) and trapped:
                trapped_faces[i] = trapped
        for comp in _components(trapped_faces, adj):
            owners = {owner[i].id for i in comp if i in owner}
            sub, params = "unreleased", {}
            if len(owners) == 1:
                hold = next(iter(owners))
                params["feature"] = hold
                params["feature_kind"] = next(f.kind.value for f in result.features if f.id == hold)
                sub = "feature"
            released = 0
            for k, sd in enumerate(sides, start=1):
                if sd is None:
                    continue
                g = gp_Dir(*sd)
                ok = sum(1 for i in comp for pt, n in trapped_faces[i]
                         if n.Dot(g) > -0.0175 and ray_is_clear(inter, pt, g, 0.05))
                total = sum(len(trapped_faces[i]) for i in comp)
                if ok * 2 >= total:
                    released = k
                    break
            if released:
                params["side_action"] = released
                sub = "slide" if sub == "unreleased" else sub
            params["released"] = bool(released)
            new_features.append(Feature(
                id=next_id, kind=FeatureKind.UNDERCUT, subtype=sub, faces=tuple(comp), params=params,
                confidence=0.9, evidence=(f"+ {len(comp)} face(s) blocked both ways along the pull",)
                + ((f"+ released by side action {released}",) if released else ()),
            ))
            next_id += 1

    if p is not None:
        nz = max(1e-6, nozzle_diameter)
        for f in result.features:
            ann = report.annotations.setdefault(f.id, {})
            if f.kind == FeatureKind.HOLE:
                axis = f.params.get("axis")
                if axis:
                    a = V.line_angle(V.unit(axis), p)
                    ann["print_axis_angle_deg"] = round(a, 3)
                    ann["horizontal"] = a >= 60.0
                    dia = float(f.params.get("diameter", 0.0))
                    ann["teardrop_recommended"] = bool(ann["horizontal"] and dia >= options.fdm_teardrop_diameter)
                    ann["below_min_printable"] = dia < options.fdm_min_hole_nozzles * nz
            elif f.kind == FeatureKind.RIB:
                t = float(f.params.get("thickness", 0.0))
                ann["thin_for_nozzle"] = t < options.fdm_min_wall_nozzles * nz
                ann["perimeters"] = round(t / nz, 2)
            elif f.kind == FeatureKind.BOSS:
                od = float(f.params.get("outer_diameter", 0.0))
                ann["fragile_pin"] = od < 2 * options.fdm_min_wall_nozzles * nz
                if "wall_thickness" in f.params:
                    ann["thin_wall_for_nozzle"] = float(f.params["wall_thickness"]) < options.fdm_min_wall_nozzles * nz
            if not ann:
                report.annotations.pop(f.id, None)
        next_id = _supports(result, shape, faces, adj, p, options, owner, report, new_features, next_id)
        _cups(result, shape, faces, p, report)

    report.features = tuple(new_features)
    report.annotations = {k: v for k, v in report.annotations.items() if v}
    return report


def _supports(result, shape, faces, adj, p, options, owner, report, new_features, next_id) -> int:
    """Printing: faces that need support, along the print direction *p*.

    * OVERHANG: connected faces facing down more steeply than
      ``fdm_overhang_angle`` from vertical, not on the bed and not in a
      hole (holes get their teardrop advice instead).
    * BRIDGE: a flat face looking straight down, held up at two opposite
      sides by walls standing below it. Its span is the gap between them;
      longer than ``fdm_max_bridge`` is flagged.
    * A chamfer or round at the bed: relief against elephant's foot.
    """
    from OCP.BRepBndLib import BRepBndLib
    from OCP.Bnd import Bnd_Box
    from ._topology.descriptors import _interior_sample

    def level_range(face):
        from OCP.BRep import BRep_Tool
        from OCP.TopAbs import TopAbs_VERTEX
        from OCP.TopTools import TopTools_IndexedMapOfShape

        pts = []
        vmap = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(face, TopAbs_VERTEX, vmap)
        for i in range(1, vmap.Extent() + 1):
            q = BRep_Tool.Pnt_s(TopoDS.Vertex_s(vmap.FindKey(i)))
            pts.append(q.X() * p[0] + q.Y() * p[1] + q.Z() * p[2])
        if not pts:
            box = Bnd_Box()
            BRepBndLib.Add_s(face, box)
            x0, y0, z0, x1, y1, z1 = box.Get()
            corners = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
            pts = [V.dot(c, p) for c in corners]
        return min(pts), max(pts)

    levels = {i: level_range(faces.face_at(i)) for i in range(1, len(faces) + 1)}
    bed = min(lo for lo, _hi in levels.values())
    tol = 1e-3
    limit = math.sin(math.radians(options.fdm_overhang_angle))
    down: dict[int, float] = {}
    for i in range(1, len(faces) + 1):
        _pt, n = _interior_sample(faces.face_at(i))
        if n is None:
            continue
        c = -V.dot(n, p)                      # 1 = straight down
        lo, hi = levels[i]
        if hi <= bed + tol:
            continue                          # on the bed
        f = owner.get(i)
        if f is not None and f.kind == FeatureKind.HOLE:
            continue
        if c > limit + 1e-9:
            down[i] = c
    for comp in _components(down, adj):
        flat = [i for i in comp if down[i] > 1.0 - 1e-6]
        bridge = None
        if len(comp) == 1 and flat:
            bridge = _bridge(faces, adj, comp[0], p, levels)
        lo = min(levels[i][0] for i in comp)
        angle = math.degrees(math.asin(min(1.0, max(down[i] for i in comp))))
        if bridge is not None:
            span, sides = bridge
            params = {"span": round(span, 4), "height_above_bed": round(lo - bed, 4),
                      "too_long": span > options.fdm_max_bridge, "supported_sides": sides}
            new_features.append(Feature(
                id=next_id, kind=FeatureKind.BRIDGE, subtype="flat", faces=tuple(comp), params=params,
                confidence=0.9, evidence=(f"+ a flat face looking down, held at {sides} sides, spanning {span:.3g}",)))
        else:
            params = {"max_angle_from_horizontal_deg": round(90.0 - angle, 3), "height_above_bed": round(lo - bed, 4),
                      "face_count": len(comp)}
            new_features.append(Feature(
                id=next_id, kind=FeatureKind.OVERHANG, subtype="flat" if len(flat) == len(comp) else "sloped",
                faces=tuple(comp), params=params, confidence=0.9,
                evidence=(f"+ {len(comp)} face(s) facing down more than {options.fdm_overhang_angle:g}° "
                          "from vertical, off the bed",)))
        next_id += 1
    # Bed edges: a chamfer or round along the bottom relieves elephant's foot
    for f in result.features:
        if f.kind in (FeatureKind.CHAMFER, FeatureKind.FILLET) and \
                any(levels[i][0] <= bed + tol for i in f.faces if i in levels):
            report.annotations.setdefault(f.id, {})["bed_edge_relief"] = True
    return next_id


def _bridge(faces, adj, face, p, levels):
    """(span, number of supported sides) when *face* (flat, looking down)
    rests on walls at two opposite sides; else None."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    lvl = levels[face][0]
    supports = []
    for g in adj.get(face, ()):
        # a wall standing below the face holds it up
        if levels[g][0] < lvl - 1e-3 and levels[g][1] <= lvl + 1e-3:
            supports.append(g)
    if len(supports) < 2:
        return None
    # span: the largest distance across the face between support walls' contact lines
    mids = []
    for g in supports:
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(faces.face_at(g), props)
        c = props.CentreOfMass()
        mids.append((c.X(), c.Y(), c.Z()))
    best = 0.0
    for i, a in enumerate(mids):
        for b in mids[i + 1:]:
            d = V.sub(b, a)
            d = V.sub(d, V.mul(p, V.dot(d, p)))
            best = max(best, V.norm(d))
    if best <= 0:
        return None
    return best, len(supports)


#: A recess opening within this angle of the print direction faces the tank (degrees).
CUP_ANGLE = 30.0
#: Openings within this distance of the lowest point of the part are on the platform (mm).
CUP_BED_TOLERANCE = 0.05
_RECESSES = (FeatureKind.HOLE, FeatureKind.POCKET, FeatureKind.CUTOUT)


def _cups(result, shape, faces, p, report) -> None:
    """Resin printing: recesses that cup, annotated with ``cup`` (where they
    seal: "tank" or "platform") and ``cup_area`` (their opening, mm²).

    Upside-down resin printers cure each layer against the tank's film and
    then peel it off. A recess open towards the film is sealed by it at every
    layer: the peel pulls a vacuum in it ("cupping"; Formlabs). So is one
    open towards the build platform when it is printed on the platform, as
    is a hole running through the part there. A recess that meets an
    opening through the part (a vent hole through a pocket's floor) lets
    air in and doesn't cup. Sealed hollows are the Sealed Cavity rule's.
    """
    from ..utils.geometry import face_area
    from ._topology.descriptors import _interior_sample

    cos_cup = math.cos(math.radians(CUP_ANGLE))
    level = {}  # face -> (lowest, highest) along p

    def extent(i):
        if i not in level:
            from ..utils.geometry import get_extent_along

            level[i] = get_extent_along(faces.face_at(i), gp_Dir(*p))
        return level[i]

    bed = min(extent(i)[0] for i in range(1, len(faces) + 1))
    by_id = {f.id: f for f in result.features}

    def on_bed(lowest: float) -> bool:
        return abs(lowest - bed) <= CUP_BED_TOLERANCE

    def vented(f) -> bool:
        """Air gets in: its floor is pierced, or it meets an opening through the part."""
        if f.params.get("pierced"):
            return True
        for other in (by_id.get(i) for i in f.interacts_with):
            if other is None:
                continue
            if other.kind == FeatureKind.CUTOUT or (other.kind in (FeatureKind.HOLE, FeatureKind.SLOT)
                                                    and other.params.get("through")):
                return True
        return False

    for f in result.features:
        if f.kind not in _RECESSES or f.params.get("partial") or vented(f):
            continue
        where, area = None, 0.0
        if f.kind == FeatureKind.HOLE:
            axis, d = f.params.get("axis"), float(f.params.get("diameter") or 0.0)
            if not axis or d <= 0.0:
                continue
            along = V.dot(V.unit(axis), p)  # the axis points into the hole from its entry
            low = min(extent(i)[0] for i in f.faces)
            area = math.pi * d * d / 4.0
            if f.params.get("through"):
                if abs(along) >= cos_cup and on_bed(low):
                    where = "platform"
            elif -along >= cos_cup:
                where = "tank"
            elif along >= cos_cup and on_bed(low):
                where = "platform"
        else:
            sampled = [(i, _interior_sample(faces.face_at(i))[1]) for i in f.faces]
            normals = {i: n for i, n in sampled if n is not None}
            if f.kind == FeatureKind.CUTOUT:
                # Open at both ends: it cups only standing on the platform, its
                # walls along the print direction.
                if not normals or any(abs(V.dot(n, p)) > 1.0 - cos_cup for n in normals.values()):
                    continue
                if not on_bed(min(extent(i)[0] for i in f.faces)):
                    continue
                where = "platform"
                area = float(f.params.get("floor_area") or 0.0) or _cross_section(faces, f.faces, p)
            else:
                # A pocket opens the way its floor faces (the floor's normal
                # points out of the material, into the pocket).
                floors = [i for i, n in normals.items() if abs(V.dot(n, p)) >= cos_cup]
                if not floors:
                    continue
                facing = sum(V.dot(normals[i], p) * face_area(faces.face_at(i)) for i in floors)
                area = abs(facing)
                if facing > 0:
                    where = "tank"
                elif on_bed(min(extent(i)[0] for i in f.faces)):
                    where = "platform"
        if where and area > 0.0:
            ann = report.annotations.setdefault(f.id, {})
            ann["cup"] = where
            ann["cup_area"] = round(area, 3)


def _cross_section(faces, members, p) -> float:
    """The area a cutout's walls enclose across *p*, from their outline's
    bounding rectangle across p (an estimate, generous for round shapes)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    a = V.unit(V.cross(p, (1.0, 0.0, 0.0) if abs(p[0]) < 0.9 else (0.0, 1.0, 0.0)))
    b = V.cross(p, a)
    pts = []
    for i in members:
        box = Bnd_Box()
        BRepBndLib.Add_s(faces.face_at(i), box)
        x0, y0, z0, x1, y1, z1 = box.Get()
        pts += [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    if not pts:
        return 0.0
    span = [max(V.dot(q, u) for q in pts) - min(V.dot(q, u) for q in pts) for u in (a, b)]
    return span[0] * span[1]
