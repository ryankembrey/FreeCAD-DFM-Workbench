# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Holes: simple, counterbored, countersunk, counterdrilled, stepped;
through or blind with a flat, drill-point or spherical bottom; threaded
when modeled threads (or the model's history) say so.

Hint (Vandenbrande & Requicha): an *internal* cylinder or cone, i.e. one
whose normal points at its axis. Coaxial internal faces joined directly or
by an annular shoulder form one hole, walked along the axis from the
opening. A cylinder cut away by another feature still counts if at least
``min_hole_span`` of it is left (an intersecting hole), but a part-cylinder
tangent to flat walls is a slot's or pocket's rounded end, not a hole.
"""

from __future__ import annotations

import math

from ..model import FeatureKind, Frame
from .._topology import vec as V
from .common import Candidate, Context, Score, axial_interval, deg, parallel, r3, vec3


def _teardrop(ctx: Context, f: int) -> list[int]:
    """The two flat faces of a teardrop hole's point: tangent to the
    cylinder, along its axis, and meeting each other in a valley."""
    info = ctx.info(f)
    if info.kind != "cylinder" or info.span >= 359.0 or info.span < 200.0:
        return []
    tangents = [a.other(f) for a in ctx.aag.arcs(f) if a.convexity == "smooth"
                and ctx.info(a.other(f)).kind == "plane"
                and abs(V.dot(ctx.info(a.other(f)).normal, info.axis)) < 1e-3]
    for i, p in enumerate(tangents):
        for q in tangents[i + 1:]:
            if any(a.other(p) == q and a.convexity == "concave" for a in ctx.aag.arcs(p)):
                return [p, q]
    return []


def _is_rounded_end(ctx: Context, f: int) -> bool:
    """A part-cylinder tangent to plane walls parallel to its axis: the
    rounded end of a slot or pocket (or obround cutout)."""
    info = ctx.info(f)
    if info.span >= 359.0 or _teardrop(ctx, f):
        return False
    for arc in ctx.aag.arcs(f):
        g = arc.other(f)
        gi = ctx.info(g)
        if arc.convexity == "smooth" and gi.kind == "plane" and \
                abs(V.dot(gi.normal, info.axis)) < math.sin(math.radians(ctx.options.parallel_angle)):
            return True
    return False


def _hole_faces(ctx: Context) -> list[int]:
    out = []
    for f in ctx.aag.faces:
        if f in ctx.owner:
            continue
        info = ctx.info(f)
        if info.kind not in ("cylinder", "cone") or not info.internal or info.axis is None:
            continue
        if info.kind == "cylinder" and _is_rounded_end(ctx, f):
            continue
        out.append(f)
    return out


def _perp_plane(ctx: Context, g: int, axis) -> bool:
    gi = ctx.info(g)
    return gi.kind == "plane" and parallel(gi.normal, axis, ctx.options.parallel_angle)


def _groups(ctx: Context, faces: list[int]) -> list[list[int]]:
    aag = ctx.aag
    parent = {f: f for f in faces}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    fs = set(faces)
    for f in faces:
        info = ctx.info(f)
        for g in aag.neighbors(f):
            if g in fs and aag.coaxial(f, g):
                parent[find(g)] = find(f)
            elif _perp_plane(ctx, g, info.axis) and g not in ctx.owner:
                # an annular shoulder joins coaxial faces on either side
                for h in aag.neighbors(g):
                    if h in fs and h != f and aag.coaxial(f, h):
                        parent[find(h)] = find(f)
    # Pieces of one drilled hole interrupted by another hole: coaxial, equal
    # radius, and the axis between them runs through empty space.
    cyl = [f for f in faces if ctx.info(f).kind == "cylinder"]
    for i, f in enumerate(cyl):
        for g in cyl[i + 1:]:
            if find(f) == find(g) or not aag.coaxial(f, g):
                continue
            fi, gi = ctx.info(f), ctx.info(g)
            if abs(fi.radius - gi.radius) > max(ctx.options.linear_tolerance * 10, 1e-6):
                continue
            a = axial_interval(fi, fi.origin, fi.axis)
            b = axial_interval(gi, fi.origin, fi.axis)
            if a[1] < b[0]:
                mid = (a[1] + b[0]) / 2.0
            elif b[1] < a[0]:
                mid = (b[1] + a[0]) / 2.0
            else:
                continue
            if _is_void(ctx, V.add(fi.origin, V.mul(fi.axis, mid))):
                parent[find(g)] = find(f)
    groups: dict[int, list[int]] = {}
    for f in faces:
        groups.setdefault(find(f), []).append(f)
    return [sorted(g) for g in groups.values()]


def _is_void(ctx: Context, point) -> bool:
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_OUT
    from OCP.gp import gp_Pnt

    cls = getattr(ctx, "_classifier", None)
    if cls is None:
        cls = BRepClass3d_SolidClassifier(ctx.topo.shape)
        ctx._classifier = cls
    cls.Perform(gp_Pnt(*point), 1e-6)
    return cls.State() == TopAbs_OUT


def _arc(ctx: Context, f: int, origin, axis):
    """(start, length) in degrees of the arc face *f* covers around *axis*,
    measured from one reference direction for the whole hole."""
    from .common import face_points

    ref = V.any_perpendicular(axis)
    ref2 = V.cross(axis, ref)

    def ang(p):
        r = V.radial(p, origin, axis)
        if V.norm(r) < 1e-9:
            return None
        return math.degrees(math.atan2(V.dot(r, ref2), V.dot(r, ref))) % 360.0

    info = ctx.info(f)
    angles = sorted({round(a, 4) for a in (ang(p) for p in face_points(ctx.topo, f)) if a is not None})
    if len(angles) < 2 or info.span >= 359.0:
        return 0.0, 360.0
    gaps = []
    for i, a in enumerate(angles):
        nxt = angles[(i + 1) % len(angles)] + (360.0 if i == len(angles) - 1 else 0.0)
        gaps.append((nxt - a, a))
    gap, start = max(gaps)
    ai = ang(info.sample_point) if info.sample_point else None
    if ai is not None and (ai - start) % 360.0 < gap:
        return start, gap           # the face is the big arc
    return (start + gap) % 360.0, 360.0 - gap


def _coverage(ctx: Context, members: list[int], radius: float, origin=None, axis=None) -> float:
    """Degrees of the circle the hole's cylinders cover between them: the
    union of their arcs, not the sum. Pieces of one half-cylinder on
    several walls (a saddle cut through ribs) still cover only half."""
    tol = max(ctx.options.linear_tolerance * 10, 1e-6)
    cyl = [m for m in members if ctx.info(m).kind == "cylinder" and abs(ctx.info(m).radius - radius) <= tol]
    if not cyl:
        return 0.0
    if origin is None or axis is None:
        return min(360.0, sum(ctx.info(m).span for m in cyl))
    bins = [False] * 360
    for m in cyl:
        start, length = _arc(ctx, m, origin, axis)
        if length >= 359.0:
            return 360.0
        for k in range(int(math.floor(start)), int(math.ceil(start + length))):
            bins[k % 360] = True
    return float(sum(bins))


def find_holes(ctx: Context) -> list[Candidate]:
    opt = ctx.options
    aag = ctx.aag
    out: list[Candidate] = []
    for members in _groups(ctx, _hole_faces(ctx)):
        cyls = [m for m in members if ctx.info(m).kind == "cylinder"]
        ref = ctx.info(cyls[0] if cyls else members[0])
        axis = ref.axis
        origin = ref.origin
        mset = set(members)

        # Faces around the group: shoulders, bottoms, rims
        shoulders, bottoms, rim_faces = set(), set(), set()
        rim_edges: list[tuple[int, int]] = []   # (edge, face across)
        bottom_kind = None
        for m in members:
            for arc in aag.arcs(m):
                g = arc.other(m)
                if g in mset:
                    continue
                gi = ctx.info(g)
                if arc.convexity in ("convex",) or (arc.virtual and arc.convexity == "convex"):
                    rim_faces.add(g)
                    if arc.edge:
                        rim_edges.append((arc.edge, g))
                    continue
                if _perp_plane(ctx, g, axis) and g not in ctx.owner:
                    others = aag.neighbors(g) - mset
                    if len(aag.neighbors(g) & mset) >= 2 or ctx.info(g).inner:
                        if not others:
                            shoulders.add(g)    # an annulus between two coaxial members
                    elif not others:
                        bottoms.add(g)          # a disc closing the hole
                        bottom_kind = "flat"
                elif gi.kind == "sphere" and gi.internal and g not in ctx.owner and \
                        V.point_line_distance(gi.origin, origin, axis) < max(1e-6, opt.linear_tolerance * 10):
                    if not (aag.neighbors(g) - mset):
                        bottoms.add(g)
                        bottom_kind = "spherical"
        # Hidden blends at the rim: rim arcs became virtual convex arcs (rounded entries)
        for m in members:
            for e in ctx.info(m).edges:
                if aag.edge_class(m, e) == "convex":
                    for g in aag.across(m, e):
                        if g not in mset and g not in bottoms and g not in shoulders:
                            rim_faces.add(g)
                            rim_edges.append((e, g))

        # Cones: countersink at an opening, drill point at the bottom, step between
        cones = [m for m in members if ctx.info(m).kind == "cone"]
        drill_cone = None
        for c in cones:
            ci = ctx.info(c)
            if ci.r_min < max(1e-3, 0.02 * ci.r_max):
                drill_cone = c
                bottom_kind = "drill_point"

        bores = [c for c in cones if c != drill_cone and ctx.info(c).semi_angle <= opt.max_bore_taper]
        if not cyls:
            if not cones:
                continue
            main_r = min(0.5 * (ctx.info(c).r_min + ctx.info(c).r_max) for c in bores) if bores else \
                max(ctx.info(c).r_max for c in cones)
        else:
            main_r = min(ctx.info(c).radius for c in cyls)
        if opt.max_hole_diameter > 0 and 2 * main_r > opt.max_hole_diameter:
            continue
        coverage = _coverage(ctx, cyls, main_r, origin, axis) if cyls else 360.0
        if coverage < opt.min_hole_span:
            continue

        # Axial layout
        intervals = {m: axial_interval(ctx.info(m), origin, axis) for m in members}
        lo = min(a for a, _ in intervals.values())
        hi = max(b for _, b in intervals.values())
        tol = max(1e-6, opt.linear_tolerance * 10, 1e-6 * ctx.topo.scale)

        def end_of(position: float) -> str:
            return "lo" if abs(position - lo) <= abs(position - hi) else "hi"

        open_ends = set()
        for e, g in rim_edges:
            edge = ctx.topo.edges[e]
            if edge is None or not edge.points:
                continue
            ps = [V.dot(V.sub(p, origin), axis) for p in edge.points]
            reach = tol + 1e-3 * (hi - lo)
            # An opening reaches the end of the hole (an inclined or curved
            # entry face makes the rim an ellipse or saddle spanning a range).
            if min(ps) <= lo + reach and max(ps) < hi - reach:
                open_ends.add("lo")
            elif max(ps) >= hi - reach and min(ps) > lo + reach:
                open_ends.add("hi")
            elif min(ps) <= lo + reach and max(ps) >= hi - reach:
                pos = sum(ps) / len(ps)
                open_ends.add(end_of(pos))
        bottom_end = None
        for b in bottoms:
            bi = ctx.info(b)
            pos = V.dot(V.sub(bi.centroid, origin), axis)
            bottom_end = end_of(pos)
        if drill_cone is not None:
            ci = ctx.info(drill_cone)
            a, b = intervals[drill_cone]
            bottom_end = "lo" if abs(a - lo) < abs(b - hi) else "hi"
        if bottom_end:
            open_ends.discard(bottom_end)
        if not open_ends:
            continue  # an enclosed void, not a hole
        through = len(open_ends) == 2 and bottom_end is None
        entry = "lo" if "lo" in open_ends else "hi"
        if through:
            # Enter from the wider end (counterbores / countersinks are at the entry)
            def radius_at(end):
                pos = lo if end == "lo" else hi
                best = 0.0
                for m in members:
                    a, b = intervals[m]
                    if abs((a if end == "lo" else b) - pos) <= tol + 1e-3 * (hi - lo):
                        best = max(best, ctx.info(m).r_max)
                return best
            r_lo, r_hi = radius_at("lo"), radius_at("hi")
            if abs(r_lo - r_hi) <= max(tol, 0.02 * max(r_lo, r_hi)):
                # Same both ends: enter from the part's main face (the
                # bigger face the hole opens onto).
                def rim_area(end):
                    out = 0.0
                    for e, g in rim_edges:
                        edge = ctx.topo.edges[e]
                        if edge is None or not edge.points:
                            continue
                        pos = sum(V.dot(V.sub(p, origin), axis) for p in edge.points) / len(edge.points)
                        if end_of(pos) == end:
                            out = max(out, ctx.info(g).area)
                    return out
                entry = "lo" if rim_area("lo") >= rim_area("hi") else "hi"
            else:
                entry = "lo" if r_lo >= r_hi else "hi"
        direction = axis if entry == "lo" else V.mul(axis, -1.0)
        start = lo if entry == "lo" else hi

        def depth_of(m: int) -> tuple[float, float]:
            a, b = intervals[m]
            if entry == "lo":
                return (a - lo, b - lo)
            return (hi - b, hi - a)

        segs = sorted(members, key=lambda m: depth_of(m))
        # Collapse into a profile of (kind, radius, d0, d1)
        profile = []
        taper = 0.0
        for m in segs:
            mi = ctx.info(m)
            d0, d1 = depth_of(m)
            bore = mi.kind == "cone" and m in bores
            if mi.kind == "cylinder" or bore:
                # a drafted bore counts as a cylinder of its mean radius
                radius = mi.radius if mi.kind == "cylinder" else 0.5 * (mi.r_min + mi.r_max)
                if bore:
                    taper = max(taper, mi.semi_angle)
                if profile and profile[-1][0] == "cyl" and abs(profile[-1][1] - radius) <= max(tol, 0.02 * radius):
                    k, r, a, b = profile[-1]
                    profile[-1] = (k, r, min(a, d0), max(b, d1))
                    continue
                profile.append(("cyl", radius, d0, d1))
            else:
                profile.append(("cone", mi.r_max, d0, d1, mi.r_min, mi.semi_angle, m))

        cyl_profile = [p for p in profile if p[0] == "cyl"]
        radii = []
        for p in cyl_profile:
            if not radii or abs(radii[-1] - p[1]) > max(tol, 0.02 * p[1]):
                radii.append(p[1])
        score = Score(0.95)
        params: dict = {}
        subtype = "simple"
        first = profile[0]
        if first[0] == "cone" and m_is_entry_cone(first, drill_cone):
            subtype = "countersunk"
            params["cs_diameter"] = r3(2 * first[1])
            params["cs_angle"] = deg(2 * first[5])
            score.ok(f"countersink {2 * first[1]:.3g} x {2 * first[5]:.0f}°")
        if len(radii) >= 2:
            # Steps: shoulder (counterbore) or cone (counterdrill) between them
            if subtype == "countersunk":
                subtype = "stepped"
            elif len(radii) == 2 and radii[0] > radii[1]:
                transition = [p for p in profile if p[0] == "cone" and p[6] != drill_cone]
                subtype = "counterdrilled" if transition else "counterbored"
                params["cb_diameter"] = r3(2 * radii[0])
                params["cb_depth"] = r3(cyl_profile[0][3])
                score.ok(f"{subtype[:-2]} {2 * radii[0]:.3g} deep {cyl_profile[0][3]:.3g}")
            elif len(radii) == 3 and radii[0] > radii[1] < radii[2] and through:
                # counterbored from both ends
                subtype = "counterbored"
                params["cb_diameter"] = r3(2 * radii[0])
                params["cb_depth"] = r3(cyl_profile[0][3])
                params["cb2_diameter"] = r3(2 * radii[2])
                params["cb2_depth"] = r3(cyl_profile[-1][3] - cyl_profile[-1][2])
                score.ok(f"counterbored at both ends: {2 * radii[0]:.3g} deep {cyl_profile[0][3]:.3g}, "
                         f"{2 * radii[2]:.3g} deep {params['cb2_depth']:.3g}")
            else:
                subtype = "stepped"
            params["step_diameters"] = tuple(r3(2 * r) for r in radii)

        main = (min(cyl_profile, key=lambda p: p[1]) if subtype == "counterbored" and "cb2_diameter" in params
                else cyl_profile[-1]) if cyl_profile else None
        if taper > 0.0:
            params["wall_taper_deg"] = r3(taper)
            score.ok(f"bore drafted {taper:.2g}°")
        diameter = 2 * (main[1] if main else main_r)
        if bottom_kind == "drill_point" and drill_cone is not None:
            dc = ctx.info(drill_cone)
            d0, d1 = depth_of(drill_cone)
            depth = d0
            # gp_Cone's semi-angle is between the axis and the wall: the
            # included tip angle is twice it (118° for a standard drill).
            params["bottom_angle"] = deg(2.0 * dc.semi_angle)
            params["total_depth"] = r3(d1)
        elif bottom_kind in ("flat", "spherical"):
            depth = hi - lo
            if bottom_kind == "spherical":
                bi = ctx.info(next(iter(bottoms)))
                params["total_depth"] = r3(hi - lo + bi.radius)
                params["bottom_radius"] = r3(bi.radius)
        else:
            depth = hi - lo
        params.update({
            "diameter": r3(diameter),
            "depth": r3(depth),
            "through": through,
            "bottom": "none" if through else (bottom_kind or "open"),
            "span_deg": deg(coverage),
            "axis": vec3(direction),
        })
        point = []
        for m in cyls:
            point = _teardrop(ctx, m)
            if point and not (set(point) & set(ctx.owner)):
                break
            point = []
        if point:
            a = math.degrees(math.acos(max(-1.0, min(1.0, -V.dot(ctx.info(point[0]).normal,
                                                                  ctx.info(point[1]).normal)))))
            params["profile"] = "teardrop"
            params["point_angle"] = round(180.0 - a, 2)
            params["partial"] = False
            score.ok(f"teardrop: a point of {180.0 - a:.0f}° so it prints without support")
            mset |= set(point)
        elif coverage < 359.0:
            score.penalize(0.6 + 0.4 * coverage / 360.0, f"only {coverage:.0f}° of the circle left (intersected)")
            params["partial"] = True
        else:
            params["partial"] = False
        score.ok(f"internal {'cylinder' if cyls else 'cone'} Ø{diameter:.3g}, "
                 f"{'through' if through else 'blind'}")
        if any(ctx.info(m).native_kind != ctx.info(m).kind for m in members):
            score.penalize(0.95, "surfaces fitted from B-splines")

        entry_point = V.add(origin, V.mul(axis, start))
        owned = sorted(mset | shoulders | bottoms)
        support = sorted((rim_faces - set(owned)))
        edges = sorted({e for e, _ in rim_edges})
        out.append(Candidate(
            kind=FeatureKind.HOLE, subtype=subtype, faces=owned, support=support, edges=edges,
            params=params, frame=Frame(vec3(entry_point), vec3(direction)), score=score,
            links={"axis": (entry_point, direction), "radius": diameter / 2.0, "members": members},
        ))
    return out


def m_is_entry_cone(seg, drill_cone) -> bool:
    """A cone at the opening that narrows into the hole."""
    return seg[0] == "cone" and seg[6] != drill_cone and seg[2] <= 1e-6 + 1e-3 * max(1.0, seg[3])
