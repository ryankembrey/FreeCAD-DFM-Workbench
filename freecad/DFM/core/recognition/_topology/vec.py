# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Small 3-vector helpers on plain tuples (fast enough, picklable, no numpy)."""

from __future__ import annotations

import math

Vec = tuple[float, float, float]


def add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(a: Vec, s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a: Vec) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Vec) -> Vec:
    n = norm(a)
    if n < 1e-15:
        return (0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def angle(a: Vec, b: Vec) -> float:
    """Angle between two vectors, degrees (0..180)."""
    na, nb = norm(a), norm(b)
    if na < 1e-15 or nb < 1e-15:
        return 0.0
    c = max(-1.0, min(1.0, dot(a, b) / (na * nb)))
    return math.degrees(math.acos(c))


def line_angle(a: Vec, b: Vec) -> float:
    """Angle between two lines (direction sign ignored), degrees (0..90)."""
    t = angle(a, b)
    return min(t, 180.0 - t)


def dist(a: Vec, b: Vec) -> float:
    return norm(sub(a, b))


def project_on_axis(p: Vec, origin: Vec, direction: Vec) -> float:
    return dot(sub(p, origin), direction)


def radial(p: Vec, origin: Vec, direction: Vec) -> Vec:
    """Vector from the axis line to p, perpendicular to the axis."""
    d = sub(p, origin)
    return sub(d, mul(direction, dot(d, direction)))


def point_line_distance(p: Vec, origin: Vec, direction: Vec) -> float:
    return norm(radial(p, origin, direction))


def any_perpendicular(d: Vec) -> Vec:
    ref = (1.0, 0.0, 0.0) if abs(d[0]) < 0.9 else (0.0, 1.0, 0.0)
    return unit(cross(d, ref))


def canonical_direction(d: Vec) -> Vec:
    """d or -d, whichever has its first significant component positive (so
    parallel axes compare equal whatever way they point)."""
    for c in d:
        if abs(c) > 1e-9:
            return d if c > 0 else mul(d, -1.0)
    return d
