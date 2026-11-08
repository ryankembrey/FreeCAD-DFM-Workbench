# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Moulding and printing details.

Recognizers, run with the others:

* ``find_snap_fits``: a cantilever beam (two flat sides, drafted or
  not, rising from a base) with a hook at its tip. The hook's catch face
  looks back towards the base.
* ``find_lips``: a thin wall standing round a housing's rim, to locate
  the mating half.
* ``find_ring_grooves``: a closed groove all round: a lip's mating
  groove, an O-ring or face groove. It has a floor ring with walls on both
  of its loops.

Passes over the accepted features, run after all recognizers:

* ``crush_ribs``: small ribs standing inside a bore to grip an insert.
* ``living_hinges``: a groove leaving a web thinner than
  ``max_hinge_thickness``, from one side or both.
* ``vents``: at least ``min_vent_count`` like openings through one wall,
  evenly spaced.
* ``text``: at least ``min_text_glyphs`` small, shallow, thin-stroked
  shapes on one face, engraved or embossed.
* ``nut_traps``: a hexagonal pocket or hole with a bolt hole on its
  axis; a slot a bolt hole crosses (a nut slot).
* ``snap_windows``: the opening under a hook that lets the mould form it.

Moulded details are drafted, so faces are matched within ``max_draft``
(walls) or ``max_rib_draft`` (beams) instead of exactly.
"""

from __future__ import annotations

import math
from typing import Optional

from ..model import FeatureKind, Frame
from .._topology import vec as V
from .common import Candidate, Context, Score, extent, face_points, r3, vec3

_BLENDS = (FeatureKind.FILLET, FeatureKind.CHAMFER)


# -- Small helpers --------------------------------------------------------------------------


def _plane(ctx: Context, f: int):
    fi = ctx.info(f)
    return fi if fi.kind == "plane" else None


def _upright(n: V.Vec, h: V.Vec, max_draft: float) -> bool:
    """A wall along *h*: its normal within *max_draft* of square to it."""
    return abs(V.dot(n, h)) <= math.sin(math.radians(max_draft)) + 1e-9


def _level(ctx: Context, f: int, h: V.Vec) -> tuple[float, float]:
    """(lowest, highest) of a face along *h*; cached, as the snap and lip
    searches ask about the same faces over and over."""
    cache = getattr(ctx, "_levels", None)
    if cache is None:
        cache = ctx._levels = {}
    key = (f, round(h[0], 9), round(h[1], 9), round(h[2], 9))
    got = cache.get(key)
    if got is None:
        got = cache[key] = extent(face_points(ctx.topo, f), h)
    return got


def ray_hits(ctx: Context, origin: V.Vec, direction: V.Vec) -> list[tuple[float, int]]:
    """(distance, face) where a ray leaves or enters the solid, nearest first."""
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

    inter = getattr(ctx, "_ray", None)
    if inter is None:
        inter = IntCurvesFace_ShapeIntersector()
        inter.Load(ctx.topo.shape, 1e-6)
        ctx._ray = inter
    inter.Perform(gp_Lin(gp_Pnt(*origin), gp_Dir(*direction)), 0.0, 1e100)
    out = []
    if inter.IsDone():
        for i in range(1, inter.NbPnt() + 1):
            out.append((inter.WParameter(i), ctx.topo.face_index.index_of(inter.Face(i))))
    return sorted(out)


def _take(ctx: Context, gone: list[Candidate], new: Candidate) -> None:
    """Replace accepted candidates *gone* by *new*, which owns their faces."""
    ids = {c.id for c in gone}
    ctx.accepted = [c for c in ctx.accepted if c.id not in ids]
    for f, cid in list(ctx.owner.items()):
        if cid in ids:
            del ctx.owner[f]
    for c in ctx.accepted:
        c.interacts -= ids
        if c.parent in ids:
            c.parent = None
    ctx.accept(new)


# =============================================================================================
# Snap fits
# =============================================================================================


def find_snap_fits(ctx: Context) -> list[Candidate]:
    opt = ctx.options
    aag = ctx.aag
    out: list[Candidate] = []
    used: set[int] = set()
    planes = [f for f in aag.faces if f not in ctx.owner and ctx.info(f).kind == "plane"]
    for c in planes:                       # the catch face, under the hook
        if c in used:
            continue
        ci = ctx.info(c)
        for arc in aag.arcs(c):
            b = arc.other(c)
            bi = _plane(ctx, b)
            if bi is None or b in ctx.owner or b in used or arc.convexity != "concave":
                continue
            # B: the beam's hook-side face; it rises from a base it meets in a valley
            if abs(V.dot(bi.normal, ci.normal)) > 0.2:
                continue
            h = V.mul(ci.normal, -1.0)              # up the beam, away from the base
            bases = [a2.other(b) for a2 in aag.arcs(b) if a2.convexity == "concave" and a2.other(b) != c
                     and _plane(ctx, a2.other(b)) is not None
                     and V.dot(ctx.info(a2.other(b)).normal, h) > 0.9]
            if not bases:
                continue
            base = max(bases, key=lambda g: ctx.info(g).area)
            if not _upright(bi.normal, h, opt.max_rib_draft):
                continue
            # A: the back of the beam, facing away from B, rising from the same base level
            a_face = None
            for f in planes:
                if f in (b, c) or f in used:
                    continue
                ai = ctx.info(f)
                if V.dot(ai.normal, bi.normal) > -math.cos(math.radians(2 * opt.max_rib_draft)):
                    continue
                # cheap first: B's middle is within a beam's thickness behind A
                tc = -V.dot(V.sub(bi.centroid, ai.origin), ai.normal)
                if not (0 < tc <= 2.0 * opt.max_snap_thickness):
                    continue
                if not (aag.neighbors(f, "concave") & set(bases)) and not any(
                        abs(_level(ctx, g, h)[1] - _level(ctx, base, h)[1]) < 1e-3
                        for g in aag.neighbors(f, "concave") if _plane(ctx, g) is not None):
                    continue
                # measured at the root (both faces may lean with draft)
                root = min(face_points(ctx.topo, b), key=lambda p: V.dot(p, h), default=bi.origin)
                t = V.dot(V.sub(root, ai.origin), ai.normal) * -1.0
                if not (0 < t <= opt.max_snap_thickness):
                    continue
                # A and B face each other's backs over the same span
                ua = extent(face_points(ctx.topo, f), V.unit(V.cross(h, bi.normal)))
                ub = extent(face_points(ctx.topo, b), V.unit(V.cross(h, bi.normal)))
                if min(ua[1], ub[1]) - max(ua[0], ub[0]) < 0.5 * (ub[1] - ub[0]):
                    continue
                a_face = (f, t)
                break
            if a_face is None:
                continue
            a, t_root = a_face
            base_lvl = _level(ctx, base, h)[1]
            catch_lvl = sum(V.dot(p, h) for p in face_points(ctx.topo, c)) / max(1, len(face_points(ctx.topo, c)))
            length = catch_lvl - base_lvl
            if length < 2.0 * t_root or t_root < 0.3:
                continue                            # a stub (or a sliver), not a flexing beam
            # The hook: faces above the catch joined to it, up to and with the beam's top
            hook = {c}
            stack = [c]
            while stack:
                f = stack.pop()
                for arc2 in aag.arcs(f):
                    g = arc2.other(f)
                    if g in hook or g in (a, b) or g in bases or g in ctx.owner:
                        continue
                    lo, _hi = _level(ctx, g, h)
                    if lo < catch_lvl - 1e-6:
                        continue
                    hook.add(g)
                    stack.append(g)
                if len(hook) > 8:
                    break                           # the rest of the part, not a hook
            if not (aag.neighbors(a) & hook):
                continue                            # the beam's back must reach the hook's top
            if len(hook) > 8:
                continue
            # a free end: the hook touches only the beam (its back, front and two ends)
            ends = (aag.neighbors(a) & aag.neighbors(b)) - hook - set(bases)
            if any(aag.neighbors(f) - hook - {a, b} - ends for f in hook):
                continue
            u = V.unit(V.cross(h, bi.normal))
            width = (lambda e: e[1] - e[0])(extent(face_points(ctx.topo, b), u))
            overhang = (lambda e: e[1] - e[0])(extent(face_points(ctx.topo, c), bi.normal))
            top_lvl = max(_level(ctx, f, h)[1] for f in hook)
            # the beam's faces run from the base to the hook; the hook is short
            blo, bhi = _level(ctx, b, h)
            if bhi - blo < 0.8 * length or top_lvl - catch_lvl > length + 3.0 * t_root:
                continue
            if overhang > length or width < t_root:
                continue                            # a beam is at least as wide as it is thick
            # Lead-in: the face of the hook leaning out from the tip
            lead_in = None
            for f in hook - {c}:
                fi = ctx.info(f)
                if fi.kind == "plane" and V.dot(fi.normal, bi.normal) > 0.05 and V.dot(fi.normal, h) > 0.05:
                    lead_in = math.degrees(math.atan2(V.dot(fi.normal, h), V.dot(fi.normal, bi.normal)))
            # thickness at the tip: A to B just under the catch
            t_tip = t_root
            if abs(V.dot(bi.normal, ctx.info(a).normal)) < 0.99999:
                mid = V.add(bi.centroid, V.mul(h, catch_lvl - V.dot(bi.centroid, h) - 1e-3))
                hits = ray_hits(ctx, V.add(mid, V.mul(bi.normal, 1e-4)), V.mul(bi.normal, -1.0))
                if hits:
                    t_tip = hits[0][0] if len(hits) == 1 else hits[1][0] - hits[0][0]
            score = Score(0.9)
            score.ok(f"beam {t_root:.3g} thick, {length:.3g} long, rising from Face{base}")
            score.ok(f"hook overhangs {overhang:.3g} with its catch face looking back at the base")
            if lead_in is not None:
                score.ok(f"lead-in face at {lead_in:.0f}°")
            draft = math.degrees(math.acos(max(-1.0, min(1.0, -V.dot(bi.normal, ctx.info(a).normal))))) / 2.0
            params = {"thickness": r3(t_root), "thickness_tip": r3(t_tip), "length": r3(length),
                      "width": r3(width), "overhang": r3(overhang), "height": r3(top_lvl - base_lvl),
                      "length_to_thickness": r3(length / t_root), "draft_deg": round(draft, 3)}
            if lead_in is not None:
                params["lead_in_deg"] = round(lead_in, 2)
            ends = {e for e in ends if e not in ctx.owner and e not in used}
            used.update({a, b} | hook | ends)
            out.append(Candidate(
                kind=FeatureKind.SNAP_FIT, subtype="cantilever", faces=sorted({a, b} | hook | ends),
                support=sorted(set(bases)), params=params,
                frame=Frame(vec3(bi.centroid), vec3(h), vec3(bi.normal)), score=score))
            break
    return out


def snap_windows(ctx: Context) -> None:
    """The through opening in the base right under a hook: it is how the
    mould forms the catch without a side action. Linked, not merged."""
    snaps = [c for c in ctx.accepted if c.kind == FeatureKind.SNAP_FIT]
    openings = [c for c in ctx.accepted if c.kind in (FeatureKind.CUTOUT, FeatureKind.HOLE, FeatureKind.SLOT)
                and c.params.get("through", c.kind == FeatureKind.CUTOUT)]
    for s in snaps:
        h = s.frame.axis
        n = s.frame.ref
        catch = [f for f in s.faces if ctx.info(f).kind == "plane" and V.dot(ctx.info(f).normal, h) < -0.9]
        if not catch:
            continue
        cp = face_points(ctx.topo, catch[0])
        for o in openings:
            op = [p for f in o.faces for p in face_points(ctx.topo, f)]
            hit = True
            for ax in (n, V.unit(V.cross(h, n))):
                a, b = extent(cp, ax), extent(op, ax)
                if min(a[1], b[1]) - max(a[0], b[0]) <= 1e-6:
                    hit = False
            if hit and extent(op, h)[0] < extent(cp, h)[0]:
                s.params["mould_window"] = True
                s.interacts.add(o.id)
                o.interacts.add(s.id)
                s.score.ok(f"an opening under the hook lets the mould form it ({o.kind.value} {o.id})")
                break
        else:
            s.params.setdefault("mould_window", False)


# =============================================================================================
# Lips and ring grooves
# =============================================================================================


def _ring(ctx: Context, f: int):
    """(outer loop faces, inner loop faces) of a planar face with exactly one hole in it."""
    fi = ctx.info(f)
    if fi.kind != "plane" or len(fi.inner) != 1:
        return None
    outer = [(e, ctx.topo.edges[e]) for e in fi.outer if ctx.topo.edges[e] is not None]
    inner = [(e, ctx.topo.edges[e]) for e in fi.inner[0] if ctx.topo.edges[e] is not None]
    return outer, inner


def _loop_faces(ctx: Context, f: int, loop) -> Optional[tuple[set[int], str]]:
    """Faces across a loop and the loop's convexity (all the same), or None."""
    faces, kinds = set(), set()
    for e, edge in loop:
        cls = ctx.aag.edge_class(f, e)
        if cls in ("smooth", "seam"):
            continue
        kinds.add(cls)
        faces |= ctx.aag.across(f, e)
    if len(kinds) != 1 or not faces:
        return None
    return faces, kinds.pop()


