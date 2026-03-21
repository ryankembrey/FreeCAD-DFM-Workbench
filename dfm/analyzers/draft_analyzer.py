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
from typing import Any, Callable, Optional

import FreeCAD  # type: ignore

from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face, topods
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.gp import gp_Dir, gp_Lin, gp_Vec
from OCC.Core.GeomAbs import GeomAbs_Plane
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector

from dfm.core.base_analyzer import BaseAnalyzer
from dfm.models import ProcessRequirement
from dfm.registries import register_analyzer
from dfm.utils.geometry import (
    get_face_uv_center,
    get_face_uv_normal,
    yield_face_uv_grid,
)
from dfm.utils.mold import MoldSide, moldside_of_face


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
    def requirements(self) -> set[ProcessRequirement]:
        return {ProcessRequirement.PULL_DIRECTION}

    @property
    def name(self) -> str:
        return "Draft Analyzer"

    def execute(self, shape, progress_cb=None, check_abort=None, **kwargs):
        """Runs a full draft analysis on an inputted shape."""
        pull_direction = kwargs.get("pull_direction", gp_Dir(0, 0, 1))
        samples = kwargs.get("samples", 20)

        self.core_cavity_mapping = self.classify_moldside(shape, pull_direction)

        results = {}

        for face in self.iter_faces(shape, progress_cb, check_abort):
            result = self.get_draft_for_face(face, pull_direction, samples)
            if result is not None:
                results[face] = result

        return results

    def get_draft_for_face(self, face: TopoDS_Face, pull_direction: gp_Dir, samples: int) -> float:
        """Calculates the minimum draft angle in degrees."""
        draft_angle = None

        surface = BRepAdaptor_Surface(face)

        if surface.GetType() == GeomAbs_Plane:
            draft_angle = self.get_draft_for_plane(face, pull_direction)
        else:
            draft_angle = self.get_draft_for_curve(face, pull_direction, samples)

        return draft_angle

    def get_draft_for_curve(self, face: TopoDS_Face, pull_direction: gp_Dir, samples: int) -> float:
        """
        Estimates the minimum draft angle (degrees) by sampling the surface normal across a UV grid.
        Returns the most critical (smallest) value found.
        """
        min_draft_angle = float("inf")

        for u, v in yield_face_uv_grid(face, samples, margin=0.01):
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

            face_mapping[current_face] = moldside_of_face(current_face, intersector, pull_direction)

            face_explorer.Next()
        return face_mapping
