# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import math
from typing import Any, Callable, Optional

import FreeCAD as App  # type: ignore

from OCP.TopoDS import TopoDS_Shape, TopoDS_Face
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.gp import gp_Dir, gp_Vec
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
    yield_face_uv_grid,
)


@register_analyzer("OVERHANG_ANALYZER")
class OverhangAnalyzer(BaseAnalyzer):
    """
    Analyzes overhang of all faces in a shape relative to a print orientation.
    """

    @property
    def analysis_type(self) -> str:
        return "OVERHANG_ANALYZER"

    @property
    def requirements(self) -> set[ProcessRequirement]:
        return {ProcessRequirement.PRINT_ORIENTATION}

    @property
    def name(self) -> str:
        return "Overhang Analyzer"

    def resolve_prefs(self, prefs: dict) -> None:
        return super().resolve_prefs(prefs)

    def execute(
        self,
        shape: TopoDS_Shape,
        face_index: FaceIndex,
        edge_index: EdgeIndex,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> dict[tuple[str, int], float]:
        self.print_orientation = kwargs.get(
            ProcessRequirement.PRINT_ORIENTATION.name, gp_Dir(0, 0, 1)
        )
        self.samples = kwargs.get("samples", 15)
        self.face_index = face_index

        self.bed_height = calculate_bed_height(shape, self.print_orientation)

        results = {}
        for face in self.iter_faces(shape, progress_cb, check_abort):
            overhang = self._get_overhang_for_face(face)
            if overhang is not None and overhang > 0.0:
                results[self.face_index.key_of(face)] = overhang

        return results

    def _is_on_bed(self, surface: BRepAdaptor_Surface, u: float, v: float) -> bool:
        """Checks if a specific UV point on a face is touching the print bed."""
        pnt = surface.Value(u, v)

        height = gp_Vec(pnt.XYZ()).Dot(gp_Vec(self.print_orientation))

        # TODO: Investigate tolerance here
        return math.isclose(height, self.bed_height, abs_tol=0.5)

    def _get_overhang_for_face(self, face: TopoDS_Face) -> Optional[float]:
        """Calculates the maximum overhang angle in degrees for the given face."""
        surface = BRepAdaptor_Surface(face)

        if surface.GetType() == GeomAbs_Plane:
            u_mid, v_mid = get_face_uv_center(face)

            if self._is_on_bed(surface, u_mid, v_mid):
                return 0.0

            normal_dir = get_face_uv_normal(face, u_mid, v_mid)
            return self._calculate_overhang_angle(normal_dir) if normal_dir else None

        max_overhang = 0.0
        for u, v in yield_face_uv_grid(face, self.samples, margin=0.01):
            if self._is_on_bed(surface, u, v):
                continue

            normal_dir = get_face_uv_normal(face, u, v)
            if normal_dir:
                angle = self._calculate_overhang_angle(normal_dir)
                max_overhang = max(max_overhang, angle)

        return max_overhang

    def _calculate_overhang_angle(self, normal_dir: gp_Dir) -> float:
        angle_deg = math.degrees(self.print_orientation.Angle(normal_dir))
        if angle_deg <= 90.0:
            return 0.0
        return angle_deg
