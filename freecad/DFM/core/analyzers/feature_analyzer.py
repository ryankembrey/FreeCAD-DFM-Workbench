# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""FEATURE_ANALYZER: the part's features, for the feature-aware rules.

Unlike the other analyzers it doesn't measure face by face: it recognises
the whole part once (bosses, ribs, holes, snap fits...), annotates the
features for the pull direction and side actions, and resolves the nominal
wall. It returns one ``PartFeatures`` that every feature rule reads.

Recognition is shape-level, so this never runs in the worker processes;
results are shared through ``FEATURE_CACHE`` (see core/utils/part_features.py).
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from OCP.TopoDS import TopoDS_Shape

from ...core.base.base_analyzer import BaseAnalyzer
from ...core.models import ProcessRequirement
from ...core.recognition import RecognitionOptions, annotate_directional, options_from_prefs
from ...core.registries import register_analyzer
from ...core.utils.geometry import EdgeIndex, FaceIndex
from ...core.utils.nominal_wall import SOURCE_NONE, resolve_nominal_wall
from ...core.utils.part_features import PartFeatures, empty_result, recognize_cached

#: Progress steps (iteration_count) and where each stage ends on them.
_STEPS = 100
_RECOGNISED = 80
_ANNOTATED = 95


@register_analyzer("FEATURE_ANALYZER")
class FeatureAnalyzer(BaseAnalyzer):
    parallel = False  # recognition is shape-level: never sent to the workers
    relative_cost = 20  # ~ms per progress step (100 steps), for the first time estimate
    #: Reads recognised features: the run passes it the model's history hints.
    uses_recognition = True

    @property
    def analysis_type(self) -> str:
        return "FEATURE_ANALYZER"

    @property
    def name(self) -> str:
        return "Feature Recognition"

    @property
    def requirements(self) -> set[ProcessRequirement]:
        return {
            ProcessRequirement.PULL_DIRECTION,
            ProcessRequirement.SIDE_ACTIONS,
            ProcessRequirement.NOMINAL_WALL_THICKNESS,
        }

    def resolve_prefs(self, prefs: dict) -> None:
        self.options: RecognitionOptions = options_from_prefs(prefs or {})

    def iteration_count(self, shape: TopoDS_Shape) -> int:
        return _STEPS

    def analyze(self, item: Any) -> None:
        """Not used: execute() recognises the whole shape at once."""
        return None

    def execute(
        self,
        shape: TopoDS_Shape,
        face_index: FaceIndex,
        edge_index: EdgeIndex,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> PartFeatures:
        self.resolve_prefs(kwargs.get("prefs", {}))

        def report(step: int) -> None:
            if progress_cb:
                progress_cb(max(0, min(_STEPS, int(step))))

        aborted = False
        try:
            result = recognize_cached(
                shape, self.options, hints=kwargs.get("recognition_hints") or (),
                progress=lambda _stage, fraction: report(_RECOGNISED * fraction),
                check_abort=check_abort,
            )
        except InterruptedError:
            aborted = True
            result = empty_result(shape, len(face_index), len(edge_index))
        report(_RECOGNISED)

        directional = None
        pull = kwargs.get(ProcessRequirement.PULL_DIRECTION.name)
        sides = kwargs.get(ProcessRequirement.SIDE_ACTIONS.name) or []
        if not aborted and (pull is not None or sides) and not (check_abort and check_abort()):
            try:
                directional = annotate_directional(result, shape, pull=pull, side_actions=sides,
                                                   options=self.options)
            except Exception:  # annotations are extras: the rules still run without them
                directional = None
        report(_ANNOTATED)

        if aborted:
            nominal, source = None, SOURCE_NONE
        else:
            nominal, source = resolve_nominal_wall(
                shape, face_index, result, kwargs.get(ProcessRequirement.NOMINAL_WALL_THICKNESS.name))
        report(_STEPS)

        return PartFeatures(
            result=result,
            directional=directional,
            nominal_wall=nominal,
            nominal_source=source,
            min_confidence=self.options.min_confidence,
            side_actions=tuple(_vector(d) for d in sides),
        )


def _vector(direction) -> tuple[float, float, float]:
    """(x, y, z) of a gp_Dir or a sequence."""
    if hasattr(direction, "X"):
        return (direction.X(), direction.Y(), direction.Z())
    return tuple(float(c) for c in direction)
