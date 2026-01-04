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
import FreeCAD

from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face, topods
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.gp import gp_Pnt, gp_Lin
from OCC.Core.BRepTools import breptools
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector

from dfm.core.base_analyzer import BaseAnalyzer
from dfm.registries import register_analyzer
from dfm.utils import get_face_uv_normal


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
        surface = BRepAdaptor_Surface(face, True)
        u_min, u_max, v_min, v_max = breptools.UVBounds(face)

        u_step = (u_max - u_min) / (samples - 1) if samples > 1 else 0
        v_step = (v_max - v_min) / (samples - 1) if samples > 1 else 0

        for i in range(samples):
            u = u_min + i * u_step
            for j in range(samples):
                v = v_min + j * v_step

                thick = self.ray_cast_at_uv(face, surface, u, v, intersector)

                if thick is not None and thick != float("inf"):
                    thicknesses.append(thick)

        return thicknesses

    def ray_cast_at_uv(
        self,
        face: TopoDS_Face,
        surface_adaptor: BRepAdaptor_Surface,
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
        point: gp_Pnt = surface_adaptor.Value(u, v)

        epsilon = 1e-4

        ray = gp_Lin(point, inward_norm)

        intersector.Perform(ray, epsilon, float("inf"))

        if intersector.IsDone():
            if intersector.NbPnt() > 0:
                return point.Distance(intersector.Pnt(1))

        return float("inf")
