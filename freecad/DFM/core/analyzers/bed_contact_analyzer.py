# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Analyzer that measures the area of flat faces resting on the print bed.

Large bed-contact faces are prone to warping because differential cooling
across a wide footprint pulls the corners off the bed.
"""

from typing import Any, Callable, Optional

from OCP.TopoDS import TopoDS_Shape, TopoDS_Face
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.gp import gp_Dir

from ...core.base.base_analyzer import BaseAnalyzer
from ...core.models import ProcessRequirement
from ...core.registries import register_analyzer
from ...core.utils.geometry import (
    EdgeIndex,
    FaceIndex,
    calculate_bed_height,
    face_area,
    get_face_uv_center,
    is_flat,
    is_on_bed,
)


@register_analyzer("BED_CONTACT_ANALYZER")
class BedContactAnalyzer(BaseAnalyzer):
    """Finds flat faces on the print bed and returns their area (mm squared)."""

    @property
    def analysis_type(self) -> str:
        return "BED_CONTACT_ANALYZER"

    @property
    def requirements(self) -> set[ProcessRequirement]:
        return {ProcessRequirement.PRINT_ORIENTATION}

    @property
    def name(self) -> str:
        return "Bed Contact Analyzer"

    def resolve_prefs(self, prefs: dict) -> None:
        self.bed_tolerance = prefs.get("BedContactBedTolerance", 0.5)
        self.planarity_tolerance = prefs.get("BedContactPlanarityTolerance", 1e-6)

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
        self.bed_height = calculate_bed_height(shape, self.print_orientation)

        results: dict[tuple[str, int], float] = {}
        for face in self.iter_faces(shape, progress_cb, check_abort):
            area = self._bed_contact_area(face)
            if area is not None and area > 0.0:
                results[face_index.key_of(face)] = area

        return results

    def _bed_contact_area(self, face: TopoDS_Face) -> Optional[float]:
        """Return the face area if it is flat and sits on the bed, else None."""
        if not is_flat(face, tolerance=self.planarity_tolerance):
            return None

        # Sample the centre of the face to decide if it touches the bed.
        u_mid, v_mid = get_face_uv_center(face)
        surface = BRepAdaptor_Surface(face)
        pnt = surface.Value(u_mid, v_mid)

        if not is_on_bed(pnt, self.bed_height, self.print_orientation, self.bed_tolerance):
            return None

        return face_area(face)
