# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""THICKNESS_TRANSITION_ANALYZER: where one wall steps to a thinner one.

Each plain wall face (not a boss, rib, hole, fillet...; see
core/utils/nominal_wall.py) gets its thickness from a few rays cast inward
through it. A face whose rays disagree (it spans a step), or that is
narrow for its thickness (a step's riser, a rim: the end of a wall), is
not a wall: it can only join two walls.

Two wall faces facing the same way are a step in one wall when they meet,
directly or through at most ``MAX_CONNECTORS`` faces between them (the
riser of the step, its fillets, a ramp), and are no further apart across
the wall than the thicker one is thick. For each such pair this reports
both thicknesses and the *run*: how far along the wall the change is made
(0 for a sharp step; a ramp's length). The check judges the change.

Recognition comes from the shared cache, so this costs no second
recognition when the feature rules run too.
"""

from __future__ import annotations

import collections
import statistics
from dataclasses import dataclass
from typing import Any, Callable, Optional

from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
from OCP.TopoDS import TopoDS, TopoDS_Shape

from ...core.base.base_analyzer import BaseAnalyzer
from ...core.recognition import options_from_prefs
from ...core.recognition.model import FeatureKind
from ...core.registries import register_analyzer
from ...core.utils.geometry import (
    EdgeIndex,
    FaceIndex,
    get_extent_along,
    get_face_uv_center,
    get_face_uv_normal,
)
from ...core.utils.nominal_wall import WALL_OWNERS, crossings, not_wall_faces
from ...core.utils.part_features import recognize_cached

#: Faces whose rays differ by more than this share of their median span a step.
UNIFORM_SPREAD = 0.1
#: Most faces between two walls for them to count as one step (riser, fillets).
MAX_CONNECTORS = 3
#: Walls this close to facing the same way can be two levels of one wall.
SAME_SIDE_DOT = 0.95
#: Smaller differences are measuring noise, not a step (mm).
MIN_STEP = 0.01
#: A wall face is at least this many times as wide as it is thick. Narrower
#: faces (a step's riser, a box's rim) are the ends of walls: their rays run
#: along the wall, not across it.
MIN_WIDTH_RATIO = 0.5

#: Faces of these may sit between two walls of a step.
_CONNECTOR_OWNERS = WALL_OWNERS | {FeatureKind.FILLET, FeatureKind.CHAMFER}


@dataclass(frozen=True)
class Transition:
    """Two levels of one wall that meet."""

    thick_face: int
    thin_face: int
    thick: float
    thin: float
    #: How far along the wall the change is made (mm): 0 for a sharp step.
    run: float
    #: The faces between them: the step's riser, fillets or ramp.
    between: tuple[int, ...] = ()

    @property
    def change(self) -> float:
        """The change as a % of the thicker wall."""
        return 100.0 * (self.thick - self.thin) / self.thick

    @property
    def faces(self) -> tuple[int, ...]:
        return tuple(sorted({self.thick_face, self.thin_face, *self.between}))


@register_analyzer("THICKNESS_TRANSITION_ANALYZER")
class ThicknessTransitionAnalyzer(BaseAnalyzer):
    parallel = False  # needs every face's thickness at once, and the recognition
    relative_cost = 3
    #: Reads recognised features: the run passes it the model's history hints.
    uses_recognition = True

    @property
    def analysis_type(self) -> str:
        return "THICKNESS_TRANSITION_ANALYZER"

    @property
    def name(self) -> str:
        return "Thickness Transition Analyzer"

    def resolve_prefs(self, prefs: dict) -> None:
        self.options = options_from_prefs(prefs or {})

    def analyze(self, item: Any) -> None:
        """Not used: execute() needs every face at once."""
        return None

    def execute(
        self,
        shape: TopoDS_Shape,
        face_index: FaceIndex,
        edge_index: EdgeIndex,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> list[Transition]:
        self.resolve_prefs(kwargs.get("prefs", {}))
        try:
            result = recognize_cached(shape, self.options, hints=kwargs.get("recognition_hints") or (),
                                      check_abort=check_abort)
        except InterruptedError:
            return []
        if result.face_count != len(face_index):
            return []
        excluded = not_wall_faces(result)

        intersector = IntCurvesFace_ShapeIntersector()
        intersector.Load(shape, 1e-3)
        thickness: dict[int, float] = {}
        normals: dict[int, Any] = {}
        for i in range(1, len(face_index) + 1):
            if check_abort and check_abort():
                return []
            if progress_cb:
                progress_cb(i)
            if i in excluded:
                continue
            face = face_index.face_at(i)
            found = crossings(face, intersector, face_index, excluded)
            if not found:
                continue
            middle = statistics.median(found)
            if middle <= 0 or (max(found) - min(found)) > UNIFORM_SPREAD * middle:
                continue
            if _width(face) < MIN_WIDTH_RATIO * middle:
                continue
            normal = get_face_uv_normal(face, *get_face_uv_center(face))
            if normal is None:
                continue
            thickness[i], normals[i] = middle, normal

        def connector(face: int) -> bool:
            if face in thickness:
                return False
            owner = result.owner_of(face)
            return owner is None or owner.kind in _CONNECTOR_OWNERS

        neighbours = _adjacency(shape, face_index)
        out: list[Transition] = []
        for a in sorted(thickness):
            for b, between in _reachable(a, neighbours, thickness, connector):
                if b <= a or normals[a].Dot(normals[b]) < SAME_SIDE_DOT:
                    continue
                ta, tb = thickness[a], thickness[b]
                if abs(ta - tb) < MIN_STEP:
                    continue
                apart = _apart(face_index.face_at(a), face_index.face_at(b), normals[a])
                if apart is None:
                    continue
                run, across = apart
                if across > 1.05 * max(ta, tb):
                    continue  # not two levels of one wall: they are too far apart across it
                thick, thin = (a, b) if ta > tb else (b, a)
                between = _between_levels(between, face_index, face_index.face_at(a),
                                          face_index.face_at(b), normals[a])
                out.append(Transition(thick, thin, max(ta, tb), min(ta, tb), run, between))
        return out


def _between_levels(faces, face_index: FaceIndex, a, b, normal) -> tuple[int, ...]:
    """Of the faces joining two levels, those lying between them (a step's
    riser, a pad's sides), not ones running past them (a plate's end face):
    the faces to show. All of them when none lies between."""
    tol = 1e-3
    levels = [*get_extent_along(a, normal), *get_extent_along(b, normal)]
    lo, hi = min(levels) - tol, max(levels) + tol
    inside = []
    for f in sorted(faces):
        f_lo, f_hi = get_extent_along(face_index.face_at(f), normal)
        if f_lo >= lo and f_hi <= hi:
            inside.append(f)
    return tuple(inside or sorted(faces))


def _width(face) -> float:
    """A face's typical width: twice its area over its perimeter (a strip's
    width, a disc's radius)."""
    area, length = GProp_GProps(), GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, area)
    BRepGProp.LinearProperties_s(face, length)
    return 2.0 * abs(area.Mass()) / length.Mass() if length.Mass() > 0 else 0.0


def _adjacency(shape: TopoDS_Shape, face_index: FaceIndex) -> dict[int, set[int]]:
    """Face number -> the faces it shares an edge with."""
    edge_faces = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, edge_faces)
    out: dict[int, set[int]] = collections.defaultdict(set)
    for k in range(1, edge_faces.Extent() + 1):
        faces = {face_index.index_of(TopoDS.Face_s(f)) for f in edge_faces.FindFromIndex(k)}
        for f in faces:
            out[f] |= faces - {f}
    return out


