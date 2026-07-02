# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import math
from typing import Any, Callable, Optional

import FreeCAD  # type: ignore

from OCP.TopoDS import TopoDS_Shape, TopoDS_Face, TopoDS
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.gp import gp_Dir
from OCP.GeomAbs import GeomAbs_Plane
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

from ...core.base.base_analyzer import BaseAnalyzer
from ...core.models import ProcessRequirement
from ...core.registries import register_analyzer
from ...core.utils.geometry import (
    EdgeIndex,
    FaceIndex,
    get_face_uv_center,
    get_face_uv_normal,
    yield_face_uv_grid,
)
from ...core.utils.mold import MoldSide, moldside_of_face


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

    def resolve_prefs(self, prefs: dict) -> None:
        pass

    def execute(
        self,
        shape,
        face_index: FaceIndex,
        edge_index: EdgeIndex,
        progress_cb=None,
        check_abort=None,
        **kwargs,
    ):
        """Runs a full draft analysis on an inputted shape."""
        self.pull_direction = kwargs.get(ProcessRequirement.PULL_DIRECTION.name, gp_Dir(0, 0, 1))
        self.samples = kwargs.get("samples", 20)

        self.face_index = face_index
        self.core_cavity_mapping = self.classify_moldside(shape)

        results = {}

        for face in self.iter_faces(shape, progress_cb, check_abort):
            result = self.get_draft_for_face(face)
            if result is not None:
                results[("Face", self.face_index.index_of(face))] = result

        return results

    def get_draft_for_face(self, face: TopoDS_Face) -> float:
        """Calculates the minimum draft angle in degrees."""
        draft_angle = None

        surface = BRepAdaptor_Surface(face)

        if surface.GetType() == GeomAbs_Plane:
            draft_angle = self.get_draft_for_plane(face)
        else:
            draft_angle = self.get_draft_for_curve(face)

        return draft_angle

    def get_draft_for_curve(self, face: TopoDS_Face) -> float:
        """
        Estimates the minimum draft angle (degrees) by sampling the surface normal across a UV grid.
        Returns the most critical (smallest) value found.
        """
        min_draft_angle = float("inf")

        for u, v in yield_face_uv_grid(face, self.samples, margin=0.01):
            normal_dir = get_face_uv_normal(face, u, v)
            if not normal_dir:
                FreeCAD.Console.PrintError(f"Normal returned None for face {face.__hash__()}")
                continue

            draft_angle = self.get_draft_for_dir(normal_dir)

            # Check if face belongs to the core, and flip the sign if True
            moldside = self.core_cavity_mapping[self.face_index.index_of(face)]
            if moldside == MoldSide.CORE:
                draft_angle = -draft_angle

            min_draft_angle = min(min_draft_angle, draft_angle)

        return min_draft_angle

    def get_draft_for_plane(self, face: TopoDS_Face) -> float:
        """
        Returns the draft angle for a face from its center.
        To be used on faces of GeomAbs_Plane type for efficiency.
        """
        u_mid, v_mid = get_face_uv_center(face)

        normal_dir = get_face_uv_normal(face, u_mid, v_mid)
        if not normal_dir:
            return 999

        draft_angle = self.get_draft_for_dir(normal_dir)

        # Check if face belongs to the core, and flip the sign if True
        moldside = self.core_cavity_mapping[self.face_index.index_of(face)]
        if moldside == MoldSide.CORE:
            draft_angle = -draft_angle

        return draft_angle

    def get_draft_for_dir(self, normal_dir: gp_Dir) -> float:
        """
        Computes the angle in degrees between a normal vector and the pull direction,
        where 0° represents a vertical face (parallel to pull).
        """
        angle_deg = math.degrees(self.pull_direction.Angle(normal_dir))

        if math.isclose(angle_deg, 0.0, abs_tol=1e-5):
            return 90.0
        if math.isclose(angle_deg, 180.0, abs_tol=1e-5):
            return -90.0

        return angle_deg - 90

    def classify_moldside(self, shape: TopoDS_Shape) -> dict[int, MoldSide]:
        """Returns a mapping of TopoDS_Face to MoldSide"""
        intersector = IntCurvesFace_ShapeIntersector()
        intersector.Load(shape, 1e-6)

        return {
            i: moldside_of_face(face, intersector, self.pull_direction)
            for i, face in enumerate(self.face_index, start=1)
        }
