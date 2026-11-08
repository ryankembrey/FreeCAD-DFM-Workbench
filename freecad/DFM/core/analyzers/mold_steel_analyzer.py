# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""MOLD_STEEL_ANALYZER: how thick the mould steel between two part walls is.

From a few points on each face a ray is cast *outward*, into the mould.
When it meets the part again on a face turned back towards it (two walls
facing each other across a gap: the sides of a slot, two close ribs), the
steel filling that gap is as thick as the distance. A long, thin piece of
steel overheats and wears, so the depth of the gap along the pull is
recorded too: where it is shallower than it is wide, it isn't a blade.

Gaps across a round hole (a core pin) are left to the hole rules, and two
faces that meet at an edge make a corner, not a gap.

Each face's value is (gap mm, the facing face's number, depth along the
pull mm) for its narrowest gap.
"""

from __future__ import annotations

from typing import Any, Optional

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.TopoDS import TopoDS_Face, TopoDS_Shape
from OCP.gp import gp_Dir, gp_Lin

from ...core.base.base_analyzer import BaseAnalyzer
from ...core.models import ProcessRequirement
from ...core.registries import register_analyzer
from .ray_thickness_analyzer import RayThicknessAnalyzer
from ...core.utils.geometry import (
    EdgeIndex,
    FaceIndex,
    get_extent_along,
    get_face_uv_normal,
    get_point_from_uv,
)
from ...core.utils.nominal_wall import EDGE_GAP, OPPOSITE_DOT, sample_points

#: Gaps wider than this aren't thin steel (mm); keeps the results small.
MAX_GAP = 50.0
_EPSILON = 1e-4


@register_analyzer("MOLD_STEEL_ANALYZER")
class MoldSteelAnalyzer(BaseAnalyzer):
    parallel = True
    relative_cost = 4  # ~ms per face (5 rays)

    @property
    def analysis_type(self) -> str:
        return "MOLD_STEEL_ANALYZER"

    @property
    def name(self) -> str:
        return "Mould Steel Analyzer"

    @property
    def requirements(self) -> set[ProcessRequirement]:
        return {ProcessRequirement.PULL_DIRECTION}

    def resolve_prefs(self, prefs: dict) -> None:
        self.intersector_tol = prefs.get("RayIntersectorTol", 1e-3)

    def prepare(self, shape: TopoDS_Shape, face_index: FaceIndex, edge_index: EdgeIndex,
                **kwargs: Any) -> None:
        super().prepare(shape, face_index, edge_index, **kwargs)
        self.face_index = face_index
        self.pull: gp_Dir = kwargs.get(ProcessRequirement.PULL_DIRECTION.name) or gp_Dir(0, 0, 1)
        self.intersector = IntCurvesFace_ShapeIntersector()
        self.intersector.Load(shape, self.intersector_tol)
        # Faces meeting at an edge make a corner, not a gap with steel in it.
        self.adjacent = set(RayThicknessAnalyzer._build_shared_edges(shape, face_index))

    def analyze(self, face: TopoDS_Face) -> Optional[tuple[float, int, float]]:
        best: Optional[tuple[float, TopoDS_Face]] = None
        for u, v in sample_points(face):
            normal = get_face_uv_normal(face, u, v)
            if normal is None:
                continue
            start = get_point_from_uv(face, normal, u, v, _EPSILON)
            self.intersector.Perform(gp_Lin(start, normal), 0.0, MAX_GAP)
            if not self.intersector.IsDone():
                continue
            hits = sorted((self.intersector.WParameter(k), k)
                          for k in range(1, self.intersector.NbPnt() + 1)
                          if self.intersector.WParameter(k) > EDGE_GAP)
            if not hits:
                continue
            w, k = hits[0]
            other = self.intersector.Face(k)
            other_normal = get_face_uv_normal(other, self.intersector.UParameter(k),
                                              self.intersector.VParameter(k))
            if other_normal is None or normal.Dot(other_normal) >= OPPOSITE_DOT:
                continue
            if other.IsSame(face) or _core_pin(face, other):
                continue
            if (self.face_index.index_of(face), self.face_index.index_of(other)) in self.adjacent:
                continue
            gap = w + _EPSILON
            if best is None or gap < best[0]:
                best = (gap, other)
        if best is None:
            return None
        gap, other = best
        return (round(gap, 6), self.face_index.index_of(other), round(self._depth(face, other), 6))

    def _depth(self, a: TopoDS_Face, b: TopoDS_Face) -> float:
        """How far the two faces run alongside each other along the pull."""
        lo_a, hi_a = get_extent_along(a, self.pull)
        lo_b, hi_b = get_extent_along(b, self.pull)
        return max(0.0, min(hi_a, hi_b) - max(lo_a, lo_b))


def _core_pin(a: TopoDS_Face, b: TopoDS_Face) -> bool:
    """True when both faces are round about one axis: across a hole."""
    sa, sb = BRepAdaptor_Surface(a), BRepAdaptor_Surface(b)
    round_kinds = (GeomAbs_Cylinder, GeomAbs_Cone)
    if sa.GetType() not in round_kinds or sb.GetType() not in round_kinds:
        return False
    axis_a = sa.Cylinder().Axis() if sa.GetType() == GeomAbs_Cylinder else sa.Cone().Axis()
    axis_b = sb.Cylinder().Axis() if sb.GetType() == GeomAbs_Cylinder else sb.Cone().Axis()
    if not axis_a.IsParallel(axis_b, 1e-3):
        return False
    return gp_Lin(axis_a).Distance(axis_b.Location()) < 1e-3
