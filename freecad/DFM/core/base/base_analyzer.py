# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The analyzer base class.

An analyzer measures one thing (thickness, draft, ...) on every face (or edge)
of a shape. It does so in two parts:

* ``prepare()`` once per shape: read the preferences and requirements and set
  up the heavy kernel tools (intersectors, classifiers);
* ``analyze()`` once per face or edge, returning its value or None.

Each face is measured on its own: nothing carries over from one face to the
next. A measurement made from one face that also tells something about another
(a thickness ray from face A exits through face B) is handed in with
``contribute()`` and added to that face's results at the end (``merge()``),
whatever order the faces ran in. So faces can be shared out in batches
(``batches()``) between worker processes (see app/parallel) and the results
are the same as in one process, on any number of cores.
"""

import math
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, Iterator
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS_Shape, TopoDS_Face, TopoDS, TopoDS_Edge
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE
from freecad.DFM.core.utils.geometry import EdgeIndex, FaceIndex
from ...core.models import ProcessRequirement


def batches(count: int, size: int) -> list[list[int]]:
    """1-based item indices 1..count, cut into consecutive batches of *size*."""
    size = max(1, int(size))
    return [list(range(start, min(start + size, count + 1))) for start in range(1, count + 1, size)]


class BaseAnalyzer(ABC):
    #: What analyze() takes, one at a time: "Face" or "Edge".
    item_kind = "Face"
    #: Heavy enough to be worth sharing out between worker processes.
    parallel = False
    #: Rough milliseconds per item on one desktop core. Orders the parallel
    #: queue (dearest analyzers first) and gives the progress panel its first
    #: guess at the time left; it learns the real speed as the analysis runs.
    relative_cost = 2

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
    def optional_requirements(self) -> set[ProcessRequirement]:
        """Those of ``requirements`` the analyzer can run without (it skips
        what needs them). A process family that has no use for one of them
        (``ProcessArchetype.waived_requirements``) doesn't ask for it."""
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

    def iteration_count(self, shape: TopoDS_Shape) -> int:
        """Return the number of items this analyzer will iterate over.

        The analysis runner uses this to allocate the correct progress-bar
        budget.  The default counts faces; override in analyzers that
        iterate edges (or anything else).
        """
        face_map = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_FACE, face_map)
        return face_map.Extent()

    # -- Per item ------------------------------------------------------------

    def prepare(
        self,
        shape: TopoDS_Shape,
        face_index: FaceIndex,
        edge_index: EdgeIndex,
        **kwargs: Any,
    ) -> None:
        """Set up for *shape*: preferences, requirements and kernel tools.

        Runs once per shape, in this process or in each worker process, before
        any analyze(). Override, calling this first.
        """
        self.resolve_prefs(kwargs.get("prefs", {}))

    def contribute(self, key: tuple[str, int], value: Any) -> None:
        """Add *value* to another item's results, e.g. a wall thickness found by a
        ray from one face, for the face the ray exits through (see merge())."""
        self._contributions.setdefault(key, []).append(value)

    def merge(self, results: dict, contributions: dict) -> dict:
        """Add contributed values to the results (list values), in item order,
        so the outcome doesn't depend on the order the items were measured in."""
        for key in sorted(contributions):
            results.setdefault(key, []).extend(sorted(contributions[key]))
        return results

    @abstractmethod
    def analyze(self, item: Any) -> Any:
        """The value for one face (or edge, see ``item_kind``), or None for no result."""
        pass

    # -- Batches ---------------------------------------------------------------

    def item_count(self, face_index: FaceIndex, edge_index: EdgeIndex) -> int:
        return len(edge_index) if self.item_kind == "Edge" else len(face_index)

    def item_at(self, index: int, face_index: FaceIndex, edge_index: EdgeIndex):
        return edge_index.edge_at(index) if self.item_kind == "Edge" else face_index.face_at(index)

    def batch_size(self, count: int) -> int:
        """Items per batch: small enough to share out evenly, big enough that
        sending them costs little."""
        return max(1, min(16, math.ceil(count / 96)))

    def batches(self, face_index: FaceIndex, edge_index: EdgeIndex) -> list[list[int]]:
        count = self.item_count(face_index, edge_index)
        return batches(count, self.batch_size(count))

    def run_batch(
        self,
        indices: list[int],
        face_index: FaceIndex,
        edge_index: EdgeIndex,
        progress_cb: Optional[Callable[[], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
    ) -> tuple[dict, dict]:
        """Analyze the items of one batch; prepare() must have run.

        Returns (results, contributions): see contribute() and merge().
        """
        self._contributions: dict[tuple[str, int], list] = {}
        results: dict[tuple[str, int], Any] = {}
        for index in indices:
            if check_abort and check_abort():
                break
            value = self.analyze(self.item_at(index, face_index, edge_index))
            if value is not None:
                results[(self.item_kind, index)] = value
            if progress_cb:
                progress_cb()
        return results, self._contributions

    def execute(
        self,
        shape: TopoDS_Shape,
        face_index: FaceIndex,
        edge_index: EdgeIndex,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> dict[tuple[str, int], Any]:
        """Analyze every face (or edge) in this process."""
        self._check_abort = check_abort
        self.prepare(shape, face_index, edge_index, **kwargs)
        done = 0

        def tick():
            nonlocal done
            done += 1
            if progress_cb:
                progress_cb(done)

        results: dict[tuple[str, int], Any] = {}
        contributions: dict[tuple[str, int], list] = {}
        for batch in self.batches(face_index, edge_index):
            if check_abort and check_abort():
                break
            found, given = self.run_batch(batch, face_index, edge_index, tick, check_abort)
            results.update(found)
            for key, values in given.items():
                contributions.setdefault(key, []).extend(values)
        return self.merge(results, contributions)

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
