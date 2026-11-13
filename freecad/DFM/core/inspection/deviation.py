# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Values for the asymmetry and deviation maps, one per mesh triangle.

Only triangles near a difference are measured: everywhere else the
surface matches (the boolean found nothing there), so it gets "no value"
(infinity), which the map draws grey as "Symmetric" or "Unchanged". So
does anything closer than the tolerance.
"""

from __future__ import annotations

import math
from typing import Callable, Iterable, Optional, Sequence

from OCP.TopoDS import TopoDS_Shape

from . import geometry as G

Progress = Optional[Callable[[int, int], None]]


def centroids(vertices: Sequence[Sequence[float]], triangles: Iterable[Sequence[int]]) -> list[G.Vec]:
    out = []
    for a, b, c in triangles:
        va, vb, vc = vertices[a], vertices[b], vertices[c]
        out.append(((va[0] + vb[0] + vc[0]) / 3.0, (va[1] + vb[1] + vc[1]) / 3.0,
                    (va[2] + vb[2] + vc[2]) / 3.0))
    return out


def _near(boxes: Sequence[G.Box], margin: float):
    grown = [b.grown(margin) for b in boxes]

    def test(p) -> bool:
        return any(b.contains(p) for b in grown)

    return test, grown


def _union(boxes: Sequence[G.Box]) -> Optional[G.Box]:
    if not boxes:
        return None
    return G.Box(tuple(min(b.lo[i] for b in boxes) for i in range(3)),  # type: ignore[arg-type]
                 tuple(max(b.hi[i] for b in boxes) for i in range(3)))


def asymmetry_values(points: Sequence[G.Vec], partner: TopoDS_Shape, boxes: Sequence[G.Box],
                     tolerance: float, progress: Progress = None) -> list[float]:
    """Distance from each point to *partner* (the mirrored or turned part)."""
    if not boxes:
        return [math.inf] * len(points)
    margin = max(tolerance * 10.0, 1e-3)
    near, grown = _near(boxes, margin)
    dist = G.SurfaceDistance(partner, _union(grown))
    out = []
    for i, p in enumerate(points):
        if progress and (i & 0x1FF) == 0:
            progress(i, len(points))
        if not near(p):
            out.append(math.inf)
            continue
        d = dist(p)
        out.append(d if math.isfinite(d) and d >= tolerance else math.inf)
    if progress:
        progress(len(points), len(points))
    return out


def deviation_values(points: Sequence[G.Vec], reference: TopoDS_Shape, boxes: Sequence[G.Box],
                     tolerance: float, progress: Progress = None) -> list[float]:
    """Signed distance from each point to *reference* (A, in B's frame):
    positive outside it (added), negative inside it (removed)."""
    if not boxes:
        return [math.inf] * len(points)
    margin = max(tolerance * 10.0, 1e-3)
    near, grown = _near(boxes, margin)
    dist = G.SurfaceDistance(reference, _union(grown))
    inside = G.Inside(reference, tolerance)
    out = []
    for i, p in enumerate(points):
        if progress and (i & 0x1FF) == 0:
            progress(i, len(points))
        if not near(p):
            out.append(math.inf)
            continue
        d = dist(p)
        if not math.isfinite(d) or d < tolerance:
            out.append(math.inf)
            continue
        out.append(-d if inside(p) else d)
    if progress:
        progress(len(points), len(points))
    return out
