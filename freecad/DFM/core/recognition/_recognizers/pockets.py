# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Pockets, cutouts and the residual depressions / protrusions.

Both start from an *inner loop* of a face (the face the feature opens
into, or stands on), the way Joshi & Chang separate feature regions from
stock faces:

* all edges of the loop convex: a depression opens there. The faces
  behind it are flooded (never back through the loop's face, never across
  an island's loop, stopping at recognised features). If the flood comes
  out at another face's inner loop, it is a CUTOUT (a window through the
  part); if it ends on a floor, a POCKET; otherwise a DEPRESSION.
* all edges concave: a protrusion stands there (a PROTRUSION, if no
  specific recognizer took it).
"""

from __future__ import annotations

from ..model import FeatureKind, Frame
from .._topology import vec as V
from .common import Candidate, Context, Score, extent, face_points, r3, vec3

_MAX_REGION = 400


def _flood(ctx: Context, rim: int, loop: list[int], depression: bool):
    """(region faces, other rims reached, open?, leaked?)"""
    aag = ctx.aag
    rim_outer = set(ctx.info(rim).outer)
    region: list[int] = []
    other_rims: set[int] = set()
    is_open = False
    stack = []
    for e in loop:
        for y in aag.across(rim, e):
            if y not in ctx.owner and y != rim:
                stack.append(y)
    seen = set(stack)
    while stack:
        x = stack.pop()
        region.append(x)
        if len(region) > _MAX_REGION:
            return region, other_rims, True, True
        xi = ctx.info(x)
        for e in xi.outer:
            cls = aag.edge_class(x, e)
            for y in aag.across(x, e):
                if y == rim:
                    if e in rim_outer:
                        return region, other_rims, True, True  # wraps round the rim face: leaked
                    continue
                if y in seen:
                    continue
                if y in ctx.owner:
                    continue
                yi = ctx.info(y)
                if any(e in w for w in yi.inner):
                    other_rims.add(y)
                    continue
                if depression and cls == "convex":
                    is_open = True
                    continue
                seen.add(y)
                stack.append(y)
    return sorted(region), other_rims, is_open, False


def _loop_class(ctx: Context, face: int, loop: list[int]) -> str:
    classes = {ctx.aag.edge_class(face, e) for e in loop}
    classes.discard("seam")
    if classes and classes <= {"convex"}:
        return "convex"
    if classes and classes <= {"concave"}:
        return "concave"
    return "mixed"


def _obround(ctx: Context, walls: list[int]) -> bool:
    """Two flat walls facing each other joined by two half-cylinders of
    half their spacing: an obround slot."""
    planes = [w for w in walls if ctx.info(w).kind == "plane"]
    cyls = [w for w in walls if ctx.info(w).kind == "cylinder"]
    if len(planes) != 2 or len(cyls) != 2 or len(walls) != 4:
        return False
    a, b = (ctx.info(w) for w in planes)
    if V.dot(a.normal, b.normal) > -0.999:
        return False
    width = abs(V.dot(V.sub(b.origin, a.origin), a.normal))
    return all(ctx.info(c).internal and abs(2.0 * ctx.info(c).radius - width) <= 0.02 * width for c in cyls)


def _region_pts(ctx: Context, region) -> list:
    return [p for f in region for p in face_points(ctx.topo, f)]


def find_pockets(ctx: Context, *, residual: bool = False) -> list[Candidate]:
    """Pockets and cutouts; with residual=True, the left-over depressions
    and protrusions."""
    out = []
    claimed: set[int] = set()
    for r in list(ctx.aag.faces):
        ri = ctx.info(r)
        for loop in ri.inner:
            cls = _loop_class(ctx, r, loop)
            if cls == "mixed":
                continue
            depression = cls == "convex"
            if not residual and not depression:
                continue
            region, rims, is_open, leaked = _flood(ctx, r, loop, depression)
            region = [f for f in region if f not in claimed]
            if leaked or not region:
                continue
            pts = _region_pts(ctx, region)
            normal = ri.normal if ri.kind == "plane" else ri.sample_normal
            if normal is None:
                continue
            ref = ri.origin if ri.kind == "plane" else ri.sample_point
            dd = extent(pts, normal, ref)
            if depression and not residual:
                if is_open:
                    continue
                if rims:
                    kind, subtype = FeatureKind.CUTOUT, "through"
                    depth = max(abs(dd[0]), abs(dd[1]))
                    floor = None
                else:
                    floors = [f for f in region if ctx.info(f).kind == "plane"
                              and V.dot(ctx.info(f).normal, normal) > 0.99]
                    floor = max(floors, key=lambda f: ctx.info(f).area) if floors else None
                    if floor is None and ri.kind != "plane":
                        floors = [f for f in region if ctx.info(f).kind == "plane"]
                        floor = max(floors, key=lambda f: ctx.info(f).area) if floors else None
                    kind, subtype = FeatureKind.POCKET, "flat" if floor else "general"
                    depth = -dd[0]
                    if ri.kind != "plane" and floor is not None:
                        fl = ctx.info(floor)
                        loop_pts = [p for e in loop for p in ctx.topo.edges[e].points]
                        depth = max(V.dot(V.sub(p, fl.origin), fl.normal) for p in loop_pts)
                walls = [f for f in region if f != floor]
                if _obround(ctx, walls):
                    continue  # a slot with rounded ends: the slot recognizer's
                if walls and all(ctx.info(w).kind == "plane" for w in walls):
                    shape = "rectangular" if len(walls) == 4 else "polygonal"
                elif walls and all(ctx.info(w).kind in ("plane", "cylinder") for w in walls):
                    shape = "rounded"
                else:
                    shape = "general"
                corner = [b.radius for b in ctx.aag.hidden.values()
                          if b.convexity == "concave" and len(set(b.supports) & set(walls)) >= 2]
                tapers = [abs(90.0 - V.angle(ctx.info(w).normal, normal)) for w in walls
                          if ctx.info(w).kind == "plane"]
                score = Score(0.88)
                score.ok(f"closed ring of {len(walls)} wall(s) behind a loop of convex edges")
                if kind == FeatureKind.CUTOUT:
                    score.ok("comes out through another face: a window")
                elif floor:
                    score.ok("flat floor")
                params = {
                    "depth": r3(depth), "shape": shape, "wall_count": len(walls),
                    "min_corner_radius": r3(min(corner)) if corner else 0.0,
                    "wall_taper_deg": r3(max(tapers)) if tapers else 0.0,
                }
                if floor:
                    params["floor_area"] = r3(ctx.info(floor).area)
                if kind == FeatureKind.CUTOUT:
                    params["thickness"] = params.pop("depth")
                support = [r] + sorted(rims)
            else:
                if is_open or (rims and not depression):
                    continue  # spans between two faces (a groove floor), not free-standing
                if depression:
                    kind = FeatureKind.DEPRESSION
                    params = {"depth": r3(-dd[0])}
                else:
                    kind = FeatureKind.PROTRUSION
                    params = {"height": r3(dd[1])}
                subtype = "block" if all(ctx.info(f).kind == "plane" for f in region) else "general"
                params["face_count"] = len(region)
                score = Score(0.5)
                score.ok("region behind a loop of " + ("convex" if depression else "concave") + " edges")
                score.penalize(0.9, "no specific feature recognised")
                support = [r] + sorted(rims)
            claimed.update(region)
            if kind in (FeatureKind.POCKET, FeatureKind.CUTOUT):
                params["shape"] = params.get("shape", "general")
            out.append(Candidate(
                kind=kind, subtype=subtype, faces=region, support=support, edges=sorted(loop),
                params=params, frame=Frame(vec3(ri.sample_point or ri.centroid), vec3(V.mul(normal, -1.0 if depression else 1.0))),
                score=score,
            ))
    if not residual:
        out.extend(_floor_pockets(ctx, claimed))
    return out


def _floor_pockets(ctx: Context, claimed: set[int]) -> list[Candidate]:
    """Pockets found from the floor: a planar face whose whole outer loop
    meets walls concavely, the walls opening convexly onto the rim. Catches
    pockets whose opening is not an inner loop of one face (a keyway in a
    shaft, a pocket across a seam)."""
    aag = ctx.aag
    out = []
    for f in aag.faces:
        fi = ctx.info(f)
        if f in ctx.owner or f in claimed or fi.kind != "plane" or len(fi.outer) < 3:
            continue
        if any(aag.edge_class(f, e) != "concave" for e in fi.outer):
            continue
        walls: set[int] = set()
        for e in fi.outer:
            walls |= aag.across(f, e)
        walls.discard(f)
        if not walls or any(w in ctx.owner or w in claimed for w in walls):
            continue
        region = walls | {f}
        rims: set[int] = set()
        closed = True
        for w in walls:
            for e in ctx.info(w).outer:
                cls = aag.edge_class(w, e)
                for y in aag.across(w, e):
                    if y in region:
                        continue
                    if cls == "convex":
                        rims.add(y)
                    else:
                        closed = False
        if not closed or not rims or _obround(ctx, sorted(walls)):
            continue
        depth = max(V.dot(V.sub(p, fi.origin), fi.normal) for w in walls for p in face_points(ctx.topo, w))
        corner = [b.radius for b in aag.hidden.values()
                  if b.convexity == "concave" and len(set(b.supports) & walls) >= 2]
        tapers = [abs(90.0 - V.angle(ctx.info(w).normal, fi.normal)) for w in walls if ctx.info(w).kind == "plane"]
        score = Score(0.85)
        score.ok(f"floor closed all round by {len(walls)} wall(s)")
        claimed.update(region)
        out.append(Candidate(
            kind=FeatureKind.POCKET, subtype="flat", faces=sorted(region), support=sorted(rims),
            edges=sorted(fi.outer),
            params={"depth": r3(depth), "shape": "rectangular" if len(walls) == 4 and all(
                        ctx.info(w).kind == "plane" for w in walls) else "general",
                    "wall_count": len(walls), "min_corner_radius": r3(min(corner)) if corner else 0.0,
                    "wall_taper_deg": r3(max(tapers)) if tapers else 0.0, "floor_area": r3(fi.area)},
            frame=Frame(vec3(fi.centroid), vec3(V.mul(fi.normal, -1.0))), score=score,
        ))
    return out


def claim_rib_walls(ctx: Context) -> None:
    """A thin wall between a pocket and the next cavity is a rib, and its
    side facing the pocket is also that pocket's wall. Ribs are found
    first, so the pocket comes out missing that wall. Give it back: the
    pocket owns its whole wall loop, and the rib keeps the side as a face
    it stands by (support). A rib keeps at least one side of its own."""
    from .._topology import vec as V

    ribs = {c.id: c for c in ctx.accepted if c.kind == FeatureKind.RIB}
    if not ribs:
        return
    for pocket in [c for c in ctx.accepted if c.kind == FeatureKind.POCKET]:
        floors = [f for f in pocket.faces if ctx.info(f).kind == "plane" and pocket.frame is not None
                  and abs(V.dot(ctx.info(f).normal, pocket.frame.axis)) > 0.99]
        if not floors:
            continue
        floor = max(floors, key=lambda f: ctx.info(f).area)
        fn = ctx.info(floor).normal
        mine = set(pocket.faces)
        for arc in ctx.aag.arcs(floor):
            w = arc.other(floor)
            rib = ribs.get(ctx.owner.get(w))
            if rib is None or arc.convexity != "concave" or ctx.info(w).kind != "plane":
                continue
            if abs(V.dot(ctx.info(w).normal, fn)) > 0.2:
                continue
            # part of this pocket's wall loop: it meets the pocket's other walls
            if not (ctx.aag.neighbors(w) & (mine - {floor})):
                continue
            if len(rib.faces) <= 1 or not (set(rib.faces) - {w}) & set(rib.links.get("sides", ())):
                continue
            # A rib standing inside the pocket (on its floor both sides) is
            # in the pocket, not one of its walls.
            others = set(rib.links.get("sides", ())) - {w}
            if any(ctx.aag.neighbors(o) & mine for o in others):
                continue
            rib.faces = [f for f in rib.faces if f != w]
            rib.support = sorted(set(rib.support) | {w})
            rib.interacts.add(pocket.id)
            pocket.interacts.add(rib.id)
            pocket.faces = sorted(set(pocket.faces) | {w})
            mine.add(w)
            ctx.owner[w] = pocket.id
            pocket.score.ok(f"Face{w} is also the side of a rib")
