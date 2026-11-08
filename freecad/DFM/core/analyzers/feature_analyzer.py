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
from ...core.recognition import DirectionalReport, RecognitionOptions, annotate_directional, options_from_prefs
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
        if not aborted and not (check_abort and check_abort()):
            try:
                directional = self.annotate(result, shape, **kwargs)
            except Exception:  # annotations are extras: the rules still run without them
                directional = None
        report(_ANNOTATED)

        if aborted or not self.measures_nominal_wall:
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
            side_actions=tuple(_vector(d) for d in kwargs.get(ProcessRequirement.SIDE_ACTIONS.name) or ()),
        )

    #: Resolve the nominal wall (moulding rules are ratios to it).
    measures_nominal_wall = True

    def annotate(self, result, shape: TopoDS_Shape, **kwargs: Any) -> Optional[DirectionalReport]:
        """Draft and undercut annotations for the run's pull and side actions."""
        pull = kwargs.get(ProcessRequirement.PULL_DIRECTION.name)
        sides = kwargs.get(ProcessRequirement.SIDE_ACTIONS.name) or []
        if pull is None and not sides:
            return None
        return annotate_directional(result, shape, pull=pull, side_actions=sides, options=self.options)


@register_analyzer("PRINT_FEATURE_ANALYZER")
class PrintFeatureAnalyzer(FeatureAnalyzer):
    """The part's features for the 3D printing rules: annotated for the print
    direction and nozzle (horizontal holes, slender pins, cups...), no pull
    direction and no nominal wall. Shares the recognition cache.

    FDM, resin and powder bed rules all read it; a powder bed has no print
    direction, so it runs without one."""

    measures_nominal_wall = False

    @property
    def analysis_type(self) -> str:
        return "PRINT_FEATURE_ANALYZER"

    @property
    def name(self) -> str:
        return "Feature Recognition (printing)"

    @property
    def requirements(self) -> set[ProcessRequirement]:
        # The nozzle is an FDM input: the FDM archetype asks for it.
        return {ProcessRequirement.PRINT_ORIENTATION}

    @property
    def optional_requirements(self) -> set[ProcessRequirement]:
        # Without a direction the features are just not annotated for it:
        # a powder bed's rules don't need one.
        return {ProcessRequirement.PRINT_ORIENTATION}

    def annotate(self, result, shape: TopoDS_Shape, **kwargs: Any) -> Optional[DirectionalReport]:
        direction = kwargs.get(ProcessRequirement.PRINT_ORIENTATION.name)
        if direction is None:
            return None
        try:
            nozzle = float(kwargs.get(ProcessRequirement.NOZZLE_DIAMETER.name) or 0.4)
        except (TypeError, ValueError):
            nozzle = 0.4
        return annotate_directional(result, shape, print_direction=direction, nozzle_diameter=nozzle,
                                    options=self.options)


def _vector(direction) -> tuple[float, float, float]:
    """(x, y, z) of a gp_Dir or a sequence."""
    if hasattr(direction, "X"):
        return (direction.X(), direction.Y(), direction.Z())
    return tuple(float(c) for c in direction)
