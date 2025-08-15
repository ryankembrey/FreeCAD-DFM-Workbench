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
from typing import Dict, Any

# OCC imports
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face, topods
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.gp import gp_Dir, gp_Pnt, gp_Vec

# Local
from analyzers.BaseAnalyzer import BaseAnalyzer
from Enums import AnalysisType


def get_face_normal_at_center(face: TopoDS_Face) -> gp_Dir:
    surface = BRepAdaptor_Surface(face, True)

    u_min = surface.FirstUParameter()
    u_max = surface.LastUParameter()
    v_min = surface.FirstVParameter()
    v_max = surface.LastVParameter()

    u_mid = (u_max - u_min) / 2.0
    v_mid = (v_max - v_min) / 2.0

    point_at_center = gp_Pnt()

    u_tangent = gp_Vec()
    v_tangent = gp_Vec()

    surface.D1(u_mid, v_mid, point_at_center, u_tangent, v_tangent)

    normal_vector = u_tangent.Crossed(v_tangent)

    return gp_Dir(normal_vector)


class DraftAnalyzer(BaseAnalyzer):
    @property
    def analysis_type(self) -> AnalysisType:
        return AnalysisType.DRAFT_ANGLE

    @property
    def name(self):
        return "Draft Analyzer"

    def execute(self, shape: TopoDS_Shape, **kwargs: Any) -> Dict[TopoDS_Face, Any]:
        print(f"Executing {self.name}…")

        pull_direction = kwargs.get("pull_direction")
        if not isinstance(pull_direction, gp_Dir):
            raise ValueError(f"{self.name} requires a 'pull_direction of type gp_Dir.")

        results: Dict[TopoDS_Face, float] = {}

        face_explorer = TopExp_Explorer(shape, TopAbs_FACE)

        while face_explorer.More():
            current_item = face_explorer.Current()

            current_face = topods.Face(current_item)
            face_normal = get_face_normal_at_center(current_face)

            angle_rad = pull_direction.Angle(face_normal)

            draft_angle_rad = abs((math.pi / 2) - angle_rad)
            draft_angle_deg = math.degrees(draft_angle_rad)

            results[current_face] = draft_angle_deg

            face_explorer.Next()

        print(f"{self.name} finished.")
        return results
