# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Ribs and gussets (after Lai et al.'s parallel pairs on thin-shell parts).

Hint: two planar side faces, anti-parallel within the allowed draft and
facing *away* from each other, both meeting one base concavely (real or
through a hidden root fillet), closed at the top by a face both meet
convexly (or a half-round tip). Thin (``max_rib_thickness``), tall
(``min_rib_aspect``) and long (``min_rib_length_ratio``) enough.

Coplanar faces count as one base (a rib running wall to wall splits its
floor in two). Side faces split by crossing ribs are pieces of one rib
when they lie on the same two planes and follow on from each other along
the rib (gaps no wider than a rib); pieces further apart (gussets either
side of a boss) are separate ribs. The base is the face the sides stand
on along their length; other faces they meet concavely are what the rib
joins (a wall, a boss, another rib).
"""

from __future__ import annotations

import math
from itertools import combinations

from ..model import FeatureKind, Frame
from .._topology import vec as V
from .common import Candidate, Context, Score, deg, extent, face_points, r3, shared_edges, vec3


def _concave_planes(ctx: Context, base: int) -> list[int]:
    out = []
    for arc in ctx.aag.arcs(base):
        g = arc.other(base)
        if arc.convexity != "concave" or g in ctx.owner:
            continue
        if ctx.info(g).kind == "plane" and g not in out:
            out.append(g)
    return out


def _tops(ctx: Context, a: int, b: int) -> set[int]:
    aag = ctx.aag
    ta = {arc.other(a) for arc in aag.arcs(a) if arc.convexity in ("convex", "smooth")}
    tb = {arc.other(b) for arc in aag.arcs(b) if arc.convexity in ("convex", "smooth")}
    return (ta & tb) - {a, b}


def _root_length(ctx: Context, side: int, base: int) -> float:
    """Length of the edges a side shares with its base (through a hidden root
    fillet, the fillet's edges with the side)."""
    total = sum(ctx.topo.edges[e].length for e in shared_edges(ctx.topo, side, base))
    if total:
        return total
    for arc in ctx.aag.arcs_between(side, base):
        if arc.virtual and arc.via:
            total += sum(ctx.topo.edges[e].length for e in shared_edges(ctx.topo, side, arc.via))
    return total


def _plane_key(ctx: Context, f: int) -> tuple:
    i = ctx.info(f)
    n = i.normal
    d = V.dot(n, i.origin)
    return (round(n[0], 3) + 0.0, round(n[1], 3) + 0.0, round(n[2], 3) + 0.0, round(d, 2) + 0.0)


def _frame(ctx: Context, a: int, b: int, members: list[int]):
    """(m, u, h): side normal of a (away from b), length and height directions."""
    na, nb = ctx.info(a).normal, ctx.info(b).normal
    m = V.unit(V.sub(na, nb))
    ra = [e for mb in members for e in shared_edges(ctx.topo, a, mb)]
    if ra and ctx.topo.edges[ra[0]].curve == "line":
        pts = ctx.topo.edges[ra[0]].points
        u = V.unit(V.sub(pts[-1], pts[0]))
    else:
        bi = ctx.info(members[0])
        u = V.unit(V.cross(m, bi.normal if bi.kind == "plane" else (bi.sample_normal or (0.0, 0.0, 1.0))))
    h = V.unit(V.cross(u, m))
    centre = ctx.info(a).centroid
    if ra:
        ref = min((p for e in ra for p in ctx.topo.edges[e].points), key=lambda p: V.dot(p, h))
    else:
        bi = ctx.info(members[0])
        ref = bi.origin if bi.kind == "plane" else (bi.sample_point or bi.centroid)
    if V.dot(V.sub(centre, ref), h) < 0:
        h = V.mul(h, -1.0)
    return m, u, h


def _collect_pairs(ctx: Context) -> dict:
    """{(plane key a, plane key b): {base: {"members", "pieces": {(a, b): tops}, "frame"}}}"""
    opt = ctx.options
    aag = ctx.aag
    groups: dict[tuple, list[int]] = {}
    for f in aag.faces:
        if ctx.info(f).kind == "plane":
            groups.setdefault(_plane_key(ctx, f), []).append(f)
        else:
            groups.setdefault(("face", f), []).append(f)
    pairs: dict[tuple, dict] = {}
    for members in groups.values():
        base = members[0]
        sides = []
        for mb in members:
            for sd in _concave_planes(ctx, mb):
                if sd not in sides and sd not in members:
                    sides.append(sd)
        for a, b in combinations(sides, 2):
            na, nb = ctx.info(a).normal, ctx.info(b).normal
            if V.angle(na, V.mul(nb, -1.0)) > 2 * opt.max_rib_draft + opt.parallel_angle:
                continue
            ca, cb = ctx.info(a).centroid, ctx.info(b).centroid
            if V.dot(na, V.sub(cb, ca)) >= 0 or V.dot(nb, V.sub(ca, cb)) >= 0:
                continue  # facing each other: a slot, not a rib
            ka, kb = _plane_key(ctx, a), _plane_key(ctx, b)
            if kb < ka:
                a, b, ka, kb = b, a, kb, ka
            m, u, h = _frame(ctx, a, b, members)
            pa, pb = face_points(ctx.topo, a), face_points(ctx.topo, b)
            if not pa or not pb:
                continue
            ua, ub = extent(pa, u), extent(pb, u)
            overlap = min(ua[1], ub[1]) - max(ua[0], ub[0])
            if overlap <= 0.5 * min(ua[1] - ua[0], ub[1] - ub[0]):
                continue
            tops = _tops(ctx, a, b) - set(members)
            if not tops:
                continue
            per_base = pairs.setdefault((ka, kb), {})
            entry = per_base.setdefault(base, {"members": members, "pieces": {}, "frame": (m, u, h)})
            entry["pieces"][(a, b)] = tops
    return pairs


def _clusters(ctx: Context, per_base: dict, gap: float) -> list[list[tuple]]:
    """Pieces (a, b) of one plane pair that follow on from each other: no
    further apart than a crossing rib is thick (gussets either side of a
    boss are further apart, and stay separate ribs)."""
    pieces = sorted({pc for e in per_base.values() for pc in e["pieces"]})
    pts = {pc: face_points(ctx.topo, pc[0]) + face_points(ctx.topo, pc[1]) for pc in pieces}
    parent = {pc: pc for pc in pieces}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, p in enumerate(pieces):
        for q in pieces[i + 1:]:
            if find(p) == find(q):
                continue
            d = min(V.dist(x, y) for x in pts[p] for y in pts[q])
            if d <= gap:
                parent[find(q)] = find(p)
    out: dict[tuple, list[tuple]] = {}
    for pc in pieces:
        out.setdefault(find(pc), []).append(pc)
    return list(out.values())


def _broad(ctx: Context, face: int, thickness: float) -> bool:
    fi = ctx.info(face)
    if fi.kind != "plane":
        return False
    perimeter = sum(ctx.topo.edges[e].length for e in fi.edges if ctx.topo.edges[e] is not None)
    return fi.area > 2.0 * thickness * perimeter / 2.0 + 1e-9


def find_ribs(ctx: Context) -> list[Candidate]:
    opt = ctx.options
    aag = ctx.aag
    tol = max(opt.linear_tolerance, 1e-6)
    out = []
    for key, per_base in sorted(_collect_pairs(ctx).items()):
        for cluster in _clusters(ctx, per_base, opt.max_rib_thickness + 10 * tol):
            cset = set(cluster)
            roots = {}
            for bse, e in per_base.items():
                mine = [pc for pc in e["pieces"] if pc in cset]
                if mine:
                    roots[bse] = sum(_root_length(ctx, a, mb) + _root_length(ctx, b, mb)
                                     for a, b in mine for mb in e["members"])
            if not roots:
                continue
            # A face of an already recognised feature (a boss) is something the
            # rib joins, not what it stands on: that makes it a gusset.
            primary = max(sorted(roots), key=lambda bse: (bse not in ctx.owner, roots[bse]))
            bases = [primary] + sorted(b for b in roots if b != primary and b not in ctx.owner
                                       and roots[b] >= 0.9 * roots[primary])
            base_faces = sorted({mb for bse in bases for mb in per_base[bse]["members"]})
            joins = {mb for bse in roots if bse not in bases for mb in per_base[bse]["members"]}
            m, u, h = per_base[primary]["frame"]
            sa = sorted({a for a, _ in cluster})
            sb = sorted({b for _, b in cluster})
            if any(f in ctx.owner for f in sa + sb):
                continue
            all_tops = set()
            for bse in roots:
                for pc, t in per_base[bse]["pieces"].items():
                    if pc in cset:
                        all_tops |= t
            all_tops -= set(base_faces) | joins

            pa = [p for f in sa for p in face_points(ctx.topo, f)]
            pb = [p for f in sb for p in face_points(ctx.topo, f)]
            base_pts = [p for f in sa for bse in base_faces for e in shared_edges(ctx.topo, f, bse)
                        for p in ctx.topo.edges[e].points]
            h0 = min(V.dot(p, h) for p in (base_pts or pa))
            bi = ctx.info(primary)
            if bi.kind == "plane" and abs(V.dot(bi.normal, h)) > 0.95:
                h0 = V.dot(bi.origin, h)  # from the base, not the top of a root fillet

            # Tops face away from the base; faces across the rib's ends are free ends
            tops, end_faces, shared_tops = [], [], []
            for t in sorted(all_tops):
                ti = ctx.info(t)
                if ti.kind == "plane" and abs(V.dot(ti.normal, u)) > math.cos(math.radians(30)):
                    end_faces.append(t)
                elif t in ctx.owner:
                    shared_tops.append(t)   # a crossing rib already took it
                else:
                    tops.append(t)
            if not tops and not shared_tops:
                continue
            top_pts = [p for t in tops + shared_tops for p in face_points(ctx.topo, t)]
            h1 = max(V.dot(p, h) for p in pa + top_pts)
            height = h1 - h0
            na, nb = ctx.info(sa[0]).normal, ctx.info(sb[0]).normal
            ob = ctx.info(sb[0]).origin

            def to_b(p):
                return abs(V.dot(V.sub(p, ob), nb))

            a_lo = min(V.dot(p, h) for p in pa)
            a_hi = max(V.dot(p, h) for p in pa)
            span = max(a_hi - a_lo, tol)
            root_side = [p for p in pa if V.dot(p, h) <= a_lo + 0.05 * span + tol]
            tip_side = [p for p in pa if V.dot(p, h) >= a_hi - 0.05 * span - tol]
            t_root = sum(map(to_b, root_side)) / len(root_side) if root_side else to_b(pa[0])
            t_tip = sum(map(to_b, tip_side)) / len(tip_side) if tip_side else t_root
            thickness = max(t_root, t_tip)
            if thickness <= tol or thickness > opt.max_rib_thickness:
                continue
            # A rib's top is a strip about as wide as the rib (a grid's or a
            # cross's tops are strips joined up: area ~ width x half the
            # perimeter). A top much broader than that is the surface the
            # "rib" is flush with: the wall between two engraved grooves.
            if any(_broad(ctx, t, thickness) for t in tops):
                continue
            lu = extent(pa + pb, u)
            length = lu[1] - lu[0]
            if height / thickness < opt.min_rib_aspect or length / thickness < opt.min_rib_length_ratio:
                continue
            draft_a = 90.0 - V.angle(na, h)
            draft_b = 90.0 - V.angle(nb, h)
            tip = "flat"
            for t in tops + shared_tops:
                ti = ctx.info(t)
                if ti.kind == "cylinder" and ti.internal is False:
                    tip = "round"
                elif ti.kind != "plane":
                    tip = "curved"
                elif abs(V.dot(ti.normal, h)) < math.cos(math.radians(15)):
                    tip = "sloped"

            sides = set(sa) | set(sb)
            skip = sides | set(base_faces) | set(tops) | set(shared_tops) | set(end_faces)
            free_ends = set()
            for f in sides:
                other = sb if f in sa else sa
                for arc in aag.arcs(f):
                    g = arc.other(f)
                    if g in skip:
                        continue
                    if arc.convexity == "convex":
                        if any(aag.arcs_between(g, x) for x in other):
                            free_ends.add(g)
                    elif arc.convexity == "concave":
                        joins.add(g)
            free_ends = {g for g in free_ends if g not in ctx.owner
                         and ctx.info(g).area <= 4 * thickness * height + tol}
            free_ends.update(t for t in end_faces if t not in ctx.owner)

            score = Score(0.9)
            score.ok(f"parallel side faces {thickness:.3g} apart on {len(base_faces)} base face(s)")
            score.ok(f"height {height:.3g} ({height / thickness:.1f} x thickness), length {length:.3g}")
            if abs(draft_a) > 0.05 or abs(draft_b) > 0.05:
                score.ok(f"drafted sides {draft_a:.2f}° / {draft_b:.2f}°")
            if len(sa) > 1 or len(sb) > 1:
                score.ok("side faces split by crossing features")
            if tip == "sloped":
                score.ok("sloped top (gusset-like)")
            if len(bases) > 1:
                score.ok("stands on two faces at once (a corner gusset)")
            root_blends = sorted({o for f in sides for arc in aag.adj[f] if not arc.virtual
                                  for o in [arc.other(f)] if aag.is_hidden(o)
                                  and aag.hidden[o].convexity == "concave"
                                  and set(aag.hidden[o].supports) & set(base_faces)})
            params = {
                "thickness": r3(thickness),
                "thickness_root": r3(t_root),
                "thickness_tip": r3(t_tip),
                "height": r3(height),
                "length": r3(length),
                "draft_deg_side1": deg(draft_a),
                "draft_deg_side2": deg(draft_b),
                "height_to_thickness": r3(height / thickness),
                "tip": tip,
                "base_faces": tuple(base_faces),
            }
            if root_blends:
                params["root_fillet_radius"] = r3(min(aag.hidden[o].radius for o in root_blends))
            mid = V.mul(V.add(ctx.info(sa[0]).centroid, ctx.info(sb[0]).centroid), 0.5)
            out.append(Candidate(
                kind=FeatureKind.RIB, subtype="sloped" if tip == "sloped" else "single",
                faces=sorted(sides | set(tops) | free_ends), support=base_faces + shared_tops,
                edges=sorted({e for f in sides for bse in base_faces for e in shared_edges(ctx.topo, f, bse)}),
                params=params, frame=Frame(vec3(mid), vec3(h), vec3(u)), score=score,
                links={"joins": joins, "sides": sides, "corner": len(bases) > 1, "tops": list(tops)},
            ))
    _flush_tops(ctx, out)
    return out


def _flush_tops(ctx: Context, ribs: list[Candidate]) -> None:
    """A rib's top is its own only if nothing but ribs stands on it. A top
    that also borders holes, outer walls or anything else is the surface
    the ribs are flush with (the rim of a ribbed housing): it is what the
    ribs stand up to, not part of them, so it moves to their support."""
    sides = set()
    mine = set()
    for c in ribs:
        sides |= set(c.links.get("sides", ()))
        mine |= set(c.faces)
    blends = (FeatureKind.FILLET, FeatureKind.CHAMFER)
    for c in ribs:
        flush = []
        for t in c.links.get("tops", ()):
            for arc in ctx.aag.arcs(t):
                g = arc.other(t)
                # what stands up from it meets it in a valley (a gusset's
                # top running into its boss); the rim it is flush with meets
                # other features at its own edges
                if arc.convexity != "convex" or g in sides or g in mine:
                    continue
                owner = ctx.owner_candidate(g)
                if owner is not None and owner.kind in blends:
                    continue
                flush.append(t)
                break
        if flush:
            c.faces = sorted(set(c.faces) - set(flush))
            c.support = sorted(set(c.support) | set(flush))
            c.params["tip"] = "flush"
            c.score.ok("its top is flush with the surrounding surface (not part of the rib)")
