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

import math
from typing import Any, Optional

from dfm.models import CheckResult, Severity
from dfm.core.base_check import BaseCheck
from dfm.processes.process import RuleLimit, RuleFeedback
from dfm.rules import Rulebook
from dfm.registries import register_check


@register_check(Rulebook.MIN_DRAFT_ANGLE)
class DraftAngleCheck(BaseCheck):
    @property
    def name(self) -> str:
        return "Draft Checker"

    @property
    def required_analyzer_id(self) -> str:
        return "DRAFT_ANALYZER"

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

        for face, measured in analysis_data_map.items():
            if math.isclose(abs(measured), 90.0, abs_tol=tolerance):
                continue

            if limit is not None and math.isclose(measured, limit, abs_tol=tolerance):
                continue
            if target is not None and math.isclose(measured, target, abs_tol=tolerance):
                continue

            severity: Optional[Severity] = None
            if limit is not None and measured < (limit - tolerance):
                severity = Severity.ERROR
            elif target is not None and measured < (target - tolerance):
                severity = Severity.WARNING

            if severity:
                template = fb.error_msg if severity == Severity.ERROR else fb.warning_msg

                limit = limit if limit is not None else 0.0
                target = target if target is not None else 0.0

                formatted_msg = self.format_feedback(template, measured, target, limit, unit)

                results.append(
                    CheckResult(
                        rule_id=rule,
                        overview=f"{measured:.2f}{unit} < {limit:.2f}{unit}",
                        message=formatted_msg,
                        severity=severity,
                        failing_geometry=[face],
                        ignore=False,
                        value=round(float(measured), 4),
                        limit=float(limit),
                        comparison="<",
                        unit="°",
                    )
                )

        return results
