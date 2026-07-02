# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, Iterator
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS_Shape, TopoDS_Face, TopoDS, TopoDS_Edge
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE
from freecad.DFM.core.utils.geometry import EdgeIndex, FaceIndex
from ...core.models import ProcessRequirement


class BaseAnalyzer(ABC):
    @property
    @abstractmethod
    def analysis_type(self) -> str:
        """
        The unique type of analysis this class performs.
        This is the primary way to identify and retrieve this analyzer.
        """
        pass

    @property
    def requirements(self) -> set[ProcessRequirement]:
        "Returns a list of process requirements."
        return set()

    @property
    @abstractmethod
    def name(self) -> str:
        """
        A user-friendly name for this analysis, e.g., for UI elements.
        """
        pass

    @abstractmethod
    def resolve_prefs(self, prefs: dict) -> None:
        """
        Resolve the preferences to `self` here from the kwargs in execute function.

        Call self.resolve_prefs(kwargs.get("prefs", {})) at the start of execute.
        """
        pass

    @abstractmethod
    def execute(
        self,
        shape: TopoDS_Shape,
        face_index: FaceIndex,
        edge_index: EdgeIndex,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> dict[tuple[str, int], Any]:
        pass

    def iter_faces(
        self,
        shape: TopoDS_Shape,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
    ) -> Iterator[TopoDS_Face]:
        """
        Yields each TopoDS_Face in the shape, handling abort checks.

        Usage:
            for face in self.iter_faces(shape, progress_cb, check_abort):
                results[face] = self._process_face(face)
        """
        explorer = TopExp_Explorer(shape, TopAbs_FACE)  # type: ignore
        count = 0

        while explorer.More():
            if check_abort and check_abort():
                return

            yield TopoDS.Face_s(explorer.Current())

            count += 1
            if progress_cb:
                progress_cb(count)

            explorer.Next()

    def iter_edges(
        self,
        shape: TopoDS_Shape,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
    ) -> Iterator[TopoDS_Edge]:
        """
        Yields each unique TopoDS_Edge in shape, handling abort checks and
        progress reporting automatically.

        Note: edges are deduplicated by hash — shared edges between two faces
        appear only once, which is correct for edge-level analysis.
        """

        edge_map = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_EDGE, edge_map)

        for i in range(1, edge_map.Extent() + 1):
            if check_abort and check_abort():
                return
            edge = TopoDS.Edge_s(edge_map.FindKey(i))
            yield edge
            if progress_cb:
                progress_cb(i)
