# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Modeled threads on recognised holes.

A modeled thread is a helical sweep: free-form faces wrapped round the
hole's axis, all within a band of radii around the hole's radius. They
are added to the hole (subtype stays, ``threaded`` is set) with the major
and minor diameters, and the pitch from where a line parallel to the axis
crosses the thread flanks.

Cosmetic threads are not in the geometry at all; the workbench passes them
in as history hints (a PartDesign Hole's "Threaded" property).
"""

from __future__ import annotations

import math
import statistics

from ..model import FeatureKind
from .._topology import vec as V
from .common import Candidate, Context, r3

_ANALYTIC = ("plane", "cylinder", "cone", "sphere", "torus")


def _dense_points(ctx: Context, face: int, n: int = 64) -> list:
    """Many points along a face's edges (a helix needs more than a few)."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    pts = []
    for e in ctx.info(face).edges:
        edge = ctx.topo.edges[e]
        if edge is None or edge.convexity == "degenerate":
            continue
        c = BRepAdaptor_Curve(edge.edge)
        a, b = c.FirstParameter(), c.LastParameter()
        for k in range(n + 1):
            p = c.Value(a + (b - a) * k / n)
            pts.append((p.X(), p.Y(), p.Z()))
    return pts


def _pitch(ctx: Context, faces: list[int], origin, axis, radius: float) -> float:
    from OCP.BRep import BRep_Builder
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    from OCP.TopoDS import TopoDS_Compound
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

    comp = TopoDS_Compound()
    b = BRep_Builder()
    b.MakeCompound(comp)
    for f in faces:
        b.Add(comp, ctx.info(f).face)
    side = V.any_perpendicular(axis)
    best = 0.0
    for k in range(3):
        ang = 2 * math.pi * k / 3
        d = V.add(V.mul(side, math.cos(ang)), V.mul(V.cross(axis, side), math.sin(ang)))
        p = V.add(origin, V.mul(d, radius))
        inter = IntCurvesFace_ShapeIntersector()
        inter.Load(comp, 1e-6)
        inter.Perform(gp_Lin(gp_Pnt(*p), gp_Dir(*axis)), -1e9, 1e9)
        if not inter.IsDone() or inter.NbPnt() < 3:
            continue
        ws = sorted(inter.WParameter(i) for i in range(1, inter.NbPnt() + 1))
        ws = [w for i, w in enumerate(ws) if i == 0 or w - ws[i - 1] > 1e-6]
        diffs = [ws[i + 1] - ws[i] for i in range(len(ws) - 1)]
        pairs = [diffs[i] + diffs[i + 1] for i in range(len(diffs) - 1)]
        if pairs:
            best = statistics.median(pairs)
            break
    return best


def find_threads(ctx: Context) -> list[Candidate]:
    tol = max(ctx.options.linear_tolerance * 10, 1e-6)
    free = [f for f in ctx.aag.faces if f not in ctx.owner and ctx.info(f).kind not in _ANALYTIC]
    if not free:
        return []
    for cand in ctx.accepted:
        if cand.kind not in (FeatureKind.HOLE, FeatureKind.BOSS) or "axis" not in cand.links:
            continue
        origin, axis = cand.links["axis"]
        r = cand.links.get("radius", 0.0)
        if r <= 0:
            continue
        mine = []
        radii = []
        for f in free:
            if f in ctx.owner:
                continue
            info = ctx.info(f)
            pts = _dense_points(ctx, f)
            if info.sample_point:
                pts.append(info.sample_point)
            if not pts:
                continue
            rs = [V.point_line_distance(p, origin, axis) for p in pts]
            if min(rs) < 0.5 * r - tol or max(rs) > 1.6 * r + tol:
                continue
            mine.append(f)
            radii.extend(rs)
        if not mine:
            continue
        # The faces must wrap round the axis
        angles = set()
        ref = V.any_perpendicular(axis)
        ref2 = V.cross(axis, ref)
        for f in mine:
            for p in _dense_points(ctx, f):
                rad = V.radial(p, origin, axis)
                angles.add(int(math.degrees(math.atan2(V.dot(rad, ref2), V.dot(rad, ref))) // 30))
        if len(angles) < 10:
            continue
        r_minor, r_major = min(radii), max(radii)
        pitch = _pitch(ctx, mine, origin, axis, (r_minor + r_major) / 2.0)
        for f in mine:
            ctx.owner[f] = cand.id
        cand.faces = sorted(set(cand.faces) | set(mine))
        cand.params["threaded"] = True
        cand.params["thread_major_diameter"] = r3(2 * r_major)
        cand.params["thread_minor_diameter"] = r3(2 * r_minor)
        if pitch > 0:
            cand.params["thread_pitch"] = r3(pitch)
        cand.params["thread_modeled"] = True
        if cand.subtype == "simple":
            cand.subtype = "threaded"
        cand.score.ok(f"modeled thread {2 * r_major:.3g} x {pitch:.3g}")
        free = [f for f in free if f not in ctx.owner]
    return []
