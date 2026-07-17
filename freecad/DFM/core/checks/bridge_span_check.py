# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from typing import Any, Optional

from ...core.models import CheckResult, Severity
from ...core.base.base_check import BaseCheck
from ...core.processes.process import RuleLimit, RuleFeedback
from ...core.rules import Rulebook
from ...core.registries import register_check


@register_check(Rulebook.MAX_BRIDGE_SPAN)
class BridgeSpanCheck(BaseCheck):
    @property
    def name(self) -> str:
        return "Bridge Span Checker"

    @property
    def required_analyzer_id(self) -> str:
        return "BRIDGE_SPAN_ANALYZER"

    def run_check(
        self,
        analysis_data_map: dict[Any, float],
        rule_config: RuleLimit,
        rule: Rulebook,
        feedback: Optional[RuleFeedback] = None,
        **kwargs,
    ) -> list[CheckResult]:
        tolerance = 1e-4
        results: list[CheckResult] = []

        target = self.safe_float(rule_config.target)
        limit = self.safe_float(rule_config.limit)
        unit = rule.unit

        if target is None and limit is None:
            return []

        fb = feedback or RuleFeedback()

        for face, measured in analysis_data_map.items():
            severity: Optional[Severity] = None
            template = ""

            if limit is not None and measured > (limit + tolerance):
                severity = Severity.ERROR
                template = fb.error_msg
            elif target is not None and measured > (target + tolerance):
                severity = Severity.WARNING
                template = fb.warning_msg
            else:
                continue

            effective_limit = limit if limit is not None else 0.0
            effective_target = target if target is not None else 0.0
            threshold = effective_limit if severity == Severity.ERROR else effective_target

            formatted_msg = self.format_feedback(
                template, measured, effective_target, effective_limit, unit
            )

            results.append(
                CheckResult(
                    rule_id=rule,
                    overview=f"{measured:.2f}{unit} > {threshold:.2f}{unit}",
                    message=formatted_msg,
                    severity=severity,
                    failing_geometry=[face],
                    ignore=False,
                    value=round(float(measured), 4),
                    limit=float(effective_limit),
                    comparison=">",
                    unit=unit,
                )
            )

        return results
