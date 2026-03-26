import math
from typing import Any, Optional

from dfm.models import CheckResult, Severity
from dfm.core.base_check import BaseCheck
from dfm.processes.process import RuleLimit, RuleFeedback
from dfm.rules import Rulebook
from dfm.registries import register_check


@register_check(Rulebook.SHARP_CORNERS)
class SharpCornerCheck(BaseCheck):
    @property
    def name(self) -> str:
        return "Sharp Corners Check"

    @property
    def required_analyzer_id(self) -> str:
        return "SHARP_CORNER_ANALYZER"

    def run_check(
        self,
        analysis_data_map: dict[Any, float],
        rule_config: RuleLimit,
        rule: Rulebook,
        feedback: Optional[RuleFeedback] = None,
    ) -> list[CheckResult]:
        tolerance = 1e-4
        results: list[CheckResult] = []

        target = self.safe_float(rule_config.target)
        limit = self.safe_float(rule_config.limit)
        unit = rule.unit

        if target is None and limit is None:
            return []

        fb = feedback or RuleFeedback()

        for edge, measured in analysis_data_map.items():
            if limit is not None and math.isclose(measured, limit, abs_tol=tolerance):
                continue
            if target is not None and math.isclose(measured, target, abs_tol=tolerance):
                continue

            severity: Optional[Severity] = None
            template = ""

            if limit is not None and measured > (limit - tolerance):
                severity = Severity.ERROR
                template = fb.error_msg
            elif target is not None and measured > (target - tolerance):
                severity = Severity.WARNING
                template = fb.warning_msg
            else:
                severity = Severity.SUCCESS

            effective_limit = limit if limit is not None else 0.0
            effective_target = target if target is not None else 0.0
            formatted_msg = self.format_feedback(
                template, measured, effective_target, effective_limit, unit
            )

            threshold = effective_limit if severity == Severity.ERROR else effective_target

            results.append(
                CheckResult(
                    rule_id=rule,
                    overview=f"{measured:.2f}{unit} > {threshold:.2f}{unit}",
                    message=formatted_msg,
                    severity=severity,
                    failing_geometry=[edge],
                    ignore=False,
                    value=round(float(measured), 4),
                    limit=float(effective_limit),
                    comparison=">",
                    unit=unit,
                )
            )

        return results
