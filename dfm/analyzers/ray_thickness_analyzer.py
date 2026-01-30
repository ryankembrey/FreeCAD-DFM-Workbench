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

from typing import Any, Optional
import math

from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face, topods
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.gp import gp_Pnt, gp_Lin
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector

from dfm.core.base_analyzer import BaseAnalyzer
from dfm.registries import register_analyzer
from dfm.utils import get_face_uv_normal, yield_face_uv_grid, get_point_from_uv


@register_analyzer("RAY_THICKNESS_ANALYZER")
class RayThicknessAnalyzer(BaseAnalyzer):
    @property
    def analysis_type(self) -> str:
        return "RAY_THICKNESS_ANALYZER"

    @property
    def name(self) -> str:
        return "Ray Thickness Analyzer"

    def execute(self, shape: TopoDS_Shape, **kwargs: Any) -> dict[TopoDS_Face, list[float]]:
        """Calculates the minimum thickness for all faces of a given TopoDS_Shape."""

        intersector = IntCurvesFace_ShapeIntersector()
        intersector.Load(shape, 1e-6)

        samples = kwargs.get("samples", 10)

        results: dict[TopoDS_Face, list[float]] = {}

        face_explorer = TopExp_Explorer(shape, TopAbs_FACE)  # type: ignore
        while face_explorer.More():
            current_face = topods.Face(face_explorer.Current())

            thicknesses = self._ray_cast_for_face(current_face, intersector, samples)

            if thicknesses:
                results[current_face] = thicknesses

            face_explorer.Next()

        return results

    def _ray_cast_for_face(
        self, face: TopoDS_Face, intersector: IntCurvesFace_ShapeIntersector, samples: int
    ) -> list[float]:
        """
        Returns the thicknesses found at each point UV for a given face.
        """
        thicknesses = []

        for u, v in yield_face_uv_grid(face, samples):
            thick = self.ray_cast_at_uv(face, u, v, intersector)

            if thick is not None and thick != float("inf"):
                thicknesses.append(thick)

        return thicknesses

    def ray_cast_at_uv(
        self,
        face: TopoDS_Face,
        u: float,
        v: float,
        intersector: IntCurvesFace_ShapeIntersector,
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

        intersector.Perform(ray, 0, float("inf"))

        # At acute corners, thickness is reported as zero. This is intuitively incorrect for
        # a DFM analysis. So we compare the normal directions of the origin face and the hit
        # face, and filter out any faces whose angle between them is more than 60° away
        # from being parallel, and return inf
        # 180 - 60 = 120 -> cos(120) = -0.5
        acute_filter_angle = 60
        threshold_rad = math.radians(180 - acute_filter_angle)
        acute_filter = math.cos(threshold_rad)

        if intersector.IsDone() and intersector.NbPnt() > 0:
            p_hit = intersector.Pnt(1)
            dist = point.Distance(p_hit)

            hit_face = intersector.Face(1)
            hit_u = intersector.UParameter(1)
            hit_v = intersector.VParameter(1)

            hit_normal = get_face_uv_normal(hit_face, hit_u, hit_v)

            if hit_normal:
                dot_prod = outward_norm.Dot(hit_normal)

                if dot_prod < acute_filter:
                    return dist

                return float("inf")
        return float("inf")