def find_lips(ctx: Context) -> list[Candidate]:
    """A thin wall standing round a rim. Its top T is a ring (one hole)
    with convex edges all round. On one side, short walls W drop at most
    ``max_lip_height`` to a ring P parallel to T (the rest of the rim). On
    the other, the wall goes on down (the housing's wall)."""
    opt = ctx.options
    out = []
    for t in ctx.aag.faces:
        if t in ctx.owner:
            continue
        ring = _ring(ctx, t)
        if ring is None:
            continue
        ti = ctx.info(t)
        h = ti.normal
        sides = []
        for loop in ring:
            lf = _loop_faces(ctx, t, loop)
            if lf is None or lf[1] != "convex":
                break
            sides.append(lf[0])
        if len(sides) != 2:
            continue
        top = _level(ctx, t, h)[1]
        for k, walls in enumerate(sides):
            other = sides[1 - k]
            if any(w in ctx.owner or ctx.info(w).kind not in ("plane", "cylinder", "cone") for w in walls):
                continue
            if any(ctx.info(w).kind == "plane" and not _upright(ctx.info(w).normal, h, opt.max_draft) for w in walls):
                continue
            # the walls drop to one ring face parallel to the top
            floors = set()
            for w in walls:
                for arc in ctx.aag.arcs(w):
                    g = arc.other(w)
                    if arc.convexity == "concave" and g not in walls and g != t and _plane(ctx, g) is not None \
                            and V.dot(ctx.info(g).normal, h) > 0.999:
                        floors.add(g)
            if len(floors) != 1:
                continue
            p = next(iter(floors))
            # the rim is itself a ring: the lip stands on it, it doesn't
            # enclose it (walls round a floor are a pocket)
            if _ring(ctx, p) is None:
                continue
            height = top - _level(ctx, p, h)[1]
            if not (0 < height <= opt.max_lip_height):
                continue
            # the other side keeps going down past the rim
            if min(_level(ctx, w, h)[0] for w in other) > top - height - 1e-6:
                continue
            # thickness: the top ring's width
            perim = sum(ctx.topo.edges[e].length for e in ti.edges if ctx.topo.edges[e] is not None)
            thickness = 2.0 * ti.area / max(perim, 1e-9)
            if thickness > 0.5 * height + 4.0 and thickness > 2.0 * height:
                continue                    # a wide step, not a lip
            if perim / 2.0 < 15.0 * thickness:
                continue                    # a short collar (a boss end, a nut seat), not a rim lip
            side = "outer" if k == 0 else "inner"
            score = Score(0.85)
            score.ok(f"a wall {thickness:.3g} thick standing {height:.3g} above the rim (Face{p}), all round")
            out.append(Candidate(
                kind=FeatureKind.LIP, subtype=side, faces=sorted(set(walls) | {t}), support=[p],
                params={"height": r3(height), "thickness": r3(thickness), "length": r3(perim / 2.0),
                        "position": side},
                frame=Frame(vec3(ti.centroid), vec3(h)), score=score))
            break
    return out


