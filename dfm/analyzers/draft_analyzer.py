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
from enum import Enum, auto

import FreeCAD

from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face, topods
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.gp import gp_Dir, gp_Lin
from OCC.Core.GeomAbs import GeomAbs_Plane
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector

from dfm.core.base_analyzer import BaseAnalyzer
from dfm.registries import register_analyzer
from dfm.utils import get_face_uv_center, get_face_uv_normal, get_point_from_uv, yield_face_uv_grid


class MoldSide(Enum):
    CORE = auto()
    CAVITY = auto()


@register_analyzer("DRAFT_ANALYZER")
class DraftAnalyzer(BaseAnalyzer):
    """
    Analyzes and classifies the draft angles of all faces in a shape
    relative to a specific injection molding pull direction.
    """

    @property
    def analysis_type(self) -> str:
        return "DRAFT_ANALYZER"

    @property
    def name(self) -> str:
        return "Draft Analyzer"

    def execute(self, shape: TopoDS_Shape, **kwargs: Any) -> dict[TopoDS_Face, float]:
        """
        Runs a full draft analysis on an inputted shape.
        **kwargs:
            pull_direction (gp_Dir): Direction of mold opening. Defaults to +Z.
            samples (int): Grid density for curved face analysis.

        Returns:
            Mapping of TopoDS_Face to its minimum draft angle in degrees.
        """

        pull_direction = kwargs.get("pull_direction", gp_Dir(0, 0, 1))
        samples = kwargs.get("samples", 20)

        if not isinstance(pull_direction, gp_Dir):
            raise ValueError(f"{self.name} requires a 'pull_direction' of type gp_Dir.")

        face_explorer = TopExp_Explorer(shape, TopAbs_FACE)  # type: ignore

        self.core_cavity_mapping = self.classify_moldside(shape, pull_direction)

        results: dict[TopoDS_Face, float] = {}

        while face_explorer.More():
            current_face = topods.Face(face_explorer.Current())
            draft_result = self.get_draft_for_face(current_face, pull_direction, samples)

            if draft_result is not None:
                results[current_face] = draft_result

            face_explorer.Next()
        return results

    def get_draft_for_face(self, face: TopoDS_Face, pull_direction: gp_Dir, samples: int) -> float:
        """Calculates the minimum draft angle in degrees."""
        draft_angle = None

        surface = BRepAdaptor_Surface(face)

        if surface.GetType() == GeomAbs_Plane:
            draft_angle = self.get_draft_for_plane(face, pull_direction)
        else:
            draft_angle = self.get_draft_for_curve(face, pull_direction, samples)

        if self.core_cavity_mapping[face] == MoldSide.CORE:
            print("Core")
        else:
            print("Cavity")

        print(f"D: {draft_angle}")
        return draft_angle

    def get_draft_for_curve(self, face: TopoDS_Face, pull_direction: gp_Dir, samples: int) -> float:
        """
        Estimates the minimum draft angle (degrees) by sampling the surface normal across a UV grid.
        Returns the most critical (smallest) value found.
        """
        min_draft_angle = 180

        for u, v in yield_face_uv_grid(face, samples):
            normal_dir = get_face_uv_normal(face, u, v)
            if not normal_dir:
                FreeCAD.Console.PrintError(f"Normal returned None for face {face.__hash__()}")
                continue

            draft_angle = self.get_draft_for_dir(normal_dir, pull_direction)

            # Check if face belongs to the core, and flip the sign if True
            moldside = self.core_cavity_mapping[face]
            if moldside == MoldSide.CORE:
                draft_angle = -draft_angle

            min_draft_angle = min(min_draft_angle, draft_angle)

        return min_draft_angle

    def get_draft_for_plane(self, face: TopoDS_Face, pull_direction: gp_Dir) -> float:
        """
        Returns the draft angle for a face from its center.
        To be used on faces of GeomAbs_Plane type for efficiency.
        """
        u_mid, v_mid = get_face_uv_center(face)

        normal_dir = get_face_uv_normal(face, u_mid, v_mid)
        if not normal_dir:
            return 999

        draft_angle = self.get_draft_for_dir(normal_dir, pull_direction)

        # Check if face belongs to the core, and flip the sign if True
        moldside = self.core_cavity_mapping[face]
        if moldside == MoldSide.CORE:
            draft_angle = -draft_angle

        return draft_angle

    def get_draft_for_dir(self, normal_dir: gp_Dir, pull_direction: gp_Dir) -> float:
        """
        Computes the angle in degrees between a normal vector and the pull direction,
        where 0° represents a vertical face (parallel to pull).
        """
        angle_deg = math.degrees(pull_direction.Angle(normal_dir))

        if math.isclose(angle_deg, 0.0, abs_tol=1e-5):
            return 90.0
        if math.isclose(angle_deg, 180.0, abs_tol=1e-5):
            return -90.0

        return angle_deg - 90

    def classify_moldside(
        self, shape: TopoDS_Shape, pull_direction: gp_Dir
    ) -> dict[TopoDS_Face, MoldSide]:
        """Returns a mapping of TopoDS_Face to MoldSide"""
        face_explorer = TopExp_Explorer(shape, TopAbs_FACE)  # type: ignore

        face_mapping: dict[TopoDS_Face, MoldSide] = {}

        intersector = IntCurvesFace_ShapeIntersector()
        intersector.Load(shape, 1e-6)

        while face_explorer.More():
            current_face = topods.Face(face_explorer.Current())

            face_mapping[current_face] = self.moldside_of_face(
                current_face, intersector, pull_direction
            )

            face_explorer.Next()
        return face_mapping

    def moldside_of_face(
        self, face: TopoDS_Face, intersector: IntCurvesFace_ShapeIntersector, pull_direction: gp_Dir
    ) -> MoldSide:
        """
        Classifies a face as CORE or CAVITY using ray-casting.

        Core faces (inner) require sign-inversion of the draft angle to represent de-molding logic correctly.
        """
        u, v = get_face_uv_center(face)
        norm = get_face_uv_normal(face, u, v)
        if not norm:
            # Fallback to cavity
            return MoldSide.CAVITY

        epsilon = 1e-3

        point = get_point_from_uv(face, norm, u, v, epsilon)

        up_hits = 0
        down_hits = 0

        intersector.Perform(gp_Lin(point, pull_direction), 0, float("inf"))
        if intersector.IsDone():
            up_hits = intersector.NbPnt()

        intersector.Perform(gp_Lin(point, pull_direction.Reversed()), 0, float("inf"))
        if intersector.IsDone():
            down_hits = intersector.NbPnt()

        if up_hits <= down_hits and down_hits < 4:
            return MoldSide.CORE

        return MoldSide.CAVITY
