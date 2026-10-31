# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Analyzer that computes cusp height on upward-facing sloped surfaces.

When a sloped surface is built layer by layer, each layer boundary
creates a small step (cusp) perpendicular to the ideal surface.
Cusp height depends on the layer height and the angle between the
surface normal and the print orientation:

    cusp_height = layer_height * cos(theta)

Shallow surfaces (nearly horizontal) produce the tallest cusps and
the most visible staircase effect. Surfaces within a few degrees of
horizontal are excluded because they print as flat top layers with
no visible stepping.
"""

import math
from typing import Any, Callable, Optional

from OCP.TopoDS import TopoDS_Shape, TopoDS_Face
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.gp import gp_Dir
from OCP.GeomAbs import GeomAbs_Plane

from ...core.base.base_analyzer import BaseAnalyzer
from ...core.models import ProcessRequirement
from ...core.registries import register_analyzer
from ...core.utils.geometry import (
    EdgeIndex,
    FaceIndex,
    calculate_bed_height,
    get_face_uv_center,
    get_face_uv_normal,
    is_on_bed,
    yield_face_uv_grid,
)


@register_analyzer("CUSP_ANALYZER")
class CuspAnalyzer(BaseAnalyzer):
    """Computes cusp height (mm) for upward-facing sloped surfaces."""

    @property
    def analysis_type(self) -> str:
        return "CUSP_ANALYZER"

    @property
    def requirements(self) -> set[ProcessRequirement]:
        return {ProcessRequirement.PRINT_ORIENTATION, ProcessRequirement.LAYER_HEIGHT}

    @property
    def name(self) -> str:
        return "Cusp Analyzer"

    def resolve_prefs(self, prefs: dict) -> None:
        self.samples = prefs.get("StaircaseSamples", 15)
        self.margin = prefs.get("StaircaseMargin", 0.01)
        self.bed_tolerance = prefs.get("StaircaseBedTolerance", 0.5)
        # Faces within this angle of horizontal are treated as flat top
        # layers with no visible staircase effect.
        self.horizontal_threshold_deg = prefs.get("StaircaseHorizontalThreshold", 0.1)

    def execute(
        self,
        shape: TopoDS_Shape,
        face_index: FaceIndex,
        edge_index: EdgeIndex,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> dict[tuple[str, int], float]:
        self.resolve_prefs(kwargs.get("prefs", {}))

        self.print_orientation: gp_Dir = kwargs.get(
            ProcessRequirement.PRINT_ORIENTATION.name, gp_Dir(0, 0, 1)
        )
        self.layer_height: float = kwargs.get(ProcessRequirement.LAYER_HEIGHT.name, 0.2)
        self.bed_height = calculate_bed_height(shape, self.print_orientation)

        results: dict[tuple[str, int], float] = {}
        for face in self.iter_faces(shape, progress_cb, check_abort):
            cusp = self._cusp_height_for_face(face)
            if cusp is not None and cusp > 0.0:
                results[face_index.key_of(face)] = cusp

        return results

    def _cusp_height_for_face(self, face: TopoDS_Face) -> Optional[float]:
        """Return the worst-case cusp height for a face, or None if not applicable."""
        surface = BRepAdaptor_Surface(face)

        if surface.GetType() == GeomAbs_Plane:
            return self._cusp_for_planar(face, surface)

        return self._cusp_for_curved(face, surface)

    def _cusp_for_planar(self, face: TopoDS_Face, surface: BRepAdaptor_Surface) -> Optional[float]:
        """Cusp height for a planar face, sampled at its centre."""
        u_mid, v_mid = get_face_uv_center(face)

        pnt = surface.Value(u_mid, v_mid)
        if is_on_bed(pnt, self.bed_height, self.print_orientation, self.bed_tolerance):
            return None

        normal = get_face_uv_normal(face, u_mid, v_mid)
        if normal is None:
            return None

        return self._cusp_from_normal(normal)

    def _cusp_for_curved(self, face: TopoDS_Face, surface: BRepAdaptor_Surface) -> Optional[float]:
        """Worst-case cusp height across a sampled grid on a curved face."""
        max_cusp = 0.0

        for u, v in yield_face_uv_grid(face, self.samples, margin=self.margin):
            pnt = surface.Value(u, v)
            if is_on_bed(pnt, self.bed_height, self.print_orientation, self.bed_tolerance):
                continue

            normal = get_face_uv_normal(face, u, v)
            if normal is None:
                continue

            cusp = self._cusp_from_normal(normal)
            if cusp is not None:
                max_cusp = max(max_cusp, cusp)

        return max_cusp if max_cusp > 0.0 else None

    def _cusp_from_normal(self, normal: gp_Dir) -> Optional[float]:
        """Compute cusp height from a surface normal, or None if excluded.

        Excludes downward-facing surfaces (handled by the overhang check)
        and nearly horizontal surfaces (flat top layers with no visible
        staircase effect).
        """
        theta_rad = self.print_orientation.Angle(normal)
        theta_deg = math.degrees(theta_rad)

        # Downward-facing surfaces are overhang territory.
        if theta_deg >= 90.0:
            return None

        # Nearly horizontal surfaces print as flat layers, no staircase.
        if theta_deg < self.horizontal_threshold_deg:
            return None

        return self.layer_height * math.cos(theta_rad)
