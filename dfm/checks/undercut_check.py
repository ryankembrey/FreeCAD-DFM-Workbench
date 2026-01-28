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

from typing import Any

from OCC.Core.TopoDS import TopoDS_Face

from dfm.models import CheckResult, Severity
from dfm.rules import Rulebook
from dfm.core.base_check import BaseCheck
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
        self, analysis_data_map: dict[TopoDS_Face, float], parameters: dict[str, Any], check_type
    ) -> list[CheckResult]:
        results: list[CheckResult] = []

        for face, undercut_ratio in analysis_data_map.items():
            if undercut_ratio > 0.05:  # Allow 5% noise tolerance
                percentage = undercut_ratio * 100
                overview = f"{percentage:.1f}% occlusion"
                message = (
                    f"Undercut detected. {percentage:.1f}% of this face is "
                    "trapped (occluded from both Top and Bottom)."
                    f"<div style='margin-top: 8px; font-style: italic;'>"
                    f"<b>Suggestions:</b><br>"
                    f"1) Remove the overhang or align the feature with the pull direction.<br>"
                    f"2) Create a hole beneath/above the feature to allow the core/cavity to form the underside.<br>"
                    f"3) If this feature is critical, note that it will require expensive sliding mechanisms in the mold."
                    f"</div>"
                )

                results.append(
                    CheckResult(
                        rule_id=Rulebook.NO_UNDERCUTS,
                        overview=overview,
                        message=message,
                        severity=Severity.ERROR,
                        failing_geometry=[face],
                        ignore=False,
                    )
                )

        return results
