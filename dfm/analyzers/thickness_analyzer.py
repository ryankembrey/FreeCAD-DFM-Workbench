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

from typing import Any
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face, topods
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.gp import gp_Pnt, gp_Lin
from OCC.Core.BRepTools import breptools
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCC.Core.GeomLProp import GeomLProp_SLProps
from OCC.Core.BRep import BRep_Tool

from .base_analyzer import BaseAnalyzer


class ThicknessAnalyzer(BaseAnalyzer):
    @property
    def analysis_type(self) -> str:
        return "THICKNESS_ANALYZER"

    @property
    def name(self) -> str:
        return "Thickness Analyzer"

    def execute(self, shape: TopoDS_Shape, **kwargs: Any) -> dict[TopoDS_Face, Any]:
        method = kwargs.get("method", "ray-cast")
        if not isinstance(method, str):
            raise ValueError(f"{self.name} requires a specified 'method'.")

        match method:
            case "ray-cast":
                result = self._perform_ray_cast(shape)
            case _:
                raise ValueError(f"Unknown method '{method}'.")
        return result

    def _perform_ray_cast(self, shape: TopoDS_Shape) -> dict[TopoDS_Face, Any]:
        """Calculates the minimum thickness for all faces of a given TopoDS_Shape."""
        results: dict[TopoDS_Face, list[float]] = {}
        face_explorer = TopExp_Explorer(shape, TopAbs_FACE)
        while face_explorer.More():
            current_face = topods.Face(face_explorer.Current())
            thicknesses = self._ray_cast_for_face(shape, current_face)
            results[current_face] = thicknesses
            face_explorer.Next()
        return results

    def _ray_cast_for_face(self, shape: TopoDS_Shape, face: TopoDS_Face) -> list[float]:
        """
        Returns the minimum thickness found for a given face using the ray-cast method.
        Increase the samples parameter for a finer search.
        """
        samples = 20
        thicknesses = []
        surface = BRepAdaptor_Surface(face, True)
        u_min, u_max, v_min, v_max = breptools.UVBounds(face)

        u_step = (u_max - u_min) / (samples - 1) if samples > 1 else 0
        v_step = (v_max - v_min) / (samples - 1) if samples > 1 else 0

        for i in range(samples):
            u = u_min + i * u_step
            for j in range(samples):
                v = v_min + j * v_step
                current_thickness = self._ray_cast_for_uv(shape, face, surface, u, v)
                thicknesses.append(current_thickness)
        return thicknesses

    def _ray_cast_for_uv(
        self,
        shape: TopoDS_Shape,
        face: TopoDS_Face,
        surface_adaptor: BRepAdaptor_Surface,
        u: float,
        v: float,
    ) -> float:
        """
        Returns the thickness at UV.
        """
        surface_geom = BRep_Tool.Surface(face)
        props = GeomLProp_SLProps(surface_geom, u, v, 1, 1e-6)

        if not props.IsNormalDefined():
            return float("inf")

        normal = props.Normal()
        point: gp_Pnt = surface_adaptor.Value(u, v)
        epsilon = 1e-7
        valid_distances = []

        # Test the original normal direction
        ray1 = gp_Lin(point, normal)
        intersector1 = IntCurvesFace_ShapeIntersector()
        intersector1.Load(shape, 1e-6)
        intersector1.Perform(ray1, epsilon, float("inf"))

        if intersector1.IsDone():
            num_hits = intersector1.NbPnt()
            # If number of hits is odd, ray is pointing inside
            if num_hits > 0 and num_hits % 2 != 0:
                dist = point.Distance(intersector1.Pnt(1))
                valid_distances.append(dist)

        # Only test the reversed normal direction if the first ray didn't find an inward hit
        if not valid_distances:
            ray2 = gp_Lin(point, normal.Reversed())
            intersector2 = IntCurvesFace_ShapeIntersector()
            intersector2.Load(shape, 1e-6)
            intersector2.Perform(ray2, epsilon, float("inf"))

            if intersector2.IsDone():
                num_hits = intersector2.NbPnt()
                # If number of hits is odd, ray is pointing inside
                if num_hits > 0 and num_hits % 2 != 0:
                    dist = point.Distance(intersector2.Pnt(1))
                    valid_distances.append(dist)

        if valid_distances:
            return min(valid_distances)
        else:
            return float("inf")  # Error