def _reachable(start: int, neighbours: dict, walls: dict, connector) -> list[tuple[int, set[int]]]:
    """Wall faces reached from *start* through at most MAX_CONNECTORS
    connector faces, each with the connectors on its shortest routes (all of
    them: a pad's every side joins it to the floor)."""
    found: dict[int, set[int]] = {}
    shortest: dict[int, int] = {}
    queue = collections.deque([(start, frozenset())])
    seen = {start}
    while queue:
        face, path = queue.popleft()
        for nxt in neighbours.get(face, ()):
            if nxt in walls:
                if nxt == start or len(path) > shortest.get(nxt, len(path)):
                    continue
                shortest[nxt] = len(path)
                found.setdefault(nxt, set()).update(path)
                continue
            if nxt in seen or len(path) >= MAX_CONNECTORS or not connector(nxt):
                continue
            seen.add(nxt)
            queue.append((nxt, path | {nxt}))
    return list(found.items())


def _apart(face_a, face_b, normal) -> Optional[tuple[float, float]]:
    """(along, across): how far apart two levels of a wall are, along the
    wall (the run of the change) and across it, between their nearest points."""
    tool = BRepExtrema_DistShapeShape(face_a, face_b)
    if not tool.IsDone() or tool.NbSolution() == 0:
        return None
    pa, pb = tool.PointOnShape1(1), tool.PointOnShape2(1)
    v = (pb.X() - pa.X(), pb.Y() - pa.Y(), pb.Z() - pa.Z())
    across = v[0] * normal.X() + v[1] * normal.Y() + v[2] * normal.Z()
    along = [v[0] - across * normal.X(), v[1] - across * normal.Y(), v[2] - across * normal.Z()]
    return sum(c * c for c in along) ** 0.5, abs(across)
