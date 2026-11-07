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
from ...core.utils.mold import MoldSide, SideActionValue, moldside_of_face, releasing_side_action


@register_analyzer("DRAFT_ANALYZER")
class DraftAnalyzer(BaseAnalyzer):
    """
    Analyzes and classifies the draft angles of all faces in a shape
    relative to a specific injection molding pull direction.

    With side actions (slides, lifters, pickouts), a face the main pull
    can't release but a side action can is measured along that side
    action's direction instead, and its value says which one
    (SideActionValue).
    """

    parallel = True
    relative_cost = 8  # ~ms per face (see BaseAnalyzer.relative_cost)

    @property
    def analysis_type(self) -> str:
        return "DRAFT_ANALYZER"

    @property
    def requirements(self) -> set[ProcessRequirement]:
        return {ProcessRequirement.PULL_DIRECTION, ProcessRequirement.SIDE_ACTIONS}

    @property
    def name(self) -> str:
        return "Draft Analyzer"

    def resolve_prefs(self, prefs: dict) -> None:
        self.samples = prefs.get("DraftSamples", 20)
        self.margin = prefs.get("DraftMargin", 0.01)
        self.classifier_tol = prefs.get("DraftClassifierTol", 1e-6)
        self.side_samples = prefs.get("DraftSideActionSamples", 4)

    def prepare(self, shape, face_index: FaceIndex, edge_index: EdgeIndex, **kwargs) -> None:
        super().prepare(shape, face_index, edge_index, **kwargs)
        self.pull_direction = kwargs.get(ProcessRequirement.PULL_DIRECTION.name, gp_Dir(0, 0, 1))

        self.side_actions = [d for d in (kwargs.get(ProcessRequirement.SIDE_ACTIONS.name) or []) if d]

        self.face_index = face_index
        # Core or cavity side, worked out per face when it is analyzed.
        self.core_cavity_mapping = _MoldSides(self)
        self._intersector = IntCurvesFace_ShapeIntersector()
        self._intersector.Load(shape, self.classifier_tol)

    def analyze(self, face: TopoDS_Face):
        """The smallest draft of one face (a SideActionValue if a side action forms it)."""
        side = 0
        if self.side_actions:
            side = releasing_side_action(
                face, self._intersector, self.pull_direction, self.side_actions,
                self.side_samples,
            )
        if side:
            result = self.get_side_action_draft(face, self.side_actions[side - 1])
            if result is not None:
                result = SideActionValue(result, side)
            return result
        return self.get_draft_for_face(face)

    def get_side_action_draft(self, face: TopoDS_Face, direction: gp_Dir) -> Optional[float]:
        """The smallest draft of a face formed by a side action moving along *direction*.

        The side action pulls away from the face, so draft is measured the
        same way as for faces pulled along the main direction: 90 degrees
        facing the direction, 0 parallel to it, negative if it leans back.
        """
        if BRepAdaptor_Surface(face).GetType() == GeomAbs_Plane:
            uvs = [get_face_uv_center(face)]
        else:
            uvs = list(yield_face_uv_grid(face, self.samples, margin=self.margin))
        smallest = None
        for u, v in uvs:
            normal = get_face_uv_normal(face, u, v)
            if not normal:
                continue
            angle = math.degrees(direction.Angle(normal))
            draft = 90.0 if math.isclose(angle, 0.0, abs_tol=1e-5) else 90.0 - angle
            smallest = draft if smallest is None else min(smallest, draft)
        return smallest

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

        for u, v in yield_face_uv_grid(face, self.samples, margin=self.margin):
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

    def moldside_of(self, face: TopoDS_Face) -> MoldSide:
        """Core or cavity side of one face (see core.utils.mold)."""
        return moldside_of_face(face, self._intersector, self.pull_direction)

    def classify_moldside(self, shape: TopoDS_Shape) -> dict[int, MoldSide]:
        """Returns a mapping of TopoDS_Face to MoldSide"""
        intersector = IntCurvesFace_ShapeIntersector()
        intersector.Load(shape, self.classifier_tol)

        return {
            i: moldside_of_face(face, intersector, self.pull_direction)
            for i, face in enumerate(self.face_index, start=1)
        }


class _MoldSides(dict):
    """Face index -> MoldSide, each worked out the first time it is asked for.

    Classifying is a few rays per face; doing it per face, as faces are
    analyzed, lets the work be shared between processes with the faces.
    """

    def __init__(self, analyzer: DraftAnalyzer):
        super().__init__()
        self._analyzer = analyzer

    def __missing__(self, index: int) -> MoldSide:
        side = self._analyzer.moldside_of(self._analyzer.face_index.face_at(index))
        self[index] = side
        return side
