# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""BED_EDGE_ANALYZER: edges of the faces on the print bed that meet a wall
with no chamfer or fillet.

FDM's first layer is squashed onto the bed and spreads a little sideways
("elephant's foot"): the part is wider at its base and holes there are
narrower. Where the wall leaves the bed through a chamfer or fillet, the
spread fills that relief instead (Ultimaker, Hubs).

An edge of a flat bed face is sharp when the face on its other side is a
wall: faces down by less than ``RELIEF_ANGLE`` below the horizontal (or
leans in over the bed). A chamfer or fillet there faces further down.

Each edge's value is (bed face number, wall face number).
"""

from __future__ import annotations

import math
from typing import Any, Optional

from OCP.BRep import BRep_Tool
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
from OCP.TopoDS import TopoDS, TopoDS_Edge, TopoDS_Face, TopoDS_Shape
from OCP.gp import gp_Dir

from ...core.base.base_analyzer import BaseAnalyzer
from ...core.models import ProcessRequirement
from ...core.registries import register_analyzer
from ...core.utils.geometry import (
    EdgeIndex,
    FaceIndex,
    calculate_bed_height,
    get_face_uv_center,
    get_face_uv_normal,
    is_flat,
    is_on_bed,
)

#: A face leaving the bed facing down by at least this much is a chamfer or
#: fillet (degrees below the horizontal); a wall faces down less (or up).
RELIEF_ANGLE = 20.0


@register_analyzer("BED_EDGE_ANALYZER")
class BedEdgeAnalyzer(BaseAnalyzer):
    item_kind = "Edge"
    relative_cost = 0.1  # ~ms per edge

    @property
    def analysis_type(self) -> str:
        return "BED_EDGE_ANALYZER"

    @property
    def name(self) -> str:
        return "Bed Edge Analyzer"

    @property
    def requirements(self) -> set[ProcessRequirement]:
        return {ProcessRequirement.PRINT_ORIENTATION}

    def resolve_prefs(self, prefs: dict) -> None:
        self.bed_tolerance = prefs.get("BedContactBedTolerance", 0.5)
        self.planarity_tolerance = prefs.get("BedContactPlanarityTolerance", 1e-6)

    def iteration_count(self, shape: TopoDS_Shape) -> int:
        return len(EdgeIndex(shape))

    def prepare(self, shape: TopoDS_Shape, face_index: FaceIndex, edge_index: EdgeIndex,
                **kwargs: Any) -> None:
        super().prepare(shape, face_index, edge_index, **kwargs)
        self.face_index = face_index
        self.up: gp_Dir = kwargs.get(ProcessRequirement.PRINT_ORIENTATION.name) or gp_Dir(0, 0, 1)
        self.bed_height = calculate_bed_height(shape, self.up)
        self.wall_dot = -math.sin(math.radians(RELIEF_ANGLE))
        self.edge_faces = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, self.edge_faces)
        self.bed_faces = {i for i in range(1, len(face_index) + 1) if self._on_bed(face_index.face_at(i))}

    def _on_bed(self, face: TopoDS_Face) -> bool:
        """A flat face facing straight down onto the bed."""
        if not is_flat(face, tolerance=self.planarity_tolerance):
            return False
        u, v = get_face_uv_center(face)
        normal = get_face_uv_normal(face, u, v)
        if normal is None or normal.Dot(self.up) > -0.999:
            return False
        point = BRep_Tool.Surface_s(face).Value(u, v)
        return is_on_bed(point, self.bed_height, self.up, self.bed_tolerance)

    def analyze(self, edge: TopoDS_Edge) -> Optional[tuple[int, int]]:
        if not self.bed_faces or BRep_Tool.Degenerated_s(edge) or not self.edge_faces.Contains(edge):
            return None
        faces = self.edge_faces.FindFromKey(edge)
        if faces.Size() != 2:
            return None
        a, b = TopoDS.Face_s(faces.First()), TopoDS.Face_s(faces.Last())
        na, nb = self.face_index.index_of(a), self.face_index.index_of(b)
        if (na in self.bed_faces) == (nb in self.bed_faces):
            return None
        bed, wall, wall_face = (na, nb, b) if na in self.bed_faces else (nb, na, a)
        normal = _normal_at_edge(wall_face, edge)
        if normal is None or normal.Dot(self.up) < self.wall_dot:
            return None  # a chamfer, fillet or overhang leaves the bed here
        return (bed, wall)


def _normal_at_edge(face: TopoDS_Face, edge: TopoDS_Edge) -> Optional[gp_Dir]:
    """*face*'s normal at the middle of *edge* (one of its edges)."""
    try:
        first, last = BRep_Tool.Range_s(edge)
        pcurve = BRep_Tool.CurveOnSurface_s(edge, face, 0.0, 0.0)
        if pcurve is None:
            return None
        uv = pcurve.Value((first + last) / 2.0)
        return get_face_uv_normal(face, uv.X(), uv.Y())
    except Exception:
        return None
