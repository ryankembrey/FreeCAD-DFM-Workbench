# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Fillets, rounds and chamfers; run first, then hidden in the graph.

Blend faces (Venkataraman, Sohoni & Elber 2001; Slyadnev & Turlapov 2020)
are part-cylinders, part-tori or sphere patches tangent ("smooth" edges) to
at least two support faces, turning through less than ``max_blend_span``.
Half-round ends (180°) are left to ribs and slots. Concave (inside the
material's corner) is a fillet, convex a round. Blends of one radius that
touch form a chain: one FILLET feature per chain.

Chamfers are narrow planar strips (or external cones) between two faces,
meeting each at between ``min_chamfer_angle`` and ``90 - min_chamfer_angle``.

Once found, each blend face is hidden in the AAG and its supports are
joined by a virtual arc of the convexity it stood for.
"""

from __future__ import annotations

import math

from ..model import FeatureKind, Frame
from .._graph import Blend
from .._topology import vec as V
from .common import Candidate, Context, Score, deg, r3, shared_edges


def _torus_minor_span(ctx: Context, face: int) -> float:
    info = ctx.info(face)
    pts = []
    for e in info.edges:
        edge = ctx.topo.edges[e]
        if edge is not None:
            pts.extend(edge.points)
    if info.sample_point:
        pts.append(info.sample_point)
    if len(pts) < 2:
        return 0.0
    angles = []
    for p in pts:
        rad = V.radial(p, info.origin, info.axis)
        if V.norm(rad) < 1e-12:
            continue
        ru = V.unit(rad)
        # The tube's centre circle lies at axial height 0 of the torus frame.
        centre = V.add(info.origin, V.mul(ru, info.major_radius))
        d = V.sub(p, centre)
        angles.append(math.degrees(math.atan2(V.dot(d, info.axis), V.dot(d, ru))))
    if not angles:
        return 0.0
    angles = sorted(a % 360.0 for a in angles)
    gaps = [(angles[(i + 1) % len(angles)] - a) % 360.0 for i, a in enumerate(angles)]
    if len(angles) == 1:
        return 0.0
    return 360.0 - max(gaps)


def _blend_span(ctx: Context, face: int) -> float:
    info = ctx.info(face)
    if info.kind == "cylinder":
        return info.span
    if info.kind == "torus":
        return _torus_minor_span(ctx, face)
    return 0.0


def _smooth_neighbors(ctx: Context, face: int) -> set[int]:
    aag = ctx.aag
    out = set()
    for arc in aag.arcs(face):
        g = arc.other(face)
        if arc.convexity == "smooth" and not aag.same_surface(face, g):
            out.add(g)
    return out


def find_blends(ctx: Context) -> list[Candidate]:
    opt = ctx.options
    aag = ctx.aag
    cands: dict[int, dict] = {}
    for f in aag.faces:
        info = ctx.info(f)
        freeform = info.curvature_radius > 0
        if (info.kind not in ("cylinder", "torus", "sphere") and not freeform) or info.internal is None:
            continue
        radius = info.curvature_radius if freeform else info.radius
        if radius <= 0 or radius > opt.max_fillet_radius:
            continue
        smooth = _smooth_neighbors(ctx, f)
        if len(smooth) < 2:
            continue
        if info.kind in ("cylinder", "torus"):
            span = _blend_span(ctx, f)
            if span <= 0.5 or span > opt.max_blend_span:
                continue
        else:
            span = 0.0
        cands[f] = {"smooth": smooth, "span": span}

    blends: dict[int, Blend] = {}
    for f, data in cands.items():
        info = ctx.info(f)
        supports = sorted(g for g in data["smooth"] if g not in cands)
        chain = sorted(g for g in data["smooth"] if g in cands)
        if info.kind in ("cylinder", "torus"):
            if len(supports) < 2 and not (supports and chain):
                continue
        else:  # sphere: a corner blend between other blends
            if len(chain) < 2 and len(supports) < 2:
                continue
        kind = "fillet" if info.internal else "round"
        radius = info.curvature_radius if info.curvature_radius > 0 else info.radius
        blends[f] = Blend(f, kind, "concave" if info.internal else "convex", tuple(supports), radius)

    # Corner spheres support the supports of the blends they join
    for f, b in list(blends.items()):
        if ctx.info(f).kind == "sphere" and len(b.supports) < 2:
            sup = set(b.supports)
            for g in cands[f]["smooth"]:
                if g in blends:
                    sup.update(blends[g].supports)
            blends[f] = Blend(f, b.kind, b.convexity, tuple(sorted(sup)), b.radius)

    # Chains: touching blends of the same kind and radius
    tol = max(opt.linear_tolerance * 10, 1e-6)
    parent = {f: f for f in blends}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for f in blends:
        for arc in aag.arcs(f):
            g = arc.other(f)
            if g in blends and blends[g].kind == blends[f].kind and abs(blends[g].radius - blends[f].radius) <= tol:
                parent[find(g)] = find(f)
    # pieces of one fillet split by other features (gussets across a fillet)
    fl = sorted(blends)
    for i, f in enumerate(fl):
        for g in fl[i + 1:]:
            if find(f) != find(g) and blends[f].kind == blends[g].kind and \
                    ctx.info(f).kind == ctx.info(g).kind and aag.same_surface(f, g):
                parent[find(g)] = find(f)
    chains: dict[int, list[int]] = {}
    for f in blends:
        chains.setdefault(find(f), []).append(f)

    out = []
    for members in chains.values():
        members.sort()
        kind = blends[members[0]].kind
        radii = [blends[m].radius for m in members]
        supports = sorted({s for m in members for s in blends[m].supports} - set(members))
        score = Score(0.95)
        score.ok(f"{len(members)} face(s) tangent to their neighbours, radius {radii[0]:.3g}")
        gaps = [ctx.info(m).fit_gap for m in members if ctx.info(m).native_kind != ctx.info(m).kind]
        if gaps:
            score.penalize(0.95, "B-spline surface fitted to a canonical one")
        spans = [cands[m]["span"] for m in members if cands[m]["span"]]
        edges = sorted({e for m in members for s in blends[m].supports for e in shared_edges(ctx.topo, m, s)})
        first = ctx.info(members[0])
        out.append(Candidate(
            kind=FeatureKind.FILLET,
            subtype="concave" if kind == "fillet" else "convex",
            faces=members, support=supports, edges=edges,
            params={
                "radius": r3(sorted(radii)[len(radii) // 2]),
                "radius_min": r3(min(radii)), "radius_max": r3(max(radii)),
                "face_count": len(members),
                "span_deg": deg(max(spans)) if spans else 0.0,
            },
            frame=Frame(tuple(first.origin), tuple(first.axis)) if first.origin and first.axis else None,
            score=score,
            links={"blends": [blends[m] for m in members]},
        ))
    return out


def _line_dir(ctx: Context, e: int) -> V.Vec:
    pts = ctx.topo.edges[e].points
    return V.unit(V.sub(pts[-1], pts[0]))


def find_chamfers(ctx: Context) -> list[Candidate]:
    opt = ctx.options
    aag = ctx.aag
    lo, hi = opt.min_chamfer_angle, 90.0 - opt.min_chamfer_angle
    found: dict[int, dict] = {}
    for f in aag.faces:
        info = ctx.info(f)
        if f in ctx.owner:
            continue
        if info.kind == "plane":
            edges = [ctx.topo.edges[e] for e in info.outer if ctx.topo.edges[e] is not None]
            sharp = [e for e in edges if e.is_manifold and e.convexity in ("convex", "concave")]
            if len(sharp) < 2:
                continue
            sharp.sort(key=lambda e: -e.length)
            e1, e2 = sharp[0], sharp[1]
            if not e1.straight or not e2.straight or e1.convexity != e2.convexity:
                continue
            if V.line_angle(_line_dir(ctx, e1.index), _line_dir(ctx, e2.index)) > 2.0:
                continue
            s1, s2 = e1.other(f), e2.other(f)
            if s1 == s2 or aag.same_surface(s1, s2):
                continue
            a1, a2 = abs(e1.angle), abs(e2.angle)
            if not (lo <= a1 <= hi and lo <= a2 <= hi):
                continue
            # An inside bevel stands for a corner of about a right angle; a
            # wider turn is a side of a polygonal hole or pocket.
            if e1.convexity == "concave" and a1 + a2 > 100.0:
                continue
            width = V.point_line_distance(e2.points[2], e1.points[0], _line_dir(ctx, e1.index))
            if width <= opt.linear_tolerance or width > opt.max_chamfer_width:
                continue
            if width > min(e1.length, e2.length) * 1.01:
                continue
            # The end edges are short (about the width): a strip, not a wall.
            others = [e for e in edges if e.index not in (e1.index, e2.index)]
            if any(e.length > 3.0 * width + opt.linear_tolerance for e in others):
                continue
            # A bevel has nothing standing on it: a long concave edge makes
            # the face the side of a step or groove.
            if e1.convexity == "convex" and any(
                    e.convexity == "concave" and e.length > 0.5 * e1.length for e in others):
                continue
            # The two supports would meet at the edge the chamfer replaced.
            n1 = ctx.info(s1).sample_normal
            n2 = ctx.info(s2).sample_normal
            dist1 = width * math.sin(math.radians(a2)) / max(1e-9, math.sin(math.radians(a1 + a2)))
            dist2 = width * math.sin(math.radians(a1)) / max(1e-9, math.sin(math.radians(a1 + a2)))
            found[f] = {"supports": (s1, s2), "convexity": e1.convexity, "width": width,
                        "angles": (a1, a2), "d": (dist1, dist2), "edges": (e1.index, e2.index),
                        "normals": (n1, n2)}
        elif info.kind == "cone" and (info.internal is False or (
                info.internal and ctx.options.min_chamfer_angle <= info.semi_angle)):
            edges = [ctx.topo.edges[e] for e in info.outer if ctx.topo.edges[e] is not None]
            circles = [e for e in edges if e.curve == "circle" and e.is_manifold and e.center is not None]
            if len(circles) != 2:
                continue
            if not (lo <= info.semi_angle <= hi):
                continue
            if any(e.convexity not in ("convex",) for e in circles):
                continue
            s1, s2 = circles[0].other(f), circles[1].other(f)
            if s1 == s2:
                continue
            dr = abs(circles[0].circle_radius - circles[1].circle_radius)
            dz = abs(V.dot(V.sub(circles[0].center, circles[1].center), info.axis))
            width = math.hypot(dr, dz)
            if width <= opt.linear_tolerance or width > opt.max_chamfer_width:
                continue
            # Inside a hole, a narrow cone is the entry's lead-in chamfer; a
            # wide one is a countersink, which the hole recognizer takes.
            if info.internal and width > 0.35 * min(c.circle_radius for c in circles):
                continue
            found[f] = {"supports": (s1, s2), "convexity": "convex", "width": width,
                        "angles": (abs(circles[0].angle), abs(circles[1].angle)), "d": (dz, dr),
                        "edges": (circles[0].index, circles[1].index), "normals": None}

        else:
            strip = _curved_strip(ctx, f, info, lo, hi)
            if strip is not None:
                found[f] = strip

    # A chamfer bevels the corner between two larger faces. Faces whose
    # "supports" are themselves strips of the same kind (the walls of a
    # hexagonal pocket, each between two others) are not chamfers, and
    # neither is a strip as big as the faces it would bevel.
    for f in list(found):
        s1, s2 = found[f]["supports"]
        # Convex bevels can be broad (a block corner cut off); a concave
        # strip that large is a wall of whatever depression it lines.
        ratio = 2.0 if found[f]["convexity"] == "convex" else 0.5
        if ctx.info(f).area > ratio * min(ctx.info(s1).area, ctx.info(s2).area):
            found.pop(f)
    # Two strips bevelling each other: the more even-angled one is the
    # chamfer (its neighbour is a face it cut, e.g. what is left of a side).
    strip_sup = {f: [s for s in found[f]["supports"] if s in found] for f in found}

    def evenness(f):
        a1, a2 = found[f]["angles"]
        return (abs(a1 - a2), ctx.info(f).area)

    drop = set()
    for f, sup in strip_sup.items():
        if len(sup) == 2:
            drop.add(f)
        elif len(sup) == 1 and len(strip_sup[sup[0]]) == 1 and evenness(f) > evenness(sup[0]):
            drop.add(f)
    for f in drop:
        found.pop(f)

    # Corner faces bounded only by chamfers join them (a chamfered box corner)
    corners: dict[int, list[int]] = {}
    for f in aag.faces:
        if f in found or f in ctx.owner or ctx.info(f).kind != "plane":
            continue
        nbrs = aag.neighbors(f)
        if len(nbrs) >= 3 and nbrs <= set(found):
            w = max(found[n]["width"] for n in nbrs)
            if ctx.info(f).area <= 2.0 * w * w + opt.linear_tolerance:
                corners[f] = sorted(nbrs)
    # Small patches running smoothly from one chamfer into another (where a
    # chamfer along a curved edge meets one along a straight edge)
    for f in aag.faces:
        if f in found or f in ctx.owner or f in corners:
            continue
        smooth = sorted({a.other(f) for a in aag.arcs(f) if a.convexity == "smooth" and a.other(f) in found})
        if len(smooth) >= 2:
            w = max(found[n]["width"] for n in smooth)
            if ctx.info(f).area <= 6.0 * w * w + opt.linear_tolerance:
                corners[f] = smooth

    parent = {f: f for f in found}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for c, nbrs in corners.items():
        for n in nbrs[1:]:
            parent[find(n)] = find(nbrs[0])
    for f in found:
        for arc in aag.arcs(f):
            g = arc.other(f)
            if g not in found:
                continue
            # one chamfer running on (tangent), or the same width round a corner
            same = abs(found[g]["width"] - found[f]["width"]) <= max(opt.linear_tolerance * 10, 1e-6,
                                                                     0.05 * found[f]["width"])
            if arc.convexity == "smooth" or same:
                parent[find(g)] = find(f)
    groups: dict[int, list[int]] = {}
    for f in found:
        groups.setdefault(find(f), []).append(f)

    out = []
    for members in groups.values():
        members.sort()
        corner_faces = sorted(c for c, n in corners.items() if find(n[0]) == find(members[0]))
        d0 = found[members[0]]
        supports = sorted({s for m in members for s in found[m]["supports"]} - set(members) - set(corner_faces))
        widths = [found[m]["width"] for m in members]
        a1, a2 = d0["angles"]
        score = Score(0.9)
        score.ok(f"narrow face ({widths[0]:.3g} wide) at {a1:.0f}° / {a2:.0f}° to its neighbours")
        if abs(a1 - a2) > 1.0:
            score.ok("unequal chamfer")
        out.append(Candidate(
            kind=FeatureKind.CHAMFER,
            subtype="convex" if d0["convexity"] == "convex" else "concave",
            faces=members + corner_faces, support=supports,
            edges=sorted({e for m in members for e in found[m]["edges"]}),
            params={
                "width": r3(min(widths)), "width_max": r3(max(widths)),
                "distance1": r3(d0["d"][0]), "distance2": r3(d0["d"][1]),
                "angle": deg(a1), "face_count": len(members) + len(corner_faces),
            },
            score=score,
            links={"blends": [Blend(m, "chamfer", found[m]["convexity"], tuple(found[m]["supports"]),
                                    found[m]["width"]) for m in members]
                   + [Blend(c, "chamfer", d0["convexity"], tuple(sorted({s for n in corners[c] for s in found[n]["supports"]})), 0.0)
                      for c in corner_faces]},
        ))
    return out


def _curved_strip(ctx: Context, f: int, info, lo: float, hi: float):
    """A chamfer along a curved edge (a bevel round a saddle cut, say): a
    face of any surface whose two longest edges are sharp at chamfer angles
    to two other faces and stay a constant distance apart."""
    opt = ctx.options
    if info.kind in ("plane", "sphere", "torus") or (info.kind == "cylinder"):
        return None
    edges = [ctx.topo.edges[e] for e in info.outer if ctx.topo.edges[e] is not None]
    sharp = sorted((e for e in edges if e.is_manifold and e.convexity in ("convex", "concave") and len(e.points) >= 2),
                   key=lambda e: -e.length)
    if len(sharp) < 2:
        return None
    e1, e2 = sharp[0], sharp[1]
    if e1.convexity != e2.convexity or e1.straight:
        return None
    s1, s2 = e1.other(f), e2.other(f)
    if s1 == s2 or ctx.aag.same_surface(s1, s2):
        return None
    a1, a2 = abs(e1.angle), abs(e2.angle)
    if not (lo <= a1 <= hi and lo <= a2 <= hi):
        return None
    if e1.convexity == "concave" and a1 + a2 > 100.0:
        return None

    def dist_to(p, pts):
        best = float("inf")
        for a, b in zip(pts, pts[1:]):
            ab = V.sub(b, a)
            t = max(0.0, min(1.0, V.dot(V.sub(p, a), ab) / max(V.dot(ab, ab), 1e-18)))
            best = min(best, V.norm(V.sub(p, V.add(a, V.mul(ab, t)))))
        return best

    d = [dist_to(p, e1.points) for p in e2.points]
    if not d:
        return None
    width = sum(d) / len(d)
    if width <= opt.linear_tolerance or width > opt.max_chamfer_width:
        return None
    if max(d) - min(d) > 0.25 * width + opt.linear_tolerance:
        return None  # not a constant-width band
    if width > 0.5 * min(e1.length, e2.length):
        return None
    others = [e for e in edges if e.index not in (e1.index, e2.index)]
    if any(e.convexity in ("convex", "concave") and e.length > 3.0 * width + opt.linear_tolerance for e in others):
        return None
    dist1 = width * math.sin(math.radians(a2)) / max(1e-9, math.sin(math.radians(a1 + a2)))
    dist2 = width * math.sin(math.radians(a1)) / max(1e-9, math.sin(math.radians(a1 + a2)))
    return {"supports": (s1, s2), "convexity": e1.convexity, "width": width, "angles": (a1, a2),
            "d": (dist1, dist2), "edges": (e1.index, e2.index), "normals": None}


def suppress(ctx: Context, candidates: list[Candidate]) -> None:
    """Hide accepted blend/chamfer faces in the AAG (virtual arcs)."""
    for cand in candidates:
        for blend in cand.links.get("blends", []):
            if blend.face in cand.faces:
                ctx.aag.hide(blend)
