from typing import Any, Callable, Optional

from OCC.Core.TopoDS import TopoDS_Edge, TopoDS_Shape, topods
from OCC.Core.TopExp import topexp
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCC.Core.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

from dfm.core.base_analyzer import BaseAnalyzer
from dfm.models import ProcessRequirement
from dfm.registries.analyzers_registry import register_analyzer


# @register_analyzer("SHARP_CORNER_ANALYZER")
class SharpCornerAnalyzer(BaseAnalyzer):
    _MIN_SAMPLE_DISTANCE = 0.5  # mm

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

        # TODO: finish analyzer
