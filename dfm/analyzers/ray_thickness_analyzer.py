#  ***************************************************************************
#  *   Copyright (c) 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>              *
#  *                                                                         *
#  *   This file is part of the FreeCAD CAx development system.              *
#  *                                                                         *
#  *   This library is free software; you can redistribute it and/or         *
#  *   modify it under the terms of the GNU Library General Public           *
#  *   License as published by the Free Software Foundation; either          *
#  *   version 2 of the License, or (at your option) any later version.      *
#  *                                                                         *
#  *   This library  is distributed in the hope that it will be useful,      *
#  *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
#  *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
#  *   GNU Library General Public License for more details.                  *
#  *                                                                         *
#  *   You should have received a copy of the GNU Library General Public     *
#  *   License along with this library; see the file COPYING.LIB. If not,    *
#  *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
#  *   Suite 330, Boston, MA  02111-1307, USA                                *
#  *                                                                         *
#  ***************************************************************************

import collections
from typing import Any, Optional, Callable
import math

from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face
from OCC.Core.gp import gp_Lin
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector

from dfm.core.base_analyzer import BaseAnalyzer
from dfm.registries import register_analyzer
from dfm.utils import (
    get_adaptive_sample_count,
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

    def execute(
        self,
        shape: TopoDS_Shape,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> dict[TopoDS_Face, list[float]]:
        """Calculates the minimum thickness for all faces of a given TopoDS_Shape."""
        samples = kwargs.get("samples", 10)

        self.intersector = IntCurvesFace_ShapeIntersector()
        self.intersector.Load(shape, 1e-3)
        self.face_seeds = collections.defaultdict(list)

        results = {}
        for face in self.iter_faces(shape, progress_cb, check_abort):
            thicknesses = self._ray_cast_for_face(face, samples)
            if thicknesses:
                results[face] = thicknesses
        return results

    def _ray_cast_for_face(self, face: TopoDS_Face, samples: int) -> list[float]:
        """
        Returns the thicknesses found at each point UV for a given face.
        """
        thicknesses = []
        adaptive_samples = get_adaptive_sample_count(face, samples)

        visited_uvs = {}

        def get_thickness(test_u: float, test_v: float) -> Optional[float]:
            key = (round(test_u, 5), round(test_v, 5))
            if key in visited_uvs:
                return visited_uvs[key]

            t = self.ray_cast_at_uv(face, test_u, test_v)
            visited_uvs[key] = t
            return t

        # Inject seeds from previous faces
        for s_u, s_v, s_thick in self.face_seeds.get(face, []):
            visited_uvs[(round(s_u, 5), round(s_v, 5))] = s_thick
            thicknesses.append(s_thick)

        # Grid search
        for u, v in yield_face_uv_grid(face, adaptive_samples):
            thick = get_thickness(u, v)
            if thick is not None and thick != float("inf") and thick > 0:
                thicknesses.append(thick)

        return thicknesses

    def ray_cast_at_uv(
        self,
        face: TopoDS_Face,
        u: float,
        v: float,
    ) -> Optional[float]:
        """
        Returns the thickness at UV. Returns float('inf') if no wall is hit.
        """
        outward_norm = get_face_uv_normal(face, u, v)
        if not outward_norm:
            return None

        inward_norm = outward_norm.Reversed()

        epsilon = 1e-4
        point = get_point_from_uv(face, inward_norm, u, v, epsilon)
        ray = gp_Lin(point, inward_norm)

        self.intersector.Perform(ray, 0, float("inf"))

        # At acute corners, thickness is reported as zero. This is intuitively incorrect for
        # a DFM analysis. So we compare the normal directions of the origin face and the hit
        # face, and filter out any faces whose angle between them is more than 60° away
        # from being parallel, and return inf
        # 180 - 60 = 120 -> cos(120) = -0.5
        acute_filter_angle = 60
        threshold_rad = math.radians(180 - acute_filter_angle)
        acute_filter = math.cos(threshold_rad)

        if self.intersector.IsDone() and self.intersector.NbPnt() > 0:
            p_hit = self.intersector.Pnt(1)
            dist = point.Distance(p_hit)

            hit_face = self.intersector.Face(1)
            hit_u = self.intersector.UParameter(1)
            hit_v = self.intersector.VParameter(1)

            hit_normal = get_face_uv_normal(hit_face, hit_u, hit_v)

            if hit_normal:
                dot_prod = outward_norm.Dot(hit_normal)

                if dot_prod < acute_filter:
                    thickness = dist

                    # Record seed
                    if not hit_face.IsSame(face):
                        self.face_seeds[hit_face].append((hit_u, hit_v, thickness))

                    return thickness

        return float("inf")
