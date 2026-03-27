from typing import Any, Callable, Optional
import math

from OCC.Core.TopoDS import TopoDS_Edge, TopoDS_Shape, topods
from OCC.Core.TopExp import topexp
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCC.Core.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
from OCC.Core.BRep import BRep_Tool

from dfm.core.base_analyzer import BaseAnalyzer
from dfm.models import ProcessRequirement
from dfm.registries.analyzers_registry import register_analyzer
from dfm.utils.geometry import get_face_uv_normal


@register_analyzer("SHARP_CORNER_ANALYZER")
class SharpCornersAnalyzer(BaseAnalyzer):
    @property
    def name(self) -> str:
        return "Sharp Corner Analyzer"

    @property
    def analysis_type(self) -> str:
        return "SHARP_CORNER_ANALYZER"

    @property
    def requirements(self) -> set[ProcessRequirement]:
        return set()

    def execute(
        self,
        shape: TopoDS_Shape,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> dict[TopoDS_Edge, float]:
        edge_face_map = TopTools_IndexedDataMapOfShapeListOfShape()
        topexp.MapShapesAndAncestors(
            shape,
            TopAbs_EDGE.TopAbs_EDGE,  # type: ignore
            TopAbs_FACE.TopAbs_FACE,  # type: ignore
            edge_face_map,
        )

        results: dict[TopoDS_Edge, float] = {}

        for edge in self.iter_edges(shape, progress_cb, check_abort):
            result = self._analyze_edge(edge, edge_face_map)
            if result is not None:
                results[edge] = result

        return results

    def _analyze_edge(
        self,
        edge: TopoDS_Edge,
        edge_face_map: TopTools_IndexedDataMapOfShapeListOfShape,
    ) -> Optional[float]:
        if not edge_face_map.Contains(edge):
            return None

        adjacent_faces = edge_face_map.FindFromKey(edge)

        if adjacent_faces.Size() != 2:
            return None

        face1 = topods.Face(adjacent_faces.First())
        face2 = topods.Face(adjacent_faces.Last())

        if BRep_Tool.Degenerated(edge):
            return None

        try:
            first, last = BRep_Tool.Range(edge)
            mid_param = (first + last) / 2.0
        except Exception:
            return None

        n1 = self._get_normal_at_edge(face1, edge, mid_param)
        n2 = self._get_normal_at_edge(face2, edge, mid_param)

        if n1 is None or n2 is None:
            return None

        angle_rad = n1.Angle(n2)
        angle_deg = math.degrees(angle_rad)

        return angle_deg

    def _get_normal_at_edge(self, face, edge, param):
        try:
            curve_on_surf = BRep_Tool.CurveOnSurface(edge, face)
            if not curve_on_surf or curve_on_surf[0] is None:
                return None

            pcurve = curve_on_surf[0]
            uv = pcurve.Value(param)

            return get_face_uv_normal(face, uv.X(), uv.Y())

        except Exception:
            return None
