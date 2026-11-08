# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""FLAT_FACE_ANALYZER: the area of each flat face.

Large flat areas are a printing risk two ways. In a powder bed a big flat
panel cools unevenly and warps. In a resin printer every layer that runs
across a face lying flat in the layers is one big cross-section, and the
peel off the tank pulls hard on it (and on the part).

With a print orientation (resin) only faces within ``LAYER_ANGLE`` of the
layers count; without one (a powder bed) every flat face does.

Each face's value is its area (mm²).
"""

from __future__ import annotations

import math
from typing import Any, Optional

from OCP.TopoDS import TopoDS_Face, TopoDS_Shape
from OCP.gp import gp_Dir

from ...core.base.base_analyzer import BaseAnalyzer
from ...core.models import ProcessRequirement
from ...core.registries import register_analyzer
from ...core.utils.geometry import EdgeIndex, FaceIndex, face_area, get_face_uv_center, get_face_uv_normal, is_flat

#: Faces within this angle of the layers lie flat in them (degrees).
LAYER_ANGLE = 10.0


@register_analyzer("FLAT_FACE_ANALYZER")
class FlatFaceAnalyzer(BaseAnalyzer):
    relative_cost = 0.2  # ~ms per face

    @property
    def analysis_type(self) -> str:
        return "FLAT_FACE_ANALYZER"

    @property
    def name(self) -> str:
        return "Flat Face Analyzer"

    @property
    def requirements(self) -> set[ProcessRequirement]:
        return {ProcessRequirement.PRINT_ORIENTATION}

    @property
    def optional_requirements(self) -> set[ProcessRequirement]:
        return {ProcessRequirement.PRINT_ORIENTATION}

    def resolve_prefs(self, prefs: dict) -> None:
        self.planarity_tolerance = prefs.get("BedContactPlanarityTolerance", 1e-6)

    def prepare(self, shape: TopoDS_Shape, face_index: FaceIndex, edge_index: EdgeIndex,
                **kwargs: Any) -> None:
        super().prepare(shape, face_index, edge_index, **kwargs)
        self.direction: Optional[gp_Dir] = kwargs.get(ProcessRequirement.PRINT_ORIENTATION.name)
        self.min_dot = math.cos(math.radians(LAYER_ANGLE))

    def analyze(self, face: TopoDS_Face) -> Optional[float]:
        if not is_flat(face, tolerance=self.planarity_tolerance):
            return None
        if self.direction is not None:
            normal = get_face_uv_normal(face, *get_face_uv_center(face))
            if normal is None or abs(normal.Dot(self.direction)) < self.min_dot:
                return None
        area = face_area(face)
        return area if area > 0.0 else None
