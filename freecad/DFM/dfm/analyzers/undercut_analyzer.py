# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from typing import Any, Optional, Callable

from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face
from OCC.Core.gp import gp_Pnt, gp_Lin, gp_Dir
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector

from ...dfm.base import BaseAnalyzer
from ...dfm.models import ProcessRequirement
from ...dfm.utils import yield_face_uv_grid, get_face_uv_normal, get_point_from_uv
from ...dfm.registries import register_analyzer


@register_analyzer("UNDERCUT_ANALYZER")
class UndercutAnalyzer(BaseAnalyzer):
    @property
    def analysis_type(self) -> str:
        return "UNDERCUT_ANALYZER"

    @property
    def requirements(self) -> set[ProcessRequirement]:
        return {ProcessRequirement.PULL_DIRECTION}

    @property
    def name(self) -> str:
        return "Undercut Analyzer"

    def resolve_prefs(self, prefs: dict) -> None:
        pass

    def execute(
        self,
        shape: TopoDS_Shape,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> dict[TopoDS_Face, Any]:
        self.pull_direction = kwargs.get(ProcessRequirement.PULL_DIRECTION.name, gp_Dir(0, 0, 1))
        self.samples = kwargs.get("samples", 10)

        self.intersector = IntCurvesFace_ShapeIntersector()
        self.intersector.Load(shape, 1e-6)
        results = {}
        for face in self.iter_faces(shape, progress_cb, check_abort):
            ratio = self._analyze_face(face)
            if ratio > 0.0:
                results[face] = ratio
        return results

    def _analyze_face(self, face: TopoDS_Face):
        """
        Returns a score from 0.0 (Safe) to 1.0 (Completely Trapped).
        """
        total_points = 0
        trapped_points = 0

        for u, v in yield_face_uv_grid(face, self.samples, 0.01):
            total_points += 1

            if self._is_point_trapped(face, u, v):
                trapped_points += 1

        if total_points == 0:
            return 0.0

        return float(trapped_points) / float(total_points)

    def _is_point_trapped(
        self,
        face: TopoDS_Face,
        u: float,
        v: float,
    ):
        normal = get_face_uv_normal(face, u, v)

        if not normal:
            return None

        epsilon = 1e-3
        point = get_point_from_uv(face, normal, u, v, epsilon)

        ray_up = gp_Lin(point, self.pull_direction)
        self.intersector.Perform(ray_up, 0, float("inf"))

        blocked_top = self._has_blocking_hit(point)

        if not blocked_top:
            return False

        ray_down = gp_Lin(point, self.pull_direction.Reversed())
        self.intersector.Perform(ray_down, 0, float("inf"))

        blocked_bottom = self._has_blocking_hit(point)
        return blocked_top and blocked_bottom

    def _has_blocking_hit(self, start_point: gp_Pnt) -> bool:
        """
        Check whether the ray has blocked hit.
        """
        if not self.intersector.IsDone():
            return False

        self_hit_threshold = 0.05  ## mm

        for i in range(1, self.intersector.NbPnt() + 1):
            hit_pnt = self.intersector.Pnt(i)
            if start_point.Distance(hit_pnt) > self_hit_threshold:
                return True

        return False
