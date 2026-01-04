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

import math
from typing import Any

import FreeCAD

from OCC.Core.Geom import Geom_Surface
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face, topods
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.gp import gp_Dir
from OCC.Core.GeomAbs import GeomAbs_Plane
from OCC.Core.BRepTools import breptools
from OCC.Core.BRep import BRep_Tool
from OCC.Core.GeomLProp import GeomLProp_SLProps

from dfm.core.base_analyzer import BaseAnalyzer
from dfm.registries import register_analyzer


@register_analyzer("DRAFT_ANALYZER")
class DraftAnalyzer(BaseAnalyzer):
    """
    Analyzes the minimum draft angle for all faces of a shape.
    """

    @property
    def analysis_type(self) -> str:
        return "DRAFT_ANALYZER"

    @property
    def name(self) -> str:
        return "Draft Analyzer"

    def execute(self, shape: TopoDS_Shape, **kwargs: Any) -> dict[TopoDS_Face, float]:
        """
        Runs a draft analysis on the inputted TopoDS_Shape.
        **kwargs should hold:
            - A pull_direction of type gp_Dir
            - A number of samples of type int.

        The number of samples controls accuracy of the results at the expense of compute time.
        """

        pull_direction = kwargs.get("pull_direction", gp_Dir(0, 0, 1))
        samples = kwargs.get("samples", 20)

        if not isinstance(pull_direction, gp_Dir):
            raise ValueError(f"{self.name} requires a 'pull_direction' of type gp_Dir.")

        face_explorer = TopExp_Explorer(shape, TopAbs_FACE)  # type: ignore

        results: dict[TopoDS_Face, float] = {}

        while face_explorer.More():
            current_face = topods.Face(face_explorer.Current())
            draft_result = self.get_draft_for_face(current_face, pull_direction, samples)

            if draft_result is not None:
                results[current_face] = draft_result

            face_explorer.Next()
        return results

    def get_draft_for_face(self, face: TopoDS_Face, pull_direction: gp_Dir, samples: int) -> float:
        """Returns the draft angle for any TopoDS_Face."""
        draft_angle = None

        surface = BRepAdaptor_Surface(face)

        if surface.GetType == GeomAbs_Plane:
            draft_angle = self.get_draft_for_plane(face, pull_direction)
        else:
            draft_angle = self.get_draft_for_curve(face, pull_direction, samples)

        return draft_angle

    def get_draft_for_curve(self, face: TopoDS_Face, pull_direction: gp_Dir, samples: int) -> float:
        """Returns the draft angle for any TopoDS_Face by sampling in a grid and finding the minimum draft angle."""
        surface = BRepAdaptor_Surface(face, True)

        u_min, u_max = surface.FirstUParameter(), surface.LastUParameter()
        v_min, v_max = surface.FirstVParameter(), surface.LastVParameter()

        u_range = u_max - u_min
        v_range = v_max - v_min
        u_step = u_range / (samples - 1)
        v_step = v_range / (samples - 1)

        min_draft_angle = 180

        for i in range(samples):
            u = u_min + i * u_step
            for j in range(samples):
                v = v_min + j * v_step
                normal_dir = self.get_face_uv_normal(face, u, v)
                if not normal_dir:
                    FreeCAD.Console.PrintError(f"Normal returned None for face {face.__hash__()}")
                    continue

                draft_angle = self.get_draft_for_dir(normal_dir, pull_direction)
                min_draft_angle = min(min_draft_angle, draft_angle)

        return min_draft_angle

    def get_draft_for_plane(self, face: TopoDS_Face, pull_direction: gp_Dir) -> float:
        """
        Returns the draft angle for a face from its center.
        To be used on faces of GeomAbs_Plane type for efficiency.
        """
        u_mid, v_mid = self.get_face_uv_center(face)

        normal_dir = self.get_face_uv_normal(face, u_mid, v_mid)
        if not normal_dir:
            return 999

        return self.get_draft_for_dir(normal_dir, pull_direction)

    def get_draft_for_dir(self, normal_dir: gp_Dir, pull_direction: gp_Dir) -> float:
        """Returns the draft angle (degrees) of a gp_Dir with respect to the pull_direction"""
        angle_deg = math.degrees(pull_direction.Angle(normal_dir))

        if math.isclose(angle_deg, 0.0, abs_tol=1e-5):
            return 90.0
        if math.isclose(angle_deg, 180.0, abs_tol=1e-5):
            return -90.0

        return angle_deg - 90

    def get_face_uv_center(self, face: TopoDS_Face) -> tuple[(float, float)]:
        """Returns the center of the UV parametric space for a TopoDS_Face."""
        u_min, u_max, v_min, v_max = breptools.UVBounds(face)
        u_mid: float = (u_max + u_min) / 2
        v_mid: float = (v_max + v_min) / 2

        return (u_mid, v_mid)

    def get_face_uv_normal(self, face: TopoDS_Face, u: float, v: float) -> gp_Dir | None:
        """Returns the normal of a TopoDS_Face at UV"""

        surface = BRep_Tool.Surface(face)
        props = GeomLProp_SLProps(surface, u, v, 1, 1e-6)

        if props.IsNormalDefined():
            pnt = props.Value()
            norm = props.Normal()

            if not face.Location().IsIdentity():
                pnt.Transform(face.Location().Transformation())
                norm.Transform(face.Location().Transformation())

            if face.Orientation() == TopAbs_REVERSED:
                norm.Reverse()

            return norm
