# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import collections
from typing import Any, Optional, Callable
import math

from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face
from OCC.Core.gp import gp_Lin
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector

from ...dfm.base.base_analyzer import BaseAnalyzer
from ...dfm.registries import register_analyzer
from ...dfm.utils import (
    get_adaptive_sample_count,
    get_face_uv_center,
    get_face_uv_normal,
    yield_face_uv_grid,
    get_point_from_uv,
)


@register_analyzer("RAY_THICKNESS_ANALYZER")
class RayThicknessAnalyzer(BaseAnalyzer):
    @property
    def analysis_type(self) -> str:
        return "RAY_THICKNESS_ANALYZER"

    @property
    def name(self) -> str:
        return "Ray Thickness Analyzer"

    def resolve_prefs(self, prefs: dict) -> None:
        self.min_samples = prefs.get("RayMinSamples", 5)
        self.max_samples = prefs.get("RayMaxSamples", 10)
        self.margin = prefs.get("RayMargin", 0.0)
        self.intersector_tol = prefs.get("RayIntersectorTol", 1e-3)
        cone_deg = prefs.get("RayNormalCone", 5.0)
        self.normal_cone_cos = math.cos(math.radians(180.0 - cone_deg))
        self.seed_threshold: float = prefs.get("RaySeedCoverageThreshold", 50) / 100

    def execute(
        self,
        shape: TopoDS_Shape,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> dict[TopoDS_Face, list[float]]:
        """
        Calculates the minimum thickness for all faces of a given TopoDS_Shape.
        """
        self.resolve_prefs(kwargs.get("prefs", {}))

        self.intersector = IntCurvesFace_ShapeIntersector()
        self.intersector.Load(shape, self.intersector_tol)

        self.face_seeds: dict[TopoDS_Face, list[tuple[float, float, float]]] = (
            collections.defaultdict(list)
        )
        self.measured_faces: set[TopoDS_Face] = set()

        results = {}
        for face in self.iter_faces(shape, progress_cb, check_abort):
            thicknesses = self._ray_cast_for_face(face)
            if thicknesses:
                results[face] = thicknesses
        return results

    def _ray_cast_for_face(self, face: TopoDS_Face) -> list[float]:
        """
        Ray cast the given face and return a list of thickness values.
        """
        adaptive_samples = get_adaptive_sample_count(face, self.min_samples, self.max_samples)
        coverage_threshold = max(1, int((adaptive_samples**2) * self.seed_threshold))

        seeds = self.face_seeds.get(face, [])

        if len(seeds) >= coverage_threshold:
            self.measured_faces.add(face)
            return [s[2] for s in seeds]

        thicknesses = [s[2] for s in seeds]

        _R = 3  # round value
        visited_uvs: dict[tuple[float, float], float] = {
            (round(s_u, _R), round(s_v, _R)): s_thick for s_u, s_v, s_thick in seeds
        }

        # Always sample the face centre
        u_mid, v_mid = get_face_uv_center(face)
        key_mid = (round(u_mid, _R), round(v_mid, _R))
        if key_mid not in visited_uvs:
            for t in self.ray_cast_at_uv(face, u_mid, v_mid):
                if t > 0:
                    thicknesses.append(t)
                    visited_uvs[key_mid] = t

        for u, v in yield_face_uv_grid(face, adaptive_samples, margin=self.margin):
            key = (round(u, _R), round(v, _R))
            if key in visited_uvs:
                continue
            for t in self.ray_cast_at_uv(face, u, v):
                if t > 0:
                    thicknesses.append(t)
            visited_uvs[key] = 0.0

        self.measured_faces.add(face)
        return thicknesses

    def ray_cast_at_uv(
        self,
        face: TopoDS_Face,
        u: float,
        v: float,
    ) -> list[float]:
        """
        Returns all wall thicknesses measured along the inward ray from (u, v).

        A hit is only used for seeding if the ray is within _NORMAL_CONE_DEG of
        normal to the hit face, ensuring seeds land at meaningful UV locations.
        """
        outward_norm = get_face_uv_normal(face, u, v)
        if not outward_norm:
            return []

        inward_norm = outward_norm.Reversed()
        epsilon = 1e-4
        point = get_point_from_uv(face, inward_norm, u, v, epsilon)
        ray = gp_Lin(point, inward_norm)

        self.intersector.Perform(ray, -1e30, 1e30)

        if not self.intersector.IsDone() or self.intersector.NbPnt() == 0:
            return []

        # Collect and sort all valid forward hits by W parameter
        hits: list[tuple[float, TopoDS_Face, float, float]] = []
        for i in range(1, self.intersector.NbPnt() + 1):
            w = self.intersector.WParameter(i)
            if w > epsilon * 10:
                hits.append(
                    (
                        w,
                        self.intersector.Face(i),
                        self.intersector.UParameter(i),
                        self.intersector.VParameter(i),
                    )
                )
        hits.sort(key=lambda h: h[0])

        if not hits:
            return []

        thicknesses: list[float] = []

        exit_w, exit_face, exit_u, exit_v = hits[0]
        exit_normal = get_face_uv_normal(exit_face, exit_u, exit_v)

        if exit_normal:
            ray_dot_exit = outward_norm.Dot(exit_normal)

            if ray_dot_exit < self.normal_cone_cos:
                thicknesses.append(exit_w)

                if not exit_face.IsSame(face) and exit_face not in self.measured_faces:
                    self.face_seeds[exit_face].append((exit_u, exit_v, exit_w))

        i = 1
        while i + 1 < len(hits):
            entry_w, entry_face, entry_u, entry_v = hits[i]
            exit_w, exit_face, exit_u, exit_v = hits[i + 1]

            wall_thickness = exit_w - entry_w

            if wall_thickness <= epsilon:
                i += 2
                continue

            entry_normal = get_face_uv_normal(entry_face, entry_u, entry_v)
            exit_normal = get_face_uv_normal(exit_face, exit_u, exit_v)

            # At least one face must be roughly normal to the ray for a valid wall.
            entry_ok = (
                entry_normal is not None and outward_norm.Dot(entry_normal) > -self.normal_cone_cos
            )
            exit_ok = (
                exit_normal is not None and outward_norm.Dot(exit_normal) < self.normal_cone_cos
            )

            if entry_ok or exit_ok:
                thicknesses.append(wall_thickness)

                if (
                    entry_ok
                    and not entry_face.IsSame(face)
                    and entry_face not in self.measured_faces
                ):
                    self.face_seeds[entry_face].append((entry_u, entry_v, wall_thickness))

                if exit_ok and not exit_face.IsSame(face) and exit_face not in self.measured_faces:
                    self.face_seeds[exit_face].append((exit_u, exit_v, wall_thickness))

            i += 2

        return thicknesses
