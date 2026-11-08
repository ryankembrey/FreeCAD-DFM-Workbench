# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Bosses: solid, screw (with a coaxial hole), stepped, tapered; columns
(a cylinder between two walls).

Hint (after Wang & Lai's tube recognition): an *external* cylinder or
cone whose one end meets a base face concavely (directly, or through a
hidden root fillet) and whose other end is capped convexly. Both ends
concave is a column; both convex is a bar or shaft, not a boss.
"""

from __future__ import annotations

import math

from ..model import FeatureKind, Frame
from .._topology import vec as V
from .common import extent, face_points, Candidate, Context, Score, axial_interval, deg, parallel, r3, vec3


def _boss_faces(ctx: Context) -> list[int]:
    opt = ctx.options
    out = []
    for f in ctx.aag.faces:
        if f in ctx.owner:
            continue
        i = ctx.info(f)
        if i.kind not in ("cylinder", "cone") or i.internal is not False or i.axis is None:
            continue
        if i.kind == "cone" and i.semi_angle > opt.max_boss_taper:
            continue
        if i.span < opt.min_hole_span:
            continue
        if opt.max_boss_diameter > 0 and 2 * i.r_max > opt.max_boss_diameter:
            continue
        out.append(f)
    return out


def find_bosses(ctx: Context) -> list[Candidate]:
    opt = ctx.options
    aag = ctx.aag
    faces = _boss_faces(ctx)
    fs = set(faces)
    parent = {f: f for f in faces}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for f in faces:
        for g in aag.neighbors(f):
            if g in fs and aag.coaxial(f, g):
                parent[find(g)] = find(f)
            elif g not in fs and ctx.info(g).kind == "plane" and \
                    parallel(ctx.info(g).normal, ctx.info(f).axis, opt.parallel_angle):
                for h in aag.neighbors(g):
                    if h in fs and h != f and aag.coaxial(f, h):
                        parent[find(h)] = find(f)
    groups: dict[int, list[int]] = {}
    for f in faces:
        groups.setdefault(find(f), []).append(f)

    out = []
    for members in groups.values():
        members.sort()
        if len(members) == 1:
            cand = _boss(ctx, members)
        else:
            # A stack that isn't a boss as a whole may hold one: a flange's
            # rim and hub are coaxial, but only the hub stands on a base.
            ref = ctx.info(members[0])
            order = sorted(members, key=lambda m: axial_interval(ctx.info(m), ref.origin, ref.axis)[0])
            cand = None
            for size in range(len(order), 0, -1):
                for start in range(0, len(order) - size + 1):
                    cand = _boss(ctx, sorted(order[start:start + size]))
                    if cand is not None:
                        break
                if cand is not None:
                    break
        if cand is not None:
            out.append(cand)
    return out


def _boss(ctx: Context, members: list[int]):
    opt = ctx.options
    aag = ctx.aag
    tol = max(1e-6, opt.linear_tolerance * 10, 1e-6 * ctx.topo.scale)
    mset = set(members)
    ref = ctx.info(members[0])
    axis, origin = ref.axis, ref.origin
    iv = {m: axial_interval(ctx.info(m), origin, axis) for m in members}
    lo = min(a for a, _ in iv.values())
    hi = max(b for _, b in iv.values())
    if hi - lo <= tol:
        return None

    ends: dict[str, dict] = {"lo": {"concave": set(), "convex": set()},
                             "hi": {"concave": set(), "convex": set()}}
    shoulders = set()
    # a shoulder is a ring within the boss's footprint, not the plate the
    # boss's split halves both stand in
    r_out = max(ctx.info(m).r_max for m in members)
    root_blends = []
    for m in members:
        for e in ctx.info(m).edges:
            edge = ctx.topo.edges[e]
            if edge is None or not edge.is_manifold or not edge.points:
                continue
            cls = aag.edge_class(m, e)
            if cls not in ("concave", "convex"):
                continue
            ps = [V.dot(V.sub(p, origin), axis) for p in edge.points]
            pos = sum(ps) / len(ps)
            if max(ps) - min(ps) > 0.25 * (hi - lo) + tol:
                continue  # runs along the boss, not round an end
            near = 0.1 * (hi - lo) + tol
            if abs(pos - lo) <= near:
                end = "lo"
            elif abs(pos - hi) <= near:
                end = "hi"
            else:
                continue  # something meets the boss part-way up (a gusset's top)
            for g in aag.across(m, e):
                if g in mset:
                    continue
                gi = ctx.info(g)
                if gi.kind == "plane" and parallel(gi.normal, axis, opt.parallel_angle) and \
                        len(aag.neighbors(g) & mset) >= 2 and gi.area <= 1.2 * math.pi * r_out ** 2:
                    shoulders.add(g)
                    continue
                ends[end][cls].add(g)
                other = edge.other(m)
                if aag.is_hidden(other) and cls == "concave":
                    root_blends.append(other)

    lo_base = bool(ends["lo"]["concave"]) and not ends["lo"]["convex"]
    hi_base = bool(ends["hi"]["concave"]) and not ends["hi"]["convex"]
    lo_cap = bool(ends["lo"]["convex"]) and not ends["lo"]["concave"]
    hi_cap = bool(ends["hi"]["convex"]) and not ends["hi"]["concave"]
    if lo_base and hi_base:
        subtype, base_end = "column", "lo"
    elif lo_base and hi_cap:
        subtype, base_end = "solid", "lo"
    elif hi_base and lo_cap:
        subtype, base_end = "solid", "hi"
    else:
        return None
    # Standing on a narrow coaxial ring is a groove's floor or a shaft's step,
    # not a boss on a base.
    r_boss = max(ctx.info(m).r_max for m in members)
    narrow = 0
    for b in ends[base_end]["concave"]:
        bi = ctx.info(b)
        circles = [ctx.topo.edges[e] for e in bi.outer]
        if circles and all(c is not None and c.curve == "circle" and c.center is not None and
                           V.point_line_distance(c.center, origin, axis) <= tol for c in circles):
            if max(c.circle_radius for c in circles) <= 1.5 * r_boss:
                narrow += 1
    if narrow and narrow == len(ends[base_end]["concave"]):
        return None
    top_end = "hi" if base_end == "lo" else "lo"
    direction = axis if base_end == "lo" else V.mul(axis, -1.0)
    base_pos = lo if base_end == "lo" else hi
    bases = sorted(ends[base_end]["concave"])
    caps = sorted(ends[top_end]["convex"]) if subtype != "column" else []

    # A cap is owned when it is closed by the boss (planar disc or annulus)
    owned_caps = []
    for c in caps:
        ci = ctx.info(c)
        if c in ctx.owner:
            continue
        if ci.kind in ("plane", "sphere", "cone", "torus"):
            outer_faces = set()
            tied = set()
            for e in ci.outer:
                across = aag.across(c, e)
                if aag.edge_class(c, e) == "concave":
                    tied |= across          # a wall the boss is merged into
                else:
                    outer_faces |= across
            walls_ok = all(ctx.info(w).kind == "plane" and abs(V.dot(ctx.info(w).normal, axis)) < 0.1
                           for w in tied)
            if outer_faces and outer_faces <= mset and walls_ok:
                owned_caps.append(c)

    cyl_members = [m for m in members if ctx.info(m).kind == "cylinder"]
    base_member = min(members, key=lambda m: abs((iv[m][0] if base_end == "lo" else iv[m][1]) - base_pos))
    bi = ctx.info(base_member)
    od = 2 * (bi.r_max if bi.kind == "cone" else bi.radius)
    if bi.kind == "cone":
        # extrapolate the wall to the base plane (a root fillet ends it above)
        a, b = iv[base_member]
        slope = (bi.r_min - bi.r_max) / max(tol, b - a)
        b0 = ctx.info(bases[0])
        if b0.kind == "plane" and parallel(b0.normal, axis, opt.parallel_angle):
            base_plane = V.dot(V.sub(b0.origin, origin), axis)
            gap = abs((a if base_end == "lo" else b) - base_plane)
            od = 2 * (bi.r_max + gap * abs(slope))
    height = hi - lo
    top_pos = hi if base_end == "lo" else lo
    for c in owned_caps:
        ci = ctx.info(c)
        if ci.kind == "plane" and parallel(ci.normal, axis, opt.parallel_angle):
            top_pos = V.dot(V.sub(ci.origin, origin), axis)  # past a hidden top round
    height = abs(top_pos - (lo if base_end == "lo" else hi))
    b0 = ctx.info(bases[0])
    if b0.kind == "plane" and parallel(b0.normal, axis, opt.parallel_angle):
        # from the base face, not the top of a root fillet
        base_pos = V.dot(V.sub(b0.origin, origin), axis)
        height = abs(top_pos - base_pos)
    taper = max([ctx.info(m).semi_angle for m in members if ctx.info(m).kind == "cone"] or [0.0])
    score = Score(0.92)
    score.ok(f"external {'cylinder' if cyl_members else 'cone'} Ø{od:.3g} standing on "
             f"{len(bases)} base face(s)")
    if owned_caps:
        score.ok("closed by a top face")
    elif subtype != "column":
        score.penalize(0.9, "top not closed by its own face")
    if taper > 0:
        score.ok(f"tapered {taper:.2f}°")
    params = {
        "outer_diameter": r3(od),
        "height": r3(height),
        "taper_deg": deg(taper),
        "axis": vec3(direction),
        "base_faces": tuple(bases),
    }
    radii = sorted({round(ctx.info(m).radius, 6) for m in cyl_members}, reverse=True)
    if len(radii) > 1:
        subtype = "stepped" if subtype == "solid" else subtype
        params["step_diameters"] = tuple(r3(2 * r) for r in radii)
    if root_blends:
        rb = [aag.hidden[b].radius for b in root_blends if b in aag.hidden]
        if rb:
            params["root_fillet_radius"] = r3(min(rb))
    if any(ctx.info(m).native_kind != ctx.info(m).kind for m in members):
        score.penalize(0.95, "surfaces fitted from B-splines")
    # A slender solid boss is a pin: fragile to print, hard to fill and eject.
    if subtype == "solid" and od > 0 and height >= opt.min_pin_slenderness * od * (1.0 - 1e-3):
        subtype = "pin"
        score.ok(f"slender: {height / od:.2g} diameters tall")
    params["slenderness"] = r3(height / od) if od > 0 else 0.0
    # Tied to a wall: a side face it meets in a valley, running along its axis.
    walls = set()
    for m in members:
        for arc in aag.arcs(m):
            g = arc.other(m)
            gi = ctx.info(g)
            if arc.convexity == "concave" and g not in bases and gi.kind == "plane" and \
                    abs(V.dot(gi.normal, axis)) < 0.1:
                across = V.unit(V.cross(gi.normal, axis))
                lo_w, hi_w = extent(face_points(ctx.topo, g), across)
                if hi_w - lo_w >= 2.0 * od:     # a wall runs on past the boss; a gusset stops
                    walls.add(g)
    if walls:
        params["wall_faces"] = tuple(sorted(walls))
        score.ok(f"tied into {len(walls)} wall face(s)")
    base_point = V.add(origin, V.mul(axis, base_pos))
    return Candidate(
        kind=FeatureKind.BOSS, subtype=subtype,
        faces=sorted(mset | set(owned_caps) | shoulders),
        support=bases + [c for c in caps if c not in owned_caps],
        edges=[], params=params, frame=Frame(vec3(base_point), vec3(direction)), score=score,
        links={"axis": (base_point, direction), "radius": od / 2.0, "caps": owned_caps,
               "top_pos": hi if base_end == "lo" else lo},
    )
