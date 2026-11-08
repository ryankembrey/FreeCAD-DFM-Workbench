# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Slots and steps.

Slot hint (Vandenbrande & Requicha): two planar walls facing *each other*,
both meeting a common face concavely (a floor or a closed end) and both
opening convexly onto a rim face. Ends are open, closed by a wall, or
rounded (a part-cylinder of the slot's half width). A slot closed at both
ends by flat walls is left to the pocket recognizer.

Step: a floor and a wall meeting concavely, each meeting nothing else
concavely, open on the other sides.
"""

from __future__ import annotations

from itertools import combinations

from ..model import FeatureKind, Frame
from .._topology import vec as V
from .common import Candidate, Context, Score, extent, face_points, perpendicular, r3, shared_edges, vec3
from .depressions import rim_along


def _neighbors(ctx: Context, f: int, *conv: str) -> set[int]:
    return {arc.other(f) for arc in ctx.aag.arcs(f) if arc.convexity in conv}


def find_slots(ctx: Context) -> list[Candidate]:
    opt = ctx.options
    aag = ctx.aag
    tol = max(opt.linear_tolerance * 10, 1e-6)
    planes = [f for f in aag.faces if f not in ctx.owner and ctx.info(f).kind == "plane"]
    seen = set()
    out = []
    by_concave: dict[int, set[int]] = {f: _neighbors(ctx, f, "concave", "smooth") for f in aag.faces}
    for x in aag.faces:
        walls = [w for w in by_concave[x] if w in planes]
        for a, b in combinations(sorted(walls), 2):
            if (a, b) in seen:
                continue
            na, nb = ctx.info(a).normal, ctx.info(b).normal
            if V.angle(na, V.mul(nb, -1.0)) > opt.parallel_angle + 2 * opt.max_rib_draft:
                continue
            ca, cb = ctx.info(a).centroid, ctx.info(b).centroid
            if V.dot(na, V.sub(cb, ca)) <= tol or V.dot(nb, V.sub(ca, cb)) <= tol:
                continue  # facing away (a rib) or coplanar
            seen.add((a, b))
            width = abs(V.dot(V.sub(ctx.info(b).origin, ctx.info(a).origin), na))
            if opt.max_slot_width > 0 and width > opt.max_slot_width:
                continue
            # The floor and rims run the walls' length: they share the walls'
            # longest edges. Short neighbours are ends.
            def along(face, cls):
                best = []
                for arc in aag.arcs(face):
                    g = arc.other(face)
                    if arc.convexity not in cls or g in (a, b):
                        continue
                    gi = ctx.info(g)
                    if gi.kind != "plane" or not perpendicular(gi.normal, na, 2.0):
                        continue
                    via = arc.via if arc.virtual else g
                    length = sum(ctx.topo.edges[e].length for e in shared_edges(ctx.topo, face, via))
                    best.append((length, g))
                return best

            longest_a = max([ln for ln, _ in along(a, ("concave", "convex"))] or [0.0])
            if longest_a <= tol:
                continue
            rims = {g for ln, g in along(a, ("convex",)) + along(b, ("convex",)) if ln >= 0.5 * longest_a}
            floors = {g for ln, g in along(a, ("concave",)) if ln >= 0.5 * longest_a} & \
                {g for ln, g in along(b, ("concave",)) if ln >= 0.5 * longest_a}
            floors -= {f for f in floors if f in ctx.owner}
            if not rims:
                continue
            rim_list = sorted(rims)
            depth_dir = ctx.info(rim_list[0]).normal
            # a floor faces out of the slot, like the rims (a short end wall can
            # share an edge as long as the floor's)
            floors = {f for f in floors if V.dot(ctx.info(f).normal, depth_dir) > 0.9}
            if any(V.dot(ctx.info(r).normal, depth_dir) < 0.9 and V.dot(ctx.info(r).normal, depth_dir) > -0.9
                   for r in rim_list):
                continue
            through = any(V.dot(ctx.info(r).normal, depth_dir) < -0.9 for r in rim_list) and not floors
            floor = max(floors, key=lambda f: ctx.info(f).area) if floors else None
            if floor is not None and V.dot(ctx.info(floor).normal, depth_dir) < 0.9:
                continue
            u = V.unit(V.cross(na, depth_dir))
            common = (by_concave[a] & by_concave[b]) - {a, b} - set(floors)
            ends = []
            for c in sorted(common):
                ci = ctx.info(c)
                if c in ctx.owner:
                    continue
                if ci.kind == "plane" and abs(V.dot(ci.normal, u)) > 0.95:
                    ends.append((c, "wall"))
                elif ci.kind == "cylinder" and ci.internal and abs(V.dot(ci.axis, depth_dir)) > 0.95 \
                        and abs(ci.radius - width / 2.0) <= max(tol, 0.02 * width):
                    ends.append((c, "round"))
            if floor is None and not through:
                continue
            # A floor runs between the walls. One that spreads well past them
            # is the face two protrusions stand on (the gap between two
            # embossed letters), not a slot's floor.
            if floor is not None:
                fe = extent(face_points(ctx.topo, floor), na)
                wa = V.dot(ctx.info(a).origin, na)
                wb = V.dot(ctx.info(b).origin, na)
                if fe[0] < min(wa, wb) - 0.05 * width - tol or fe[1] > max(wa, wb) + 0.05 * width + tol:
                    continue
            pa = face_points(ctx.topo, a) + face_points(ctx.topo, b)
            lu = extent(pa, u)
            end_sides = {}
            for c, kind in ends:
                pos = V.dot(ctx.info(c).centroid, u)
                side = "lo" if abs(pos - lu[0]) < abs(pos - lu[1]) else "hi"
                end_sides[side] = kind
            # An end is open only if the walls stop there at convex edges: a
            # wall meeting anything else concavely at an end (a pocket's
            # slanted side) closes it.
            span = lu[1] - lu[0]
            closed_other = False
            for w in (a, b):
                for e in ctx.info(w).edges:
                    edge = ctx.topo.edges[e]
                    if edge is None or not edge.is_manifold:
                        continue
                    other = edge.other(w)
                    if other in (a, b) or other in floors or other in rims:
                        continue
                    ps = [V.dot(p, u) for p in edge.points]
                    side = "lo" if max(ps) <= lu[0] + 0.1 * span + tol else \
                        ("hi" if min(ps) >= lu[1] - 0.1 * span - tol else None)
                    if side and aag.edge_class(w, e) == "concave" and side not in end_sides:
                        end_sides[side] = "wall"
                        closed_other = True
            kinds = sorted(end_sides.values())
            if kinds == ["wall", "wall"]:
                continue  # a rectangular pocket / cutout: the pocket recognizer's
            if len(end_sides) == 2:
                subtype = "obround" if kinds == ["round", "round"] else "closed"
            elif len(end_sides) == 1:
                subtype = "open_end"
            else:
                subtype = "open"
            owned = [a, b] + ([floor] if floor else []) + [c for c, _ in ends]
            if any(f in ctx.owner for f in owned):
                continue
            dpts = face_points(ctx.topo, a)
            dd = extent(dpts, depth_dir)
            depth = dd[1] - dd[0]
            if floor is not None:
                # from the floor's plane (a floor fillet shortens the walls)
                fi = ctx.info(floor)
                depth = max(V.dot(V.sub(p, fi.origin), fi.normal) for p in dpts)
            length = lu[1] - lu[0]
            score = Score(0.9)
            score.ok(f"walls facing each other {width:.3g} apart")
            score.ok(("no floor (cut through)" if through else "flat floor") + f", depth {depth:.3g}")
            if length < width:
                score.penalize(0.7, "shorter than it is wide")
            params = {
                "width": r3(width), "depth": r3(depth), "length": r3(length),
                "through": through, "open_ends": 2 - len(end_sides),
                "end_radius": r3(width / 2.0) if "round" in kinds else 0.0,
            }
            if "round" in kinds:
                # the straight walls plus the half-round ends
                params["overall_length"] = r3(length + width / 2.0 * kinds.count("round"))
            mid = V.mul(V.add(ca, cb), 0.5)
            out.append(Candidate(
                kind=FeatureKind.SLOT, subtype=subtype, faces=owned, support=sorted(rims),
                edges=sorted(e for r in rims for w in (a, b) for e in shared_edges(ctx.topo, w, r)),
                params=params, frame=Frame(vec3(mid), vec3(V.mul(depth_dir, -1.0)), vec3(u)), score=score,
            ))
    return out


def find_slits(ctx: Context) -> list[Candidate]:
    """A cut right through the part between two flat walls that face each
    other closely and meet nothing concavely: a clamp's split, the gap
    between two prongs. No floor, no ends; the walls open onto rims all
    round."""
    opt = ctx.options
    tol = max(opt.linear_tolerance * 10, 1e-6)
    walls = [f for f in ctx.aag.faces if f not in ctx.owner and ctx.info(f).kind == "plane"
             and not _neighbors(ctx, f, "concave")]
    out = []
    used: set[int] = set()
    for a, b in combinations(walls, 2):
        if a in used or b in used:
            continue
        ai, bi = ctx.info(a), ctx.info(b)
        if V.dot(ai.normal, bi.normal) > -0.999:
            continue
        width = V.dot(V.sub(bi.origin, ai.origin), ai.normal)
        if width <= tol:
            continue  # facing away: the two sides of a wall
        if opt.max_slot_width > 0 and width > opt.max_slot_width:
            continue
        pa, pb = face_points(ctx.topo, a), face_points(ctx.topo, b)
        # in-plane axes; the walls must overlap on both, and be much wider
        # and deeper than the gap
        e = max((ctx.topo.edges[x] for x in ai.outer if ctx.topo.edges[x] is not None and ctx.topo.edges[x].straight),
                key=lambda x: x.length, default=None)
        if e is None:
            continue
        u = V.sub(e.points[-1], e.points[0])
        u = V.unit(V.sub(u, V.mul(ai.normal, V.dot(u, ai.normal))))
        v = V.unit(V.cross(ai.normal, u))
        overlap = []
        for ax in (u, v):
            la, lb = extent(pa, ax), extent(pb, ax)
            common = min(la[1], lb[1]) - max(la[0], lb[0])
            overlap.append((common, min(la[1] - la[0], lb[1] - lb[0])))
        if any(c < 0.5 * m or c < 3.0 * width for c, m in overlap):
            continue
        used.update((a, b))
        score = Score(0.8)
        score.ok(f"two walls {width:.3g} apart facing each other, cut through")
        rims = sorted((_neighbors(ctx, a, "convex") | _neighbors(ctx, b, "convex")) - {a, b})
        mid = V.mul(V.add(ai.centroid, bi.centroid), 0.5)
        out.append(Candidate(
            kind=FeatureKind.SLOT, subtype="open", faces=sorted([a, b]), support=rims,
            params={"width": r3(width), "depth": r3(overlap[1][0]), "length": r3(overlap[0][0]),
                    "through": True, "open_ends": 2, "end_radius": 0.0, "profile": "slit"},
            frame=Frame(vec3(mid), vec3(v), vec3(u)), score=score,
        ))
    return out


def find_steps(ctx: Context) -> list[Candidate]:
    aag = ctx.aag
    out = []
    seen = set()
    for f in aag.faces:
        if f in ctx.owner or ctx.info(f).kind != "plane":
            continue
        concave = _neighbors(ctx, f, "concave")
        if len(concave) != 1:
            continue
        w = next(iter(concave))
        if w in ctx.owner or ctx.info(w).kind != "plane" or (min(f, w), max(f, w)) in seen:
            continue
        if _neighbors(ctx, w, "concave") != {f}:
            continue
        if not perpendicular(ctx.info(f).normal, ctx.info(w).normal, 30.0):
            continue
        if not _neighbors(ctx, f, "convex") or not _neighbors(ctx, w, "convex"):
            continue
        seen.add((min(f, w), max(f, w)))
        # Both faces opening onto faces that look the same way: a V groove
        # cut into one face, not a step at the edge between two.
        fw = V.cross(ctx.info(f).normal, ctx.info(w).normal)
        if V.norm(fw) > 1e-9:
            r1, r2 = rim_along(ctx, f, V.unit(fw), {f, w}), rim_along(ctx, w, V.unit(fw), {f, w})
            if r1 is not None and r2 is not None and ctx.info(r1).kind == "plane" \
                    and ctx.info(r2).kind == "plane" and V.dot(ctx.info(r1).normal, ctx.info(r2).normal) > 0.98:
                continue
        # Floor = the face whose normal points away from the other face's interior
        # The two faces look alike; the wall is the one reaching less far
        # from the edge they share (a step is wider than it is deep).
        fi, wi = ctx.info(f), ctx.info(w)
        reach_f = extent(face_points(ctx.topo, f), wi.normal)
        reach_w = extent(face_points(ctx.topo, w), fi.normal)
        if (reach_f[1] - reach_f[0]) >= (reach_w[1] - reach_w[0]):
            floor, wall = f, w
        else:
            floor, wall = w, f
        fl, wl = ctx.info(floor), ctx.info(wall)
        # measured from the other face's plane, so a fillet between them
        # doesn't shorten either
        depth = max(abs(V.dot(V.sub(p, fl.origin), fl.normal)) for p in face_points(ctx.topo, wall))
        width = max(abs(V.dot(V.sub(p, wl.origin), wl.normal)) for p in face_points(ctx.topo, floor))
        edges = shared_edges(ctx.topo, floor, wall)
        length = sum(ctx.topo.edges[e].length for e in edges) if edges else 0.0
        score = Score(0.85)
        score.ok("floor and one wall, open on the other sides")
        out.append(Candidate(
            kind=FeatureKind.STEP, subtype="", faces=sorted([floor, wall]),
            support=sorted((_neighbors(ctx, floor, "convex") | _neighbors(ctx, wall, "convex")) - {floor, wall}),
            edges=edges,
            params={"depth": r3(depth), "width": r3(width), "length": r3(length)},
            frame=Frame(vec3(fl.centroid), vec3(fl.normal), vec3(wl.normal)), score=score,
        ))
    return out
