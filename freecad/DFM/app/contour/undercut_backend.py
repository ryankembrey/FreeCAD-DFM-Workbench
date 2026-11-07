# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Which way each point of the surface releases from the mold.

Loaded straight from its file by the worker processes (like
thickness_backend.py), so it imports OCP only, never FreeCAD or this package.

Each point gets a category code (a float, like every contour value):

    0  either side: open both ways along the pull (a straddle wall)
    1  releases along the pull (core side)
    2  releases against the pull (cavity side)
    3  undercut: blocked both ways, and no side action frees it
    4+ released by side action 1, 2, ... (code 4 + index)

The test matches the DFM undercut rule (UNDERCUT_ANALYZER): a point is
blocked in a direction when a ray from just off the surface hits the part
further than the self-hit distance away. A face that leans back against a
direction is blocked in it straight away, without a ray: moving that way
would pull the steel through the part.
"""

EITHER = 0.0
ALONG = 1.0
AGAINST = 2.0
UNDERCUT = 3.0
SIDE_ACTION_BASE = 4.0

# What a point that fails to measure is shown as.
FAILED_VALUE = EITHER

_EPS = 1e-4
# A face may release along a direction it faces or runs parallel to;
# leaning back by more than this (cosine) locks it.
_PARALLEL = 1e-3


def _add(p, d, s):
    return (p[0] + d[0] * s, p[1] + d[1] * s, p[2] + d[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unit(v):
    length = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5
    if length < 1e-12:
        return None
    return (v[0] / length, v[1] / length, v[2] / length)


class UndercutRays:
    """Classifies points by ray casting along the pull and the side actions."""

    def __init__(self, ocp_shape, config, tol=1e-6):
        from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

        self._it = IntCurvesFace_ShapeIntersector()
        self._it.Load(ocp_shape, tol)
        pull = _unit(tuple(config.get("pull", (0.0, 0.0, 1.0)))) or (0.0, 0.0, 1.0)
        self._pull = pull
        self._against = (-pull[0], -pull[1], -pull[2])
        self._two_sided = bool(config.get("two_sided", True))
        self._sides = [d for d in (_unit(tuple(s)) for s in config.get("side_actions", ())) if d]
        # Hits closer than this are the face the ray starts on.
        self._ignore = float(config.get("self_hit", 0.05))
        # How far off the surface the rays start.
        self._lift = float(config.get("lift", 1e-3))

    def _hits(self, origin, direction):
        from OCP.gp import gp_Pnt, gp_Lin, gp_Dir

        self._it.Perform(gp_Lin(gp_Pnt(*origin), gp_Dir(*direction)), 0.0, 1.0e30)
        if not self._it.IsDone():
            return []
        return [self._it.WParameter(i) for i in range(1, self._it.NbPnt() + 1)]

    def _on_surface(self, point, outward, margin):
        """The point of the true surface under a mesh point: the first entry
        into the part along the inward normal, from just outside it."""
        inward = (-outward[0], -outward[1], -outward[2])
        origin = _add(point, outward, margin)
        ws = [w for w in self._hits(origin, inward) if w > _EPS]
        if not ws:
            return point
        return _add(origin, inward, min(ws))

    def _clear(self, start, outward, direction):
        if _dot(outward, direction) < -_PARALLEL:
            return False
        return all(w <= self._ignore for w in self._hits(start, direction))

    def at(self, point, outward, margin):
        surface = self._on_surface(point, outward, margin)
        start = _add(surface, outward, self._lift)

        along = self._clear(start, outward, self._pull)
        if not self._two_sided:
            if along:
                return ALONG
        else:
            against = self._clear(start, outward, self._against)
            if along and against:
                return EITHER
            if along:
                return ALONG
            if against:
                return AGAINST

        for index, direction in enumerate(self._sides):
            if self._clear(start, outward, direction):
                return SIDE_ACTION_BASE + index
        return UNDERCUT


def make_backend(method, ocp_shape):
    return UndercutRays(ocp_shape, dict(method or {}))