def find_ring_grooves(ctx: Context) -> list[Candidate]:
    """A groove closed on itself: a floor ring F whose two loops both meet
    walls in valleys, the walls rising to the face the groove is cut into.
    A lip's mating groove, an O-ring or face seal groove."""
    opt = ctx.options
    out = []
    for f in ctx.aag.faces:
        if f in ctx.owner:
            continue
        ring = _ring(ctx, f)
        if ring is None:
            continue
        fi = ctx.info(f)
        h = fi.normal
        sides = []
        for loop in ring:
            lf = _loop_faces(ctx, f, loop)
            if lf is None or lf[1] != "concave":
                break
            sides.append(lf[0])
        if len(sides) != 2:
            continue
        walls = sides[0] | sides[1]
        if any(w in ctx.owner for w in walls):
            continue
        if any(ctx.info(w).kind == "plane" and not _upright(ctx.info(w).normal, h, opt.max_draft) for w in walls):
            continue
        if any(ctx.info(w).kind not in ("plane", "cylinder", "cone", "torus", "bspline") for w in walls):
            continue
        # the walls rise to rim faces (convex), parallel to the floor
        rims = set()
        for w in walls:
            for arc in ctx.aag.arcs(w):
                g = arc.other(w)
                if arc.convexity == "convex" and g not in walls and g != f:
                    rims.add(g)
        if not rims:
            continue
        floor = _level(ctx, f, h)[1]
        depth = max(_level(ctx, w, h)[1] for w in walls) - floor
        perim = sum(ctx.topo.edges[e].length for e in fi.edges if ctx.topo.edges[e] is not None)
        width = 2.0 * fi.area / max(perim, 1e-9)
        if width <= 0 or depth <= 0:
            continue
        if width > 0.15 * perim / 2.0 or not _constant_width(ctx, ring):
            continue                    # a pocket round an island, not a groove
        score = Score(0.85)
        score.ok(f"a groove {width:.3g} wide, {depth:.3g} deep, closed on itself")
        params = {"width": r3(width), "depth": r3(depth), "length": r3(perim / 2.0), "open_ends": 0,
                  "through": False, "profile": "ring"}
        circ = [ctx.info(w) for w in walls if ctx.info(w).kind in ("cylinder", "cone")]
        if circ and len(circ) == len(walls):
            params["inner_diameter"] = r3(2 * min(c.r_min for c in circ))
            params["outer_diameter"] = r3(2 * max(c.r_max for c in circ))
            params["width"] = r3((params["outer_diameter"] - params["inner_diameter"]) / 2.0)
        out.append(Candidate(
            kind=FeatureKind.SLOT, subtype="ring", faces=sorted(walls | {f}), support=sorted(rims),
            params=params, frame=Frame(vec3(fi.centroid), vec3(V.mul(h, -1.0))), score=score))
    return out


