# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Prismatic depressions the specific hints missed.

Runs after holes, slots, steps and pockets. The faces nobody has claimed
are split into *concave components* (faces joined by concave arcs, the
"depression" sub-graphs of Joshi & Chang). A component that is a
prismatic sweep is classified by its profile, the way 2.5D feature
recognisers do (Gao & Shah 1998; Sakurai & Dave 1996):

* Sweep direction ``d``: the direction of the concave edges between walls.
  Walls have normals perpendicular to ``d``; *caps* (floors / end walls)
  have normals along ``d``. Every face of the component must be one or
  the other.
* The walls' concave arcs form the profile: a *ring* (closed profile) or a
  *path* (open profile).
* Ring: no cap = CUTOUT (a passage of any section), one cap = POCKET.
* Path: the two outermost walls open convexly onto *rims*. Rims that face
  the same way (the part's top face either side of a groove) make a SLOT,
  rims on different faces (two sides of the part's edge) make a STEP.
  Caps close the sweep at its ends: one = blind slot / blind step.

This picks up V and polygonal slots and steps, blind steps cut into a
corner, steps with a two-sided (kinked) floor, and passages and pockets
whose rim loop was broken by an intersecting feature.
"""

from __future__ import annotations

import math
from typing import Optional

from ..model import FeatureKind, Frame
from .._topology import vec as V
from .common import Candidate, Context, Score, extent, face_points, r3, shared_edges, vec3

_MAX_FACES = 24
_ANG = 2.0      # degrees: walls perpendicular to / caps along the sweep direction


def _components(ctx: Context) -> list[list[int]]:
    aag = ctx.aag
    free = {f for f in aag.faces if f not in ctx.owner}
    seen: set[int] = set()
    out = []
    for f0 in sorted(free):
        if f0 in seen:
            continue
        comp, stack = [], [f0]
        seen.add(f0)
        while stack:
            f = stack.pop()
            comp.append(f)
            for arc in aag.arcs(f):
                g = arc.other(f)
                if arc.convexity == "concave" and g in free and g not in seen:
                    seen.add(g)
                    stack.append(g)
        if len(comp) >= 2:
            out.append(sorted(comp))
    return out


def _is_wall(ctx: Context, f: int, d: V.Vec) -> bool:
    fi = ctx.info(f)
    s = math.sin(math.radians(_ANG))
    if fi.kind == "plane":
        return abs(V.dot(fi.normal, d)) < s
    if fi.kind == "cylinder":
        return abs(V.dot(fi.axis, d)) > math.cos(math.radians(_ANG))
    return False


def _is_cap(ctx: Context, f: int, d: V.Vec) -> bool:
    fi = ctx.info(f)
    return fi.kind == "plane" and abs(V.dot(fi.normal, d)) > math.cos(math.radians(_ANG))


def _sweep(ctx: Context, comp: list[int]):
    """(d, walls, caps) for the sweep direction explaining every face, the
    one with most walls; None if the component isn't a prismatic sweep."""
    cset = set(comp)
    dirs: list[V.Vec] = []
    for f in comp:
        fi = ctx.info(f)
        for arc in ctx.aag.arcs(f):
            g = arc.other(f)
            if g <= f or g not in cset or arc.convexity != "concave":
                continue
            gi = ctx.info(g)
            if fi.kind == "plane" and gi.kind == "plane":
                c = V.cross(fi.normal, gi.normal)
                if V.norm(c) < 1e-6:
                    continue
                d = V.unit(c)
            elif fi.kind == "cylinder":
                d = fi.axis
            elif gi.kind == "cylinder":
                d = gi.axis
            else:
                continue
            if not any(abs(V.dot(d, e)) > math.cos(math.radians(_ANG)) for e in dirs):
                dirs.append(d)
    best = None
    for d in dirs:
        walls = [f for f in comp if _is_wall(ctx, f, d)]
        caps = [f for f in comp if f not in walls and _is_cap(ctx, f, d)]
        if len(walls) + len(caps) != len(comp) or len(walls) < 2:
            continue
        key = (len(walls), -len(caps))
        if best is None or key > best[0]:
            best = (key, d, walls, caps)
    return None if best is None else best[1:]


def _profile(ctx: Context, walls: list[int]):
    """("ring" | "path", ordered wall groups) or None. Pieces of one wall
    split by an intersecting feature count as one wall."""
    rep: dict[int, int] = {}
    for w in walls:
        rep[w] = next((rep[v] for v in rep if ctx.aag.same_surface(v, w)), w)
    groups = sorted(set(rep.values()))
    adj: dict[int, set[int]] = {g: set() for g in groups}
    for w in walls:
        for a in ctx.aag.arcs(w):
            o = a.other(w)
            if a.convexity == "concave" and o in rep and rep[o] != rep[w]:
                adj[rep[w]].add(rep[o])
    if max(len(v) for v in adj.values()) > 2:
        return None
    ends = [g for g in groups if len(adj[g]) == 1]
    if not ends and len(groups) >= 3:
        kind, start = "ring", groups[0]
    elif len(ends) == 2:
        kind, start = "path", ends[0]
    else:
        return None
    order, cur = [start], start
    while True:
        nxt = sorted(g for g in adj[cur] if g not in order)
        if not nxt:
            break
        cur = nxt[0]
        order.append(cur)
    if len(order) != len(groups):
        return None  # not connected
    members = {g: [w for w in walls if rep[w] == g] for g in groups}
    return kind, [members[g] for g in order]


def rim_along(ctx: Context, wall: int, d: V.Vec, comp: set[int]):
    """The face *wall* opens onto: its convex neighbour along the sweep
    (sharing the longest edge running along d)."""
    best = None
    for arc in ctx.aag.arcs(wall):
        g = arc.other(wall)
        if arc.convexity != "convex" or g in comp:
            continue
        via = arc.via if arc.virtual else g
        length = 0.0
        for e in shared_edges(ctx.topo, wall, via):
            edge = ctx.topo.edges[e]
            if edge.straight and len(edge.points) >= 2:
                t = V.sub(edge.points[-1], edge.points[0])
                if V.norm(t) > 1e-9 and abs(V.dot(V.unit(t), d)) > math.cos(math.radians(5.0)):
                    length += edge.length
        if length > 0 and (best is None or length > best[0]):
            best = (length, g)
    return None if best is None else best[1]


def sum_vecs(vs) -> V.Vec:
    x = y = z = 0.0
    for v in vs:
        x, y, z = x + v[0], y + v[1], z + v[2]
    return (x, y, z)


def _span(ctx: Context, f: int, d: V.Vec) -> float:
    """Width of planar face *f* across the sweep direction, in its plane."""
    fi = ctx.info(f)
    if fi.kind != "plane":
        return 0.0
    u = V.cross(fi.normal, d)
    if V.norm(u) < 1e-9:
        return 0.0
    lo, hi = extent(face_points(ctx.topo, f), V.unit(u))
    return hi - lo


def _facing(ctx: Context, f: int):
    fi = ctx.info(f)
    return fi.normal if fi.kind == "plane" else None


_BLENDS = (FeatureKind.FILLET, FeatureKind.CHAMFER)


def _void(ctx: Context, comp: list[int], vents: Optional[list] = None) -> Candidate:
    """A shell of faces meeting only each other: a cavity sealed inside the
    part (a slicer trick for extra perimeters, or a modelling error)."""
    pts = [p for f in comp for p in face_points(ctx.topo, f)]
    # measured across its own flat faces (world axes if it has none)
    dirs = [ctx.info(f).normal for f in comp if ctx.info(f).kind == "plane"] or \
        [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
    spans = [hi - lo for lo, hi in (extent(pts, d) for d in dirs)]
    size = (max(spans), min(spans))
    mid = V.mul(sum_vecs(ctx.info(f).centroid for f in comp), 1.0 / len(comp))
    score = Score(0.95)
    params = {"max_size": r3(size[0]), "min_size": r3(size[1]), "face_count": len(comp)}
    if vents:
        score = Score(0.85)
        dias = [float(v.params.get("diameter", 0.0)) for v in vents]
        score.ok(f"a cavity inside the part, open to the outside only through {len(vents)} small hole(s)")
        params.update({"escape_holes": len(vents), "min_escape_diameter": r3(min(dias)) if dias else 0.0})
    else:
        score.ok("closed cavity: its faces meet nothing but each other")
        params["escape_holes"] = 0
    return Candidate(kind=FeatureKind.VOID, subtype="vented" if vents else "internal", faces=list(comp),
                     support=sorted({f for v in (vents or []) for f in v.faces}),
                     params=params,
                     frame=Frame(vec3(mid), (0.0, 0.0, 1.0)), score=score)


def _stepped(ctx: Context, comp: list[int], d: V.Vec, walls: list[int], caps: list[int]):
    """A sweep whose walls stand at several depths (a nut trap with
    bridging layers, a counterbored pocket): one pocket or cutout if it
    opens onto the stock only at its ends along d. An end where it only
    meets other features (the bolt hole through a nut trap's floor) is
    pierced, not open."""
    cset = set(comp)
    pts = [p for f in comp for p in face_points(ctx.topo, f)]
    lo, hi = extent(pts, d)
    span = hi - lo
    if span <= 0:
        return None
    ends: dict[str, set[str]] = {"lo": set(), "hi": set()}
    rims: set[int] = set()
    for f in comp:
        for arc in ctx.aag.arcs(f):
            g = arc.other(f)
            if arc.convexity != "convex" or g in cset:
                continue
            via = arc.via if arc.virtual else g
            eps = [p for e in shared_edges(ctx.topo, f, via) for p in ctx.topo.edges[e].points]
            if not eps:
                continue
            t = sum(V.dot(p, d) for p in eps) / len(eps)
            if t <= lo + 0.15 * span:
                side = "lo"
            elif t >= hi - 0.15 * span:
                side = "hi"
            else:
                return None  # opens sideways: not a pocket
            owner = ctx.owner_candidate(g)
            ends[side].add("feature" if owner is not None and owner.kind not in _BLENDS else "stock")
            if "stock" in ends[side]:
                rims.add(g)
    open_ends = [k for k, v in ends.items() if "stock" in v]
    if not open_ends:
        return None
    levels = len({round(V.dot(ctx.info(c).origin, d), 6) for c in caps})
    score = Score(0.7)
    score.ok(f"walls along one direction at {levels + 1} depths")
    if len(open_ends) == 2:
        kind, subtype = FeatureKind.CUTOUT, "stepped"
        params = {"depth": r3(span), "levels": levels + 1, "walls": len(walls)}
        axis = d
    else:
        kind, subtype = FeatureKind.POCKET, "stepped"
        params = {"depth": r3(span), "levels": levels + 1, "walls": len(walls),
                  "pierced": bool(ends["lo" if open_ends == ["hi"] else "hi"])}
        axis = V.mul(d, 1.0 if open_ends == ["hi"] else -1.0)
        score.ok("opens on one side" + (", its floor pierced by another feature" if params["pierced"] else ""))
    mid = V.mul(sum_vecs(ctx.info(f).centroid for f in comp), 1.0 / len(comp))
    return Candidate(kind=kind, subtype=subtype, faces=list(comp), support=sorted(rims),
                     params=params, frame=Frame(vec3(mid), vec3(axis)), score=score)


def find_depressions(ctx: Context) -> list[Candidate]:
    out = []
    for comp in _components(ctx):
        cset = set(comp)
        outside = {a.other(f) for f in comp for a in ctx.aag.arcs(f)} - cset
        if not outside:
            out.append(_void(ctx, comp))
            continue
        owners = {ctx.owner_candidate(g).id: ctx.owner_candidate(g) for g in outside
                  if ctx.owner_candidate(g) is not None}
        if all(g in ctx.owner for g in outside) and owners and \
                all(o.kind in (FeatureKind.HOLE,) + _BLENDS for o in owners.values()) and \
                any(o.kind == FeatureKind.HOLE for o in owners.values()):
            out.append(_void(ctx, comp, [o for o in owners.values() if o.kind == FeatureKind.HOLE]))
            continue
        if len(comp) > _MAX_FACES:
            continue
        sw = _sweep(ctx, comp)
        if sw is None:
            continue
        d, walls, caps = sw
        big = max(ctx.info(f).area for f in comp)
        if any(sum(ctx.info(g).area for g in walls if g == f or ctx.aag.same_surface(f, g)) < 0.005 * big
               for f in walls):
            continue  # a sliver wall: modelling debris, not a feature side
        prof = _profile(ctx, walls)
        if prof is None or (prof[0] == "ring" and len(caps) > 1):
            cand = _stepped(ctx, comp, d, walls, caps)
            if cand is not None:
                out.append(cand)
            continue
        kind_p, order = prof
        cset = set(comp)
        # A depression's faces look into the void they bound; a protrusion's
        # (a block standing on a plate, seen as a "closed slot") look out.
        mid = V.mul(sum_vecs(ctx.info(f).centroid for f in comp), 1.0 / len(comp))
        tol = max(ctx.options.linear_tolerance * 10, 1e-6)
        if any(ctx.info(f).kind == "plane" and V.dot(ctx.info(f).normal, V.sub(mid, ctx.info(f).centroid)) < -tol
               for f in comp):
            continue
        pts = [p for f in comp for p in face_points(ctx.topo, f)]
        along = extent(pts, d)
        length = along[1] - along[0]
        score = Score(0.75)
        params = {"walls": len(order), "length": r3(length)}
        support: list[int] = []
        if kind_p == "ring":
            if len(caps) > 1:
                continue
            if caps:
                cap = caps[0]
                ci = ctx.info(cap)
                depth = max(V.dot(V.sub(p, ci.origin), ci.normal) for p in pts)
                kind, subtype = FeatureKind.POCKET, "flat"
                params = {"depth": r3(depth), "walls": len(order)}
                score.ok(f"{len(order)} walls round a floor")
                axis = ci.normal
            else:
                kind, subtype = FeatureKind.CUTOUT, "through"
                params = {"depth": r3(length), "walls": len(order)}
                score.ok(f"{len(order)} walls closing round an open passage")
                axis = d
            support = sorted({a.other(w) for w in walls for a in ctx.aag.arcs(w)
                              if a.convexity == "convex" and a.other(w) not in cset})
            frame = Frame(vec3(ctx.info(walls[0]).centroid), vec3(axis), vec3(ctx.info(walls[0]).normal)
                          if ctx.info(walls[0]).kind == "plane" else None)
        else:
            r1 = next((r for r in (rim_along(ctx, w, d, cset) for w in order[0]) if r is not None), None)
            r2 = next((r for r in (rim_along(ctx, w, d, cset) for w in order[-1]) if r is not None), None)
            if r1 is None or r2 is None:
                continue
            n1, n2 = _facing(ctx, r1), _facing(ctx, r2)
            if n1 is None or n2 is None:
                continue
            support = sorted({r1, r2})
            # Cut into stock, the faces it opens onto are broad. Two thin rims
            # are the edges of a sheet: an L-bracket's inside corner, not a step.
            reach = max(_span(ctx, w, d) for w in walls)
            if max(_span(ctx, r1, d), _span(ctx, r2, d)) < 0.15 * reach:
                continue
            same = r1 == r2 or ctx.aag.same_surface(r1, r2) or V.dot(n1, n2) > math.cos(math.radians(10.0))
            n_open = 2 - len(caps)
            if n_open < 0:
                continue
            if same:
                kind = FeatureKind.SLOT
                subtype = ("open", "open_end", "closed")[len(caps)]
                top = ctx.info(r1)
                depth = max(V.dot(V.sub(top.origin, p), n1) for p in pts)
                u = V.unit(V.cross(d, n1))
                wu = extent(pts, u)
                planar = [w for w in walls if ctx.info(w).kind == "plane"]
                shape = "rectangular" if len(order) == 3 and len(planar) == len(walls) else \
                    ("v" if len(order) == 2 and len(planar) == len(walls) else "polygonal")
                params = {"width": r3(wu[1] - wu[0]), "depth": r3(depth), "length": r3(length),
                          "through": False, "open_ends": n_open, "profile": shape}
                score.ok(f"{shape} groove of {len(order)} walls opening onto one face")
                frame = Frame(vec3(top.centroid), vec3(V.mul(n1, -1.0)), vec3(d))
            else:
                kind = FeatureKind.STEP
                subtype = "" if not caps else "blind"
                # depth from the first rim's plane, width from the second's
                a1, a2 = ctx.info(r1), ctx.info(r2)
                depth = max(V.dot(V.sub(a1.origin, p), n1) for p in pts)
                width = max(V.dot(V.sub(a2.origin, p), n2) for p in pts)
                params = {"depth": r3(depth), "width": r3(width), "length": r3(length),
                          "open_ends": n_open, "walls": len(order)}
                score.ok(f"{len(order)} walls cut into the edge between two faces")
                frame = Frame(vec3(ctx.info(order[0][0]).centroid), vec3(V.mul(n1, -1.0)), vec3(d))
            if caps:
                score.ok(f"closed by {len(caps)} end wall(s)")
        cand = Candidate(
            kind=kind, subtype=subtype, faces=list(comp), support=support,
            edges=sorted(e for w in comp for s in support for e in shared_edges(ctx.topo, w, s)),
            params=params, frame=frame, score=score,
        )
        out.append(cand)
    return out


def _surfaces(ctx: Context, faces) -> list[int]:
    reps: list[int] = []
    for f in faces:
        if not any(ctx.aag.same_surface(r, f) for r in reps):
            reps.append(f)
    return reps


def merge_split_passages(ctx: Context) -> None:
    """One passage crossed by another comes out in pieces (one each side
    of the other), each a cutout of its own. Pieces whose walls lie on the
    same surfaces are one passage: join them. Runs on accepted cutouts."""
    cuts = [c for c in ctx.accepted if c.kind == FeatureKind.CUTOUT]
    gone: set[int] = set()
    for i, a in enumerate(cuts):
        if a.id in gone:
            continue
        sa = _surfaces(ctx, a.faces)
        for b in cuts[i + 1:]:
            if b.id in gone:
                continue
            sb = _surfaces(ctx, b.faces)
            if len(sa) != len(sb) or not all(any(ctx.aag.same_surface(x, y) for y in sb) for x in sa):
                continue
            a.faces = sorted(set(a.faces) | set(b.faces))
            a.support = sorted((set(a.support) | set(b.support)) - set(a.faces))
            a.edges = sorted(set(a.edges) | set(b.edges))
            for f in b.faces:
                ctx.owner[f] = a.id
            if a.frame is not None:
                pts = [p for f in a.faces for p in face_points(ctx.topo, f)]
                lo, hi = extent(pts, a.frame.axis)
                for key in ("depth", "thickness"):
                    if key in a.params:
                        a.params[key] = r3(hi - lo)
            a.score.ok("one passage, crossed by another")
            gone.add(b.id)
    if gone:
        ctx.accepted = [c for c in ctx.accepted if c.id not in gone]


def find_saddles(ctx: Context) -> list[Candidate]:
    """Round cradles: part-cylinders (less than a hole's worth of circle)
    cut across one or more walls on one axis, such as a channel a rod or
    antenna lies in. The pieces on thin walls are already the curved tips of
    ribs; the rest are the saddle, which lists the rib tips as support."""
    from .holes import _coverage

    tol = max(ctx.options.linear_tolerance * 10, 1e-6, 1e-6 * ctx.topo.scale)
    pieces = [f for f in ctx.aag.faces if ctx.info(f).kind == "cylinder" and ctx.info(f).internal
              and ctx.info(f).span < 359.0]
    groups: list[list[int]] = []
    for f in pieces:
        fi = ctx.info(f)
        for g in groups:
            gi = ctx.info(g[0])
            if abs(fi.radius - gi.radius) <= tol and abs(abs(V.dot(fi.axis, gi.axis)) - 1.0) < 1e-6 and \
                    V.point_line_distance(fi.origin, gi.origin, gi.axis) <= tol:
                g.append(f)
                break
        else:
            groups.append([f])
    out = []
    for g in groups:
        free = [f for f in g if f not in ctx.owner]
        if not free:
            continue
        ribbed = [f for f in g if f in ctx.owner and ctx.owner_candidate(f) is not None
                  and ctx.owner_candidate(f).kind == FeatureKind.RIB]
        if len(free) + len(ribbed) < 2 and not ribbed:
            # a lone part-cylinder: only a cradle if it is a channel across a wall
            fi = ctx.info(free[0])
            if fi.span > 200.0:
                continue
        ref = ctx.info(g[0])
        coverage = _coverage(ctx, g, ref.radius, ref.origin, ref.axis)
        if coverage >= ctx.options.min_hole_span:
            continue
        pts = [p for f in g for p in face_points(ctx.topo, f)]
        lo, hi = extent(pts, ref.axis)
        score = Score(0.8)
        score.ok(f"part-cylinders Ø{2 * ref.radius:.3g} over {coverage:.0f}° on one axis, across "
                 f"{len(g)} wall(s)")
        if ribbed:
            score.ok(f"{len(ribbed)} of them the curved tips of ribs")
        out.append(Candidate(
            kind=FeatureKind.SLOT, subtype="saddle", faces=sorted(free),
            support=sorted(ribbed),
            params={"diameter": r3(2 * ref.radius), "span_deg": round(coverage, 1), "length": r3(hi - lo),
                    "walls": len(g), "rib_tips": len(ribbed), "profile": "round"},
            frame=Frame(vec3(ref.origin), vec3(ref.axis)), score=score))
    return out
