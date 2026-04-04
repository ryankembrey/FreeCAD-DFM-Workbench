#  ***************************************************************************
#  *   Copyright (c) 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>              *
#  *                                                                         *
#  *   This file is part of the FreeCAD CAx development system.              *
#  *                                                                         *
#  *   This library is free software; you can redistribute it and/or         *
#  *   modify it under the terms of the GNU Library General Public           *
#  *   License as published by the Free Software Foundation; either          *
#  *   version 2 of the License, or (at your option) any later version.      *
#  *                                                                         *
#  *   This library  is distributed in the hope that it will be useful,      *
#  *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
#  *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
#  *   GNU Library General Public License for more details.                  *
#  *                                                                         *
#  *   You should have received a copy of the GNU Library General Public     *
#  *   License along with this library; see the file COPYING.LIB. If not,    *
#  *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
#  *   Suite 330, Boston, MA  02111-1307, USA                                *
#  *                                                                         *
#  ***************************************************************************

from typing import Any, Optional

from dfm.models import CheckResult, Severity
from dfm.rules import Rulebook

from dfm.core.base_check import BaseCheck
from dfm.processes.process import RuleLimit, RuleFeedback
from dfm.registries import register_check


@register_check(Rulebook.MIN_WALL_THICKNESS)
class MinThicknessCheck(BaseCheck):
    @property
    def name(self) -> str:
        return "Min Thickness Check"

    @property
    def required_analyzer_id(self) -> str:
        return "RAY_THICKNESS_ANALYZER"

    def run_check(
        self,
        analysis_data_map: dict[Any, list[float]],
        rule_config: RuleLimit,
        rule: Rulebook,
        feedback: Optional[RuleFeedback] = None,
    ) -> list[CheckResult]:
        results: list[CheckResult] = []
        fb = feedback or RuleFeedback()

        target = self.safe_float(rule_config.target)
        limit = self.safe_float(rule_config.limit)
        unit = rule.unit

        if target is None and limit is None:
            return []

        for face, thicknesses in analysis_data_map.items():
            valid_thicknesses = [t for t in thicknesses if t != float("inf")]
            if not valid_thicknesses:
                continue

            measured = min(valid_thicknesses)

            severity: Optional[Severity] = None
            template = ""

            if limit is not None and measured < limit:
                severity = Severity.ERROR
                template = fb.error_msg
            elif target is not None and measured < target:
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
                    overview=f"{measured:.2f}{unit} < {threshold:.2f}{unit}",
                    message=formatted_msg,
                    severity=severity,
                    failing_geometry=[face],
                    ignore=False,
                    value=float(measured),
                    limit=effective_limit,
                    comparison="<",
                    unit="mm",
                )
            )

        return results


@register_check(Rulebook.MAX_WALL_THICKNESS)
class MaxThicknessCheck(BaseCheck):
    @property
    def name(self) -> str:
        return "Max Thickness Check"

    @property
    def required_analyzer_id(self) -> str:
        return "SPHERE_THICKNESS_ANALYZER"

    def run_check(
        self,
        analysis_data_map: dict[Any, list[float]],
        rule_config: RuleLimit,
        rule: Rulebook,
        feedback: Optional[RuleFeedback] = None,
    ) -> list[CheckResult]:
        results: list[CheckResult] = []
        fb = feedback or RuleFeedback()

        target = self.safe_float(rule_config.target)
        limit = self.safe_float(rule_config.limit)
        unit = rule.unit

        if target is None and limit is None:
            return []

        for face, diameters in analysis_data_map.items():
            if not diameters:
                continue

            measured = max(diameters)

            severity: Optional[Severity] = None
            template = ""

            if limit is not None and measured > limit:
                severity = Severity.ERROR
                template = fb.error_msg
            elif target is not None and measured > target:
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
                    value=float(measured),
                    limit=effective_limit,
                    comparison=">",
                    unit="mm",
                )
            )

        return results
