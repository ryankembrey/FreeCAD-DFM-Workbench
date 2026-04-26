# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from typing import Optional

from OCC.Core.TopoDS import TopoDS_Edge

from ...dfm.models import CheckResult, Severity
from ...dfm.base.base_check import BaseCheck
from ...dfm.processes.process import RuleLimit, RuleFeedback
from ...dfm.rules import Rulebook
from ...dfm.registries import register_check


@register_check(Rulebook.SHARP_INTERNAL_CORNERS)
class SharpInternalCornerCheck(BaseCheck):
    @property
    def name(self) -> str:
        return "Sharp Internal Corners Check"

    @property
    def required_analyzer_id(self) -> str:
        return "SHARP_CORNER_ANALYZER"

    def run_check(
        self,
        analysis_data_map: dict[TopoDS_Edge, tuple[float, bool]],
        rule_config: RuleLimit,
        rule: Rulebook,
        feedback: Optional[RuleFeedback] = None,
    ) -> list[CheckResult]:
        tolerance = 1e-4
        results: list[CheckResult] = []

        unit = rule.unit
        fb = feedback or RuleFeedback()
        threshold = 1  # degree

        for edge, (angle_deg, is_concave) in analysis_data_map.items():
            if not is_concave or angle_deg < threshold:
                continue

            measured = angle_deg
            severity = self.severity_from_rule_config(rule_config)
            template = fb.warning_msg if severity == Severity.WARNING else fb.error_msg

            formatted_msg = self.format_feedback(template, measured, 0.0, 0.0, unit)

            results.append(
                CheckResult(
                    rule_id=rule,
                    overview=f"{measured:.2f}{unit}",
                    message=formatted_msg,
                    severity=severity,
                    failing_geometry=[edge],
                    ignore=False,
                    value=round(float(measured), 4),
                    limit=float(),
                    comparison="<",
                    unit=unit,
                )
            )

        return results
