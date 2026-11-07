# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from typing import Any, Optional, Callable

from OCP.TopoDS import TopoDS_Shape, TopoDS_Face
from OCP.gp import gp_Pnt, gp_Lin, gp_Dir
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from freecad.DFM.core.utils.geometry import EdgeIndex, FaceIndex

from ...core.base import BaseAnalyzer
from ...core.models import ProcessRequirement
from ...core.utils import yield_face_uv_grid, get_face_uv_normal, get_point_from_uv
from ...core.registries import register_analyzer
from ...core.utils.mold import point_released_by_side_action


@register_analyzer("UNDERCUT_ANALYZER")
class UndercutAnalyzer(BaseAnalyzer):
    parallel = True
    relative_cost = 60  # ~ms per face (see BaseAnalyzer.relative_cost)

    @property
    def analysis_type(self) -> str:
        return "UNDERCUT_ANALYZER"

    @property
    def requirements(self) -> set[ProcessRequirement]:
        return {ProcessRequirement.PULL_DIRECTION, ProcessRequirement.SIDE_ACTIONS}

    @property
    def name(self) -> str:
        return "Undercut Analyzer"

    def resolve_prefs(self, prefs: dict) -> None:
        self.samples = prefs.get("UndercutSamples", 10)
        self.margin = prefs.get("UndercutMargin", 0.01)
        self.intersector_tol = prefs.get("UndercutIntersectorTol", 1e-6)
        self.self_hit_threshold = prefs.get("UndercutSelfHitThreshold", 0.05)

    def prepare(
        self,
        shape: TopoDS_Shape,
        face_index: FaceIndex,
        edge_index: EdgeIndex,
        **kwargs: Any,
    ) -> None:
        super().prepare(shape, face_index, edge_index, **kwargs)
        self.pull_direction = kwargs.get(ProcessRequirement.PULL_DIRECTION.name, gp_Dir(0, 0, 1))
        # A point trapped along the pull is fine if a slide, lifter or other
        # side action can pull straight away from it.
        self.side_actions = [d for d in (kwargs.get(ProcessRequirement.SIDE_ACTIONS.name) or []) if d]

        self.intersector = IntCurvesFace_ShapeIntersector()
        self.intersector.Load(shape, self.intersector_tol)

    def analyze(self, face: TopoDS_Face) -> Optional[float]:
        """Share of the face trapped along the pull (0..1), or None if none is."""
        ratio = self._analyze_face(face)
        return ratio if ratio > 0.0 else None

    def _analyze_face(self, face: TopoDS_Face):
        """
        Returns a score from 0.0 (Safe) to 1.0 (Completely Trapped).
        """
        total_points = 0
        trapped_points = 0

        for u, v in yield_face_uv_grid(face, self.samples, self.margin):
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
        if not (blocked_top and blocked_bottom):
            return False
        if self.side_actions and point_released_by_side_action(
            self.intersector, point, normal, self.side_actions, self.self_hit_threshold
        ):
            return False
        return True

    def _has_blocking_hit(self, start_point: gp_Pnt) -> bool:
        """
        Check whether the ray has blocked hit.
        """
        if not self.intersector.IsDone():
            return False

        self_hit_threshold = self.self_hit_threshold  ## mm

        for i in range(1, self.intersector.NbPnt() + 1):
            hit_pnt = self.intersector.Pnt(i)
            if start_point.Distance(hit_pnt) > self_hit_threshold:
                return True

        return False
