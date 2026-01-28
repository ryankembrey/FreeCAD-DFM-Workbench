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
from OCC.Core.gp import gp_Pnt, gp_Lin, gp_Dir
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector

from dfm.core import BaseAnalyzer
from dfm.utils import yield_face_uv_grid, get_face_uv_normal, get_point_from_uv
from dfm.registries import register_analyzer


@register_analyzer("UNDERCUT_ANALYZER")
class UndercutAnalyzer(BaseAnalyzer):
    @property
    def analysis_type(self) -> str:
        return "UNDERCUT_ANALYZER"

    @property
    def name(self) -> str:
        return "Undercut Analyzer"

    def execute(self, shape: TopoDS_Shape, **kwargs: Any) -> dict[TopoDS_Face, Any]:
        pull_direction = kwargs.get("pull_direction", gp_Dir(0, 0, 1))
        samples = kwargs.get("samples", 10)

        intersector = IntCurvesFace_ShapeIntersector()
        intersector.Load(shape, 1e-6)

        results: dict[TopoDS_Face, float] = {}

        face_explorer = TopExp_Explorer(shape, TopAbs_FACE)  # type: ignore

        while face_explorer.More():
            current_face = topods.Face(face_explorer.Current())

            undercut_ratio = self._analyze_face(current_face, intersector, pull_direction, samples)

            if undercut_ratio > 0.0:
                results[current_face] = undercut_ratio

            face_explorer.Next()

        return results

    def _analyze_face(self, face: TopoDS_Face, intersector, pull_direction, samples):
        """
        Returns a score from 0.0 (Safe) to 1.0 (Completely Trapped).
        """
        total_points = 0
        trapped_points = 0

        surface = BRepAdaptor_Surface(face, True)

        for u, v in yield_face_uv_grid(face, samples, 0.05):
            total_points += 1

            if self._is_point_trapped(face, u, v, intersector, pull_direction):
                trapped_points += 1

        if total_points == 0:
            return 0.0

        return float(trapped_points) / float(total_points)

    def _is_point_trapped(
        self,
        face: TopoDS_Face,
        u: float,
        v: float,
        intersector: IntCurvesFace_ShapeIntersector,
        pull_direction: gp_Dir,
    ):
        normal = get_face_uv_normal(face, u, v)

        if not normal:
            return None

        epsilon = 1e-3
        point = get_point_from_uv(face, normal, u, v, epsilon)

        ray_up = gp_Lin(point, pull_direction)
        intersector.Perform(ray_up, 0, float("inf"))

        blocked_top = intersector.IsDone() and intersector.NbPnt() > 0

        if not blocked_top:
            return False

        ray_down = gp_Lin(point, pull_direction.Reversed())
        intersector.Perform(ray_down, 0, float("inf"))

        blocked_bottom = intersector.IsDone() and intersector.NbPnt() > 0

        return blocked_top and blocked_bottom