def _constant_width(ctx: Context, ring, ratio: float = 1.3) -> bool:
    """Whether the two loops of a ring stay about the same distance apart."""
    outer = [p for _e, edge in ring[0] for p in edge.points]
    inner = [p for _e, edge in ring[1] for p in edge.points]
    if len(outer) < 2 or not inner:
        return False
    segs = [(edge.points[i], edge.points[i + 1]) for _e, edge in ring[0] for i in range(len(edge.points) - 1)]

    def dist(p):
        best = float("inf")
        for a, b in segs:
            ab = V.sub(b, a)
            t = max(0.0, min(1.0, V.dot(V.sub(p, a), ab) / max(V.dot(ab, ab), 1e-18)))
            best = min(best, V.norm(V.sub(p, V.add(a, V.mul(ab, t)))))
        return best

    d = [dist(p) for p in inner]
    return min(d) > 1e-9 and max(d) <= ratio * min(d)


# =============================================================================================
# Passes over the accepted features
# =============================================================================================


def find_crush_ribs(ctx: Context) -> list[Candidate]:
    """Run as a recognizer, right after holes (before pockets can take
    the ribs and the bore's floor)."""
    crush_ribs(ctx)
    return []


def crush_ribs(ctx: Context) -> None:
    """Small ribs standing inside a bore, gripping an insert or pin. Each
    becomes a rib (``crush``) whose parent is the hole. A floor that only
    the bore and its ribs stand on is the hole's bottom."""
    for hole in [c for c in ctx.accepted if c.kind == FeatureKind.HOLE]:
        if hole.frame is None:
            continue
        axis = hole.frame.axis
        origin = hole.frame.origin
        bore = [f for f in hole.faces if ctx.info(f).kind in ("cylinder", "cone") and ctx.info(f).internal]
        if not bore:
            continue
        r = min(0.5 * (ctx.info(f).r_min + ctx.info(f).r_max) for f in bore)
        seeds = set()
        for f in bore:
            for arc in ctx.aag.arcs(f):
                g = arc.other(f)
                if arc.convexity == "concave" and g not in ctx.owner:
                    seeds.add(g)
        seen: set[int] = set()
        for s in sorted(seeds):
            if s in seen:
                continue
            group, stack = set(), [s]
            while stack:
                g = stack.pop()
                if g in group:
                    continue
                pts = face_points(ctx.topo, g)
                if not pts or max(V.norm(V.radial(p, origin, axis)) for p in pts) > r * 1.05 + 1e-6:
                    continue
                gi = ctx.info(g)
                if gi.kind == "plane" and abs(V.dot(gi.normal, axis)) > 0.2:
                    continue
                group.add(g)
                for g2 in ctx.aag.neighbors(g):
                    if g2 not in ctx.owner and g2 not in group and g2 not in bore:
                        stack.append(g2)
            seen |= group
            if not group or len(group) > 6:
                continue
            pts = [p for g in group for p in face_points(ctx.topo, g)]
            inner = min(V.norm(V.radial(p, origin, axis)) for p in pts)
            interference = r - inner
            if interference <= 1e-3 or interference > 0.3 * r:
                continue
            lo, hi = extent(pts, axis, origin)
            score = Score(0.85)
            score.ok(f"stands {interference:.3g} proud of the bore Ø{2 * r:.3g} (Hole {hole.id})")
            cand = Candidate(kind=FeatureKind.RIB, subtype="crush", faces=sorted(group), support=sorted(bore),
                             params={"interference": r3(interference), "length": r3(hi - lo),
                                     "bore_diameter": r3(2 * r)},
                             frame=Frame(vec3(origin), vec3(axis)), score=score, parent=hole.id)
            if ctx.accept(cand):
                hole.params["crush_ribs"] = hole.params.get("crush_ribs", 0) + 1
                hole.params["partial"] = False
        if hole.params.get("crush_ribs"):
            ribbed = {f for c in ctx.accepted if c.parent == hole.id for f in c.faces}
            for f in bore:
                for g in ctx.aag.neighbors(f, "concave"):
                    gi = ctx.info(g)
                    if g in ctx.owner or gi.kind != "plane" or abs(V.dot(gi.normal, axis)) < 0.99:
                        continue
                    if ctx.aag.neighbors(g) <= set(bore) | ribbed:
                        hole.faces = sorted(set(hole.faces) | {g})
                        ctx.owner[g] = hole.id
                        hole.params["bottom"] = "flat"
                        hole.params["through"] = False
                        hole.score.ok(f"Face{g} is its floor, between the crush ribs")


