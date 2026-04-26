# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from typing import Optional
from OCC.Core.TopoDS import TopoDS_Face

from ...dfm.models import CheckResult, Severity
from ...dfm.rules import Rulebook
from ...dfm.base.base_check import BaseCheck
from ...dfm.processes.process import RuleLimit, RuleFeedback
from ...dfm.registries import register_check


@register_check(Rulebook.NO_UNDERCUTS)
class UndercutCheck(BaseCheck):
    @property
    def name(self) -> str:
        return "Undercut Check"

    @property
    def required_analyzer_id(self) -> str:
        return "UNDERCUT_ANALYZER"

    def run_check(
        self,
        analysis_data_map: dict[TopoDS_Face, float],
        rule_config: RuleLimit,
        rule: Rulebook,
        feedback: Optional[RuleFeedback] = None,
    ) -> list[CheckResult]:
        results: list[CheckResult] = []
        fb = feedback or RuleFeedback()

        for face, undercut_ratio in analysis_data_map.items():
            if undercut_ratio > 0.00:
                severity = self.severity_from_rule_config(rule_config)
                template = fb.warning_msg if severity == Severity.WARNING else fb.error_msg
            else:
                continue

            percentage = undercut_ratio * 100

            msg = self.format_feedback(template, percentage, 0.0, 0.0, "%")

            results.append(
                CheckResult(
                    rule_id=rule,
                    overview=f"{percentage:.1f}% occlusion",
                    message=msg,
                    severity=severity,
                    failing_geometry=[face],
                    ignore=False,
                    value=float(percentage),
                    limit=0.0,
                    comparison=">",
                    unit="%",
                )
            )

        return results
