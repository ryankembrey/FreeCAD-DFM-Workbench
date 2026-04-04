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

from typing import Optional
from OCC.Core.TopoDS import TopoDS_Face

from dfm.models import CheckResult, Severity
from dfm.rules import Rulebook
from dfm.core.base_check import BaseCheck
from dfm.processes.process import RuleLimit, RuleFeedback
from dfm.registries import register_check


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