def living_hinges(ctx: Context) -> None:
    """A groove that leaves a web thinner than ``max_hinge_thickness``
    (from one side, or two grooves meeting from both sides)."""
    opt = ctx.options
    slots = [c for c in ctx.accepted if c.kind == FeatureKind.SLOT and c.subtype in ("open", "")]
    by_face = {f: c for c in slots for f in c.faces}
    done: set[int] = set()
    for s in slots:
        if s.id in done:
            continue
        bottom = _groove_bottom(ctx, s)
        if bottom is None:
            continue
        p, inward = bottom
        hits = ray_hits(ctx, V.add(p, V.mul(inward, -1e-4)), inward)
        hits = [(d - 1e-4, f) for d, f in hits if d > 1e-3]   # past the groove's own bottom
        if not hits:
            continue
        web, exit_face = hits[0]
        if web > opt.max_hinge_thickness:
            continue
        # a second groove from the other side, or plate on the other side
        partner = by_face.get(exit_face)
        members = [s] + ([partner] if partner is not None and partner.id != s.id else [])
        depth = sum(float(m.params.get("depth", 0.0)) for m in members)
        if depth < 2.0 * web:
            continue                         # a thin plate with a scratch, not a hinge
        length = max(float(m.params.get("length", 0.0)) for m in members)
        if length < 2.0 * max(float(m.params.get("width", 0.0)) for m in members):
            continue                         # a short notch: a hinge runs along a line
        params = {"web_thickness": r3(web), "length": r3(max(float(m.params.get("length", 0.0)) for m in members)),
                  "groove_width": r3(max(float(m.params.get("width", 0.0)) for m in members)),
                  "sides": len(members), "profile": s.params.get("profile", "")}
        score = Score(0.85)
        score.ok(f"the groove leaves a web {web:.3g} thick")
        if len(members) == 2:
            score.ok("grooved from both sides")
        cand = Candidate(kind=FeatureKind.LIVING_HINGE, subtype="double" if len(members) == 2 else "single",
                         faces=sorted({f for m in members for f in m.faces}),
                         support=sorted({f for m in members for f in m.support}), params=params,
                         frame=Frame(vec3(p), vec3(inward)), score=score)
        done |= {m.id for m in members}
        _take(ctx, members, cand)


