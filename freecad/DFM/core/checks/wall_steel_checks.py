# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Two moulding rules measured on walls rather than features:

* Wall Thickness Transition: a wall stepping abruptly to a different
  thickness (THICKNESS_TRANSITION_ANALYZER);
* Thin Steel: two part walls so close that the mould steel between them is
  a thin blade (MOLD_STEEL_ANALYZER).
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from ...core.base.feature_check import Measure, MeasuredCheck, mm
from ...core.models import CheckResult, Severity
from ...core.processes.process import RuleFeedback, RuleLimit
from ...core.registries import register_check
from ...core.rules import Rulebook

#: A change made over a ramp at least this many times the change in
#: thickness is gradual enough (DFMPro: a 3:1 taper).
GRADUAL_RUN = 3.0
#: Thin Steel ignores gaps wider than this many times its limit.
STEEL_REACH = 10.0


def _faces_label(*faces: int) -> str:
    return " ↔ ".join(f"Face{n}" for n in faces)


@register_check(Rulebook.MAX_WALL_THICKNESS_VARIATION)
class WallTransitionCheck(MeasuredCheck):
    """The change between two levels of a wall, as a % of the thicker one.
    A change made over a ramp at least 3 times as long as the change is fine."""

    message_fields = {"ramp": "The length to taper the change over (3 times the step)"}

    @property
    def name(self) -> str:
        return "Wall Transition Check"

    @property
    def progress_text(self) -> str:
        return "Checking wall thickness changes"

    @property
    def required_analyzer_id(self) -> str:
        return "THICKNESS_TRANSITION_ANALYZER"

    def run_check(
        self,
        analysis_data_map: Any,
        rule_config: RuleLimit,
        rule: Rulebook,
        feedback: Optional[RuleFeedback] = None,
        **kwargs: Any,
    ) -> list[CheckResult]:
        return self.grade_measures(self._measures(analysis_data_map or []), rule, rule_config, feedback)

    @staticmethod
    def _measures(transitions: Iterable) -> Iterable[Measure]:
        for t in transitions:
            step = t.thick - t.thin
            if t.run >= 0.98 * GRADUAL_RUN * step:  # 2%: the run is measured, not exact
                continue
            how = "sharply" if t.run < 0.05 else f"over {mm(t.run)}"
            yield Measure(
                t.change, _faces_label(t.thick_face, t.thin_face), t.faces,
                size=f"from {mm(t.thick)} to {mm(t.thin)} {how}",
                extra={"ramp": mm(GRADUAL_RUN * step)},
            )


@register_check(Rulebook.MIN_MOLD_STEEL_THICKNESS)
class MoldSteelCheck(MeasuredCheck):
    """The narrowest gap between facing part walls: the steel between them.
    A gap shallower (along the pull) than it is wide is only a warning."""

    message_fields = {"depth": "How deep the thin steel stands, along the pull"}

    @property
    def name(self) -> str:
        return "Thin Steel Check"

    @property
    def progress_text(self) -> str:
        return "Checking for thin mould steel"

    @property
    def required_analyzer_id(self) -> str:
        return "MOLD_STEEL_ANALYZER"

    def run_check(
        self,
        analysis_data_map: Any,
        rule_config: RuleLimit,
        rule: Rulebook,
        feedback: Optional[RuleFeedback] = None,
        **kwargs: Any,
    ) -> list[CheckResult]:
        thresholds = self.thresholds(rule, rule_config)
        if thresholds is None:
            return []
        reach = STEEL_REACH * max(t for t in thresholds if t is not None)
        return self.grade_measures(self._measures(analysis_data_map or {}, reach), rule, rule_config,
                                   feedback)

    @staticmethod
    def _measures(per_face: dict, reach: float) -> Iterable[Measure]:
        pairs: dict[tuple[int, int], tuple[float, float]] = {}
        for (_kind, face), value in per_face.items():
            if not value:
                continue
            gap, other, depth = value
            if gap > reach:
                continue
            key = (min(face, other), max(face, other))
            if key not in pairs or gap < pairs[key][0]:
                pairs[key] = (gap, depth)
        for (a, b), (gap, depth) in sorted(pairs.items()):
            shallow = depth < gap
            yield Measure(
                gap, _faces_label(a, b), (a, b),
                size=f"{mm(gap)} wide and {mm(depth)} deep",
                note="shallow" if shallow else "",
                cap=Severity.WARNING if shallow else None,
                extra={"depth": mm(depth)},
            )
