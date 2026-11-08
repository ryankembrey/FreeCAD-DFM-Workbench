# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Distances between recognised features, for the spacing rules.

Features are plain data (face numbers, frames); these turn them back into
faces and edges of the analysed shape through its FaceIndex, and measure.
Small vector helpers for the plain (x, y, z) tuples in feature frames are
here too. Pure OCP: no FreeCAD.
"""

from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

from OCP.BRep import BRep_Builder
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepTools import BRepTools
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Edge, TopoDS_Shape

from .geometry import FaceIndex

Vec = Sequence[float]


# -- Vectors ----------------------------------------------------------------------


def dot(a: Vec, b: Vec) -> float:
    return sum(x * y for x, y in zip(a, b))


def sub(a: Vec, b: Vec) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a: Vec, b: Vec) -> tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a: Vec) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Vec) -> Optional[tuple[float, float, float]]:
    length = norm(a)
    return None if length < 1e-12 else (a[0] / length, a[1] / length, a[2] / length)


def parallel(a: Vec, b: Vec, max_deg: float) -> bool:
    """Lines along *a* and *b* (unit) are within *max_deg* of each other."""
    return abs(dot(a, b)) >= math.cos(math.radians(max_deg))


def off_axis(point: Vec, origin: Vec, axis: Vec) -> float:
    """Distance from *point* to the line through *origin* along unit *axis*."""
    v = sub(point, origin)
    along = dot(v, axis)
    return norm((v[0] - along * axis[0], v[1] - along * axis[1], v[2] - along * axis[2]))


def overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Length two intervals share (negative when they are apart)."""
    return min(a[1], b[1]) - max(a[0], b[0])


# -- Shapes ---------------------------------------------------------------------------


def compound(shapes: Iterable[TopoDS_Shape]) -> TopoDS_Compound:
    builder = BRep_Builder()
    comp = TopoDS_Compound()
    builder.MakeCompound(comp)
    for shape in shapes:
        builder.Add(comp, shape)
    return comp


def faces_of(face_index: FaceIndex, numbers: Iterable[int]) -> TopoDS_Compound:
    return compound(face_index.face_at(n) for n in numbers)


def min_distance(a: TopoDS_Shape, b: TopoDS_Shape) -> Optional[float]:
    """The least distance between two shapes, or None when it can't be found."""
    tool = BRepExtrema_DistShapeShape(a, b)
    if not tool.IsDone() or tool.NbSolution() == 0:
        return None
    return tool.Value()


def edges(shape: TopoDS_Shape) -> list[TopoDS_Edge]:
    out = []
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    while explorer.More():
        out.append(TopoDS.Edge_s(explorer.Current()))
        explorer.Next()
    return out


def shared_edges(a: TopoDS_Shape, b: TopoDS_Shape) -> list[TopoDS_Edge]:
    """Edges of *a* that are also edges of *b*."""
    theirs = edges(b)
    return [e for e in edges(a) if any(e.IsSame(o) for o in theirs)]


def rim(face_index: FaceIndex, hole_faces: Iterable[int], entry: int) -> list[TopoDS_Edge]:
    """Where a hole opens into face *entry*: the edges they share."""
    entry_face = face_index.face_at(entry)
    out: list[TopoDS_Edge] = []
    for n in hole_faces:
        out.extend(shared_edges(face_index.face_at(n), entry_face))
    return out


def outer_wire_distance(face_index: FaceIndex, entry: int, rim_edges: list) -> Optional[float]:
    """Distance from a hole's rim to the outer boundary of the face it opens in.

    None when the rim is on that boundary (the hole breaks the edge of the
    face) or the distance can't be found.
    """
    if not rim_edges:
        return None
    wire = BRepTools.OuterWire_s(face_index.face_at(entry))
    if wire.IsNull():
        return None
    boundary = edges(wire)
    if any(r.IsSame(b) for r in rim_edges for b in boundary):
        return None
    return min_distance(compound(rim_edges), wire)