def _groove_bottom(ctx: Context, slot: Candidate):
    """A point at the bottom of a groove and the direction into the
    material under it."""
    faces = slot.faces
    # a floor: the largest flat face whose neighbours in the groove both rise from it
    best = None
    for f in faces:
        fi = ctx.info(f)
        if fi.kind != "plane":
            continue
        walls = [g for g in ctx.aag.neighbors(f, "concave") if g in faces]
        if len(walls) >= 2 and (best is None or fi.area > ctx.info(best).area):
            best = f
    if best is not None:
        fi = ctx.info(best)
        p = fi.sample_point or fi.centroid
        return p, V.mul(fi.normal, -1.0)
    # a V: the valley edge between two walls
    for f in faces:
        for arc in ctx.aag.arcs(f):
            g = arc.other(f)
            if g in faces and arc.convexity == "concave" and arc.edge:
                edge = ctx.topo.edges[arc.edge]
                if edge is None or not edge.points:
                    continue
                p = edge.points[len(edge.points) // 2]
                n1, n2 = ctx.info(f).sample_normal, ctx.info(g).sample_normal
                if n1 is None or n2 is None:
                    continue
                return p, V.unit(V.mul(V.add(n1, n2), -1.0))
    return None


def vents(ctx: Context) -> None:
    """At least ``min_vent_count`` like openings through one wall, evenly
    spaced in a row or grid: one vent feature."""
    opt = ctx.options
    kinds = (FeatureKind.CUTOUT, FeatureKind.SLOT)
    cands = [c for c in ctx.accepted if (c.kind in kinds and (c.kind == FeatureKind.CUTOUT or c.params.get("through")))
             or (c.kind == FeatureKind.HOLE and c.params.get("through") and c.subtype == "simple"
                 and float(c.params.get("diameter", 99)) <= 6.0)]
    groups: list[list[Candidate]] = []
    sigs: dict[int, tuple] = {}
    for c in cands:
        sig = _opening_signature(ctx, c)
        if sig is None:
            continue
        for g in groups:
            if g[0].kind == c.kind and _alike(sigs[id(g[0])], sig) and set(g[0].support) & set(c.support):
                sigs[id(c)] = sig
                g.append(c)
                break
        else:
            sigs[id(c)] = sig
            groups.append([c])
    for g in groups:
        need = opt.min_vent_count if g[0].kind != FeatureKind.HOLE else max(opt.min_vent_count, 6)
        if len(g) < need:
            continue
        centres = [sigs[id(c)][3] for c in g]
        pitch = _even_pitch(centres)
        if pitch is None:
            continue
        w, l, depth = sigs[id(g[0])][:3]
        # a vent is dense: openings close together (a bolt pattern is not)
        if pitch > (2.5 * w if g[0].kind == FeatureKind.HOLE else 3.0 * w):
            continue
        if g[0].kind != FeatureKind.HOLE and l < 2.0 * w and len(g) < 6:
            continue                     # a few squarish openings: separate cut-outs
        sub = {FeatureKind.HOLE: "holes", FeatureKind.SLOT: "slots"}.get(g[0].kind, "slots" if l > 2.0 * w else "openings")
        score = Score(0.85)
        score.ok(f"{len(g)} like openings {w:.3g} x {l:.3g} through one wall, {pitch:.3g} apart")
        cand = Candidate(kind=FeatureKind.VENT, subtype=sub, faces=sorted({f for c in g for f in c.faces}),
                         support=sorted({f for c in g for f in c.support} - {f for c in g for f in c.faces}),
                         params={"count": len(g), "pitch": r3(pitch), "opening_width": r3(w),
                                 "opening_length": r3(l), "wall_thickness": r3(depth),
                                 "open_area": r3(sum(sigs[id(c)][4] for c in g))},
                         frame=g[0].frame, score=score)
        _take(ctx, g, cand)


def _opening_signature(ctx: Context, c: Candidate):
    """(width, length, depth, centre, area) of a through opening, measured
    across its axis."""
    axis = c.frame.axis if c.frame is not None else None
    if axis is None:
        return None
    pts = [p for f in c.faces for p in face_points(ctx.topo, f)]
    if not pts:
        return None
    u = V.any_perpendicular(axis)
    # the opening's long direction: its longest straight edge across the axis
    best = None
    for f in c.faces:
        for e in ctx.info(f).edges:
            edge = ctx.topo.edges[e]
            if edge is None or not edge.straight or len(edge.points) < 2:
                continue
            d = V.sub(edge.points[-1], edge.points[0])
            d = V.sub(d, V.mul(axis, V.dot(d, axis)))
            if V.norm(d) > 1e-9 and (best is None or edge.length > best[0]):
                best = (edge.length, V.unit(d))
    if best is not None:
        u = best[1]
    v = V.unit(V.cross(axis, u))
    eu, ev, ea = extent(pts, u), extent(pts, v), extent(pts, axis)
    a, b = eu[1] - eu[0], ev[1] - ev[0]
    w, l = min(a, b), max(a, b)
    centre = V.mul(V.add(V.add(V.mul(u, (eu[0] + eu[1]) / 2), V.mul(v, (ev[0] + ev[1]) / 2)),
                         V.mul(axis, (ea[0] + ea[1]) / 2)), 1.0)
    if c.kind == FeatureKind.HOLE and c.params.get("diameter"):
        w = l = float(c.params["diameter"])       # exact, not from sampled points
    area = w * l * (math.pi / 4.0 if c.kind == FeatureKind.HOLE else 1.0)
    return (w, l, ea[1] - ea[0], centre, area)


def _alike(a, b, rel: float = 0.1) -> bool:
    return all(abs(x - y) <= rel * max(abs(x), abs(y), 1e-9) for x, y in zip(a[:3], b[:3]))


def _even_pitch(centres) -> Optional[float]:
    """The spacing of points along their best line (or a grid's rows),
    if it is even; else None."""
    if len(centres) < 2:
        return None
    gaps = []
    for i, c in enumerate(centres):
        d = min(V.norm(V.sub(c, o)) for j, o in enumerate(centres) if j != i)
        gaps.append(d)
    m = sorted(gaps)[len(gaps) // 2]
    if m <= 1e-9 or any(abs(g - m) > 0.15 * m for g in gaps):
        return None
    return m


def text(ctx: Context) -> None:
    """Lettering or a logo: at least ``min_text_glyphs`` small shallow
    shapes on one face, with thin strokes (stroke width at most a quarter of
    the glyph's size). Engraved or embossed. The glyphs merge into one."""
    opt = ctx.options
    kinds = (FeatureKind.POCKET, FeatureKind.SLOT, FeatureKind.STEP, FeatureKind.CUTOUT, FeatureKind.DEPRESSION,
             FeatureKind.PROTRUSION, FeatureKind.RIB, FeatureKind.BOSS)
    glyphs: dict[int, list[tuple[Candidate, float, float, float]]] = {}
    for c in ctx.accepted:
        if c.kind not in kinds:
            continue
        g = _glyph(ctx, c)
        if g is None:
            continue
        base, size, stroke, depth = g
        if depth > opt.max_text_depth or depth <= 0:
            continue
        glyphs.setdefault(base, []).append((c, size, stroke, depth))
    for base, gs in glyphs.items():
        if len(gs) < opt.min_text_glyphs:
            continue
        thin = [x for x in gs if x[2] <= 0.25 * x[1]]
        if len(thin) * 2 < len(gs) or len(gs) < opt.min_text_glyphs:
            continue
        sizes = sorted(x[1] for x in gs)
        bi = ctx.info(base)
        engraved = sum(1 for x in gs if x[0].kind in (FeatureKind.POCKET, FeatureKind.SLOT, FeatureKind.STEP,
                                                      FeatureKind.CUTOUT, FeatureKind.DEPRESSION))
        sub = "engraved" if engraved * 2 >= len(gs) else "embossed"
        pts = [p for x in gs for f in x[0].faces for p in face_points(ctx.topo, f)]
        u = V.any_perpendicular(bi.normal)
        v = V.unit(V.cross(bi.normal, u))
        eu, ev = extent(pts, u), extent(pts, v)
        score = Score(0.8)
        score.ok(f"{len(gs)} small {sub} shapes on Face{base}, strokes about "
                 f"{sorted(x[2] for x in gs)[len(gs) // 2]:.3g} wide")
        # what lies among the glyphs is part of the text too: the gaps
        # between embossed letters, the creases where drafted strokes meet
        members = [x[0] for x in gs]
        lo_u, hi_u, lo_v, hi_v = eu[0] - 0.5, eu[1] + 0.5, ev[0] - 0.5, ev[1] + 0.5
        b = V.dot(bi.origin, bi.normal)
        for c in list(ctx.accepted):
            if c in members or c.kind not in kinds:
                continue
            own = [f for f in c.faces if f != base]
            cp = [p for f in own for p in face_points(ctx.topo, f)]
            if not cp:
                continue
            cu, cv, cn = extent(cp, u), extent(cp, v), extent(cp, bi.normal)
            if cu[0] >= lo_u and cu[1] <= hi_u and cv[0] >= lo_v and cv[1] <= hi_v and \
                    max(abs(cn[0] - b), abs(cn[1] - b)) <= opt.max_text_depth + 1e-6:
                members.append(c)
        cand = Candidate(kind=FeatureKind.TEXT, subtype=sub,
                         faces=sorted({f for c in members for f in c.faces} - {base}),
                         support=[base],
                         params={"glyphs": len(gs), "depth": r3(max(x[3] for x in gs)),
                                 "glyph_size": r3(sizes[len(sizes) // 2]),
                                 "min_stroke": r3(min(x[2] for x in gs)),
                                 "extent": (r3(eu[1] - eu[0]), r3(ev[1] - ev[0]))},
                         frame=Frame(vec3(bi.centroid), vec3(bi.normal)), score=score)
        _take(ctx, members, cand)


def _in_plane_direction(ctx: Context, face: int, n) -> tuple:
    """A direction in a planar face lined up with its longest straight
    edge, so sizes are measured in the part's own frame."""
    best = None
    for e in ctx.info(face).edges:
        edge = ctx.topo.edges[e]
        if edge is None or not edge.straight or len(edge.points) < 2:
            continue
        d = V.sub(edge.points[-1], edge.points[0])
        d = V.sub(d, V.mul(n, V.dot(d, n)))
        if V.norm(d) > 1e-9 and (best is None or edge.length > best[0]):
            best = (edge.length, V.unit(d))
    return best[1] if best is not None else V.any_perpendicular(n)


def _glyph(ctx: Context, c: Candidate):
    """(base face, size, stroke width, depth) of a small shallow shape, or None."""
    planes = [f for f in c.support if _plane(ctx, f) is not None]
    if not planes:
        return None
    base = max(planes, key=lambda f: ctx.info(f).area)
    bi = ctx.info(base)
    n = bi.normal
    pts = [p for f in c.faces for p in face_points(ctx.topo, f)]
    if not pts:
        return None
    lv = extent(pts, n)
    b = V.dot(bi.origin, n)
    depth = max(abs(lv[0] - b), abs(lv[1] - b))
    u = _in_plane_direction(ctx, base, n)
    v = V.unit(V.cross(n, u))
    eu, ev = extent(pts, u), extent(pts, v)
    size = max(eu[1] - eu[0], ev[1] - ev[0])
    if size <= 0 or size > 40.0:
        return None
    # stroke: the widest flat face parallel to the base (a floor or a top): 2 A / P
    flats = [f for f in c.faces if _plane(ctx, f) is not None and abs(V.dot(ctx.info(f).normal, n)) > 0.999]
    if flats:
        f = max(flats, key=lambda x: ctx.info(x).area)
        fi = ctx.info(f)
        perim = sum(ctx.topo.edges[e].length for e in fi.edges if ctx.topo.edges[e] is not None)
        stroke = 2.0 * fi.area / max(perim, 1e-9)
    else:
        stroke = float(c.params.get("width", c.params.get("thickness", size)))
    return base, size, stroke, depth


_NUTS = ((2.0, 4.0), (2.5, 5.0), (3.0, 5.5), (4.0, 7.0), (5.0, 8.0), (6.0, 10.0), (8.0, 13.0), (10.0, 16.0),
         (12.0, 18.0))


def _metric(across_flats: float, tol: float = 0.08) -> str:
    """The metric nut nearest *across_flats* (ISO 4032), if near enough."""
    m, af = min(_NUTS, key=lambda n: abs(across_flats - n[1]))
    return f"M{m:g}" if abs(across_flats - af) <= tol * af + 0.15 else ""


def nut_traps(ctx: Context) -> None:
    """A hexagonal pocket or hole (a nut's shape) with a bolt hole on its
    axis is a nut trap; a slot that a bolt hole crosses is a nut slot."""
    holes = [c for c in ctx.accepted if c.kind == FeatureKind.HOLE and c.frame is not None]
    for c in ctx.accepted:
        if c.kind not in (FeatureKind.POCKET, FeatureKind.CUTOUT):
            continue
        hexa = _hexagon(ctx, c)
        if hexa is None:
            continue
        axis, centre, af = hexa
        c.params["across_flats"] = r3(af)
        size = _metric(af)
        if size:
            c.params["nut_size"] = size
        for h in holes:
            if abs(abs(V.dot(h.frame.axis, axis)) - 1.0) < 1e-3 and \
                    V.point_line_distance(centre, h.frame.origin, h.frame.axis) < 0.05 * af:
                c.subtype = "nut_trap"
                c.params["bolt_hole"] = h.id
                c.interacts.add(h.id)
                h.interacts.add(c.id)
                c.score.ok(f"hexagonal, {af:.3g} across flats{' (' + size + ' nut)' if size else ''}, "
                           f"on the axis of Hole {h.id}")
                break
        else:
            c.params["shape"] = "hexagon"
    for s in [c for c in ctx.accepted if c.kind in (FeatureKind.SLOT, FeatureKind.POCKET) and c.subtype != "nut_trap"]:
        walls = [f for f in s.faces if _plane(ctx, f) is not None]
        pairs = [(a, b) for i, a in enumerate(walls) for b in walls[i + 1:]
                 if V.dot(ctx.info(a).normal, ctx.info(b).normal) < -0.999]
        pts = [p for f in s.faces for p in face_points(ctx.topo, f)]
        found = False
        for a, b in pairs:
            n = ctx.info(a).normal
            gap = abs(V.dot(V.sub(ctx.info(b).origin, ctx.info(a).origin), n))
            for h in holes:
                if abs(abs(V.dot(h.frame.axis, n)) - 1.0) > 1e-3:
                    continue
                # the bolt's line runs through the slot
                o = h.frame.origin
                ax1 = V.unit(V.any_perpendicular(n))
                ax2 = V.unit(V.cross(n, ax1))
                if not all(extent(pts, ax)[0] - 1e-6 <= V.dot(o, ax) <= extent(pts, ax)[1] + 1e-6
                           for ax in (ax1, ax2)):
                    continue
                across = V.unit(V.cross(n, s.frame.axis)) if s.frame is not None and \
                    V.norm(V.cross(n, s.frame.axis)) > 1e-6 else ax1
                e = extent(pts, across)
                width = e[1] - e[0]
                size = _metric(width, 0.15)
                dia = float(h.params.get("diameter", 0.0))
                # a nut's shape: as wide as a nut for this bolt, thinner than wide
                if not size or gap > 0.6 * width or not (1.4 * dia <= width <= 2.3 * dia):
                    continue
                s.kind = FeatureKind.SLOT
                s.subtype = "nut_slot"
                s.params["nut_thickness"] = r3(gap)
                s.params["nut_width"] = r3(e[1] - e[0])
                s.params["bolt_hole"] = h.id
                s.params["nut_size"] = size
                s.interacts.add(h.id)
                h.interacts.add(s.id)
                s.score.ok(f"Hole {h.id} crosses it: a slot for a {size} nut {gap:.3g} thick")
                found = True
                break
            if found:
                break


def _hexagon(ctx: Context, c: Candidate):
    """(axis, centre, across flats) when six of the feature's flat walls
    make a regular hexagon round one axis."""
    walls = [f for f in c.faces if _plane(ctx, f) is not None]
    for f in walls:
        n0 = ctx.info(f).normal
        group = [g for g in walls if abs(V.dot(ctx.info(g).normal, n0)) > 0.999
                 or any(abs(V.dot(ctx.info(g).normal, n0) - math.cos(math.radians(a))) < 0.01 for a in (60, 120))]
        # the hexagon's walls are all square to one axis
        if len(group) < 6:
            continue
        axis = None
        for g in group:
            cr = V.cross(n0, ctx.info(g).normal)
            if V.norm(cr) > 1e-3:
                axis = V.unit(cr)
                break
        if axis is None or any(abs(V.dot(ctx.info(g).normal, axis)) > 0.02 for g in group):
            continue
        dirs = []
        for g in group:
            n = ctx.info(g).normal
            if not any(abs(V.dot(n, d) - 1) < 1e-3 for d in dirs):
                dirs.append(n)
        if len(dirs) != 6:
            continue
        # opposite walls: across flats
        afs = []
        for g in group:
            for k in group:
                if V.dot(ctx.info(g).normal, ctx.info(k).normal) < -0.999:
                    afs.append(abs(V.dot(V.sub(ctx.info(k).origin, ctx.info(g).origin), ctx.info(g).normal)))
        if not afs:
            continue
        af = sum(afs) / len(afs)
        if max(afs) - min(afs) > 0.03 * af:
            continue
        centre = V.mul(_sum([ctx.info(g).centroid for g in group]), 1.0 / len(group))
        return axis, centre, af
    return None


def _sum(vs):
    x = y = z = 0.0
    for v in vs:
        x, y, z = x + v[0], y + v[1], z + v[2]
    return (x, y, z)
