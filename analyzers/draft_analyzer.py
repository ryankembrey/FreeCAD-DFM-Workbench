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

from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face, topods
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.gp import gp_Dir, gp_Pnt, gp_Vec
from OCC.Core.GeomAbs import GeomAbs_Plane

from analyzers import BaseAnalyzer


class DraftAnalyzer(BaseAnalyzer):
    """
    Analyzes the minimum draft angle for all faces of a shape.
    """

    @property
    def analysis_type(self):
        return

    @property
    def name(self) -> str:
        return "Draft Analyzer"

    def execute(self, shape: TopoDS_Shape, **kwargs: Any) -> dict[TopoDS_Face, float]:
        # log.info(f"Executing {self.name}…")

        pull_direction = kwargs.get("pull_direction")
        samples = kwargs.get("samples", 20)

        if not isinstance(pull_direction, gp_Dir):
            raise ValueError(f"{self.name} requires a 'pull_direction' of type gp_Dir.")

        face_explorer = TopExp_Explorer(shape, TopAbs_FACE)

        results: dict[TopoDS_Face, float] = {}

        while face_explorer.More():
            current_face = topods.Face(face_explorer.Current())
            draft_result = self._get_draft_for_face(current_face, pull_direction, samples)

            if draft_result is not None:
                results[current_face] = draft_result

            face_explorer.Next()
        return results

    def _get_draft_for_face(
        self, face: TopoDS_Face, pull_direction: gp_Dir, samples: int
    ) -> float | None:
        """
        Calculates the minimum draft angle for a single face.
        This is a private method of the class, encapsulating the logic.
        """
        surface = BRepAdaptor_Surface(face, True)

        # --- Use a single, consistent method for all surface types ---
        u_min, u_max = surface.FirstUParameter(), surface.LastUParameter()
        v_min, v_max = surface.FirstVParameter(), surface.LastVParameter()

        u_range = u_max - u_min
        v_range = v_max - v_min

        if u_range == 0 or v_range == 0:
            print("Degenerated face detected (zero UV range). Skipping.")
            return None

        num_samples = 2 if surface.GetType() == GeomAbs_Plane else samples
        if num_samples <= 1:
            num_samples = 2

        u_step = u_range / (num_samples - 1)
        v_step = v_range / (num_samples - 1)

        min_draft_angle_rad = math.pi

        point = gp_Pnt()
        u_tangent, v_tangent = gp_Vec(), gp_Vec()

        for i in range(num_samples):
            u = u_min + i * u_step
            for j in range(num_samples):
                v = v_min + j * v_step
                surface.D1(u, v, point, u_tangent, v_tangent)

                normal_vec = u_tangent.Crossed(v_tangent)
                if normal_vec.Magnitude() < 1e-9:
                    continue

                normal_dir = gp_Dir(normal_vec)

                if face.Orientation() == TopAbs_REVERSED:
                    normal_dir.Reverse()

                angle_rad = pull_direction.Angle(normal_dir)
                draft_angle_rad = (math.pi / 2) - angle_rad
                min_draft_angle_rad = min(min_draft_angle_rad, draft_angle_rad)

        face_id: int = face.__hash__()
        print(f"Face ID: [{face_id}] | Draft = {-1 * math.degrees(min_draft_angle_rad):.2f}°")
        return -1 * math.degrees(min_draft_angle_rad)
