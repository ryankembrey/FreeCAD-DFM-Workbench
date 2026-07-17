# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import math
from typing import Any, Callable, Optional

from OCP.TopoDS import TopoDS_Shape, TopoDS_Face, TopoDS_Edge, TopoDS
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.gp import gp_Dir, gp_Pnt, gp_Vec
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_EDGE, TopAbs_IN, TopAbs_ON

from ...core.base.base_analyzer import BaseAnalyzer
from ...core.models import ProcessRequirement
from ...core.registries import register_analyzer
from ...core.utils.geometry import (
    EdgeIndex,
    FaceIndex,
    calculate_bed_height,
    get_extent_along,
    get_face_uv_center,
    get_face_uv_normal,
    is_flat,
    project_onto,
)


@register_analyzer("BRIDGE_SPAN_ANALYZER")
class BridgeSpanAnalyzer(BaseAnalyzer):
    """
    Measures bridge lengths with respect to a print orientation.
    """

    horizontal_tolerance_deg: float
    bed_tolerance: float
    probe_depth: float
    classifier_tol: float

    print_orientation: gp_Dir
    face_index: FaceIndex
    bed_height: float
    solid_classifier: BRepClass3d_SolidClassifier

    @property
    def analysis_type(self) -> str:
        return "BRIDGE_SPAN_ANALYZER"

    @property
    def requirements(self) -> set[ProcessRequirement]:
        return {ProcessRequirement.PRINT_ORIENTATION}

    @property
    def name(self) -> str:
        return "Bridge Span Analyzer"

    def resolve_prefs(self, prefs: dict) -> None:
        self.horizontal_tolerance_deg = prefs.get("BridgeHorizontalTolerance", 1.0)
        self.bed_tolerance = prefs.get("BridgeBedTolerance", 0.5)
        self.probe_depth = prefs.get("BridgeProbeDepth", 0.5)
        self.classifier_tol = prefs.get("BridgeClassifierTol", 1e-6)

    def execute(
        self,
        shape: TopoDS_Shape,
        face_index: FaceIndex,
        edge_index: EdgeIndex,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> dict[tuple[str, int], float]:
        """Returns the bridge span (mm) for each bridging face in a shape."""
        self.resolve_prefs(kwargs.get("prefs", {}))

        self.print_orientation = kwargs.get(
            ProcessRequirement.PRINT_ORIENTATION.name, gp_Dir(0, 0, 1)
        )
        self.bed_height = calculate_bed_height(shape, self.print_orientation)
        self.solid_classifier = BRepClass3d_SolidClassifier(shape)

        results: dict[tuple[str, int], float] = {}
        for face in self.iter_faces(shape, progress_cb, check_abort):
            span = self._get_bridge_span_for_face(face)
            if span is not None and span > 0.0:
                results[face_index.key_of(face)] = span

        return results

    def _get_bridge_span_for_face(self, face: TopoDS_Face) -> Optional[float]:
        if not self._is_bridge_candidate(face):
            return None

        spans = [self._extent_along(face, direction) for direction in self._bridge_directions(face)]
        return min(spans) if spans else None

    def _is_bridge_candidate(self, face: TopoDS_Face) -> bool:
        if not is_flat(face):
            return False

        u_mid, v_mid = get_face_uv_center(face)

        normal = get_face_uv_normal(face, u_mid, v_mid)
        if not normal:
            return False

        angle_from_down = math.degrees(normal.Angle(self.print_orientation.Reversed()))
        if angle_from_down > self.horizontal_tolerance_deg:
            return False

        surface = BRepAdaptor_Surface(face)
        height = self._height_of(surface.Value(u_mid, v_mid))
        return not math.isclose(height, self.bed_height, abs_tol=self.bed_tolerance)

    def _bridge_directions(self, face: TopoDS_Face) -> list[gp_Dir]:
        directions: list[gp_Dir] = []

        explorer = TopExp_Explorer(face, TopAbs_EDGE)
        while explorer.More():
            edge = TopoDS.Edge_s(explorer.Current())  # type: ignore
            explorer.Next()

            if BRep_Tool.Degenerated_s(edge):
                continue
            if not self._has_material_below(self._edge_midpoint(edge)):
                continue

            direction = self._perpendicular_to(edge)
            if direction is not None:
                directions.append(direction)

        return directions

    def _has_material_below(self, pnt: gp_Pnt) -> bool:
        probe = gp_Pnt(
            pnt.X() - self.print_orientation.X() * self.probe_depth,
            pnt.Y() - self.print_orientation.Y() * self.probe_depth,
            pnt.Z() - self.print_orientation.Z() * self.probe_depth,
        )
        self.solid_classifier.Perform(probe, self.classifier_tol)
        return self.solid_classifier.State() in (TopAbs_IN, TopAbs_ON)

    def _perpendicular_to(self, edge: TopoDS_Edge) -> Optional[gp_Dir]:
        curve = BRepAdaptor_Curve(edge)
        mid = (curve.FirstParameter() + curve.LastParameter()) / 2.0

        pnt, tangent = gp_Pnt(), gp_Vec()
        curve.D1(mid, pnt, tangent)

        axis = gp_Vec(self.print_orientation)
        flat = tangent.Subtracted(axis.Multiplied(tangent.Dot(axis)))
        if flat.Magnitude() < 1e-9:
            return None

        perpendicular = flat.Crossed(axis)
        if perpendicular.Magnitude() < 1e-9:
            return None

        return gp_Dir(perpendicular)

    def _extent_along(self, face, direction) -> float:
        lo, hi = get_extent_along(face, direction)
        return hi - lo

    def _height_of(self, pnt) -> float:
        return project_onto(pnt, self.print_orientation)

    def _edge_midpoint(self, edge: TopoDS_Edge) -> gp_Pnt:
        curve = BRepAdaptor_Curve(edge)
        return curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2.0)
