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
import FreeCAD

from OCC.Core.TopoDS import TopoDS_Face
from dfm.models import CheckResult, Severity
from dfm.rules import Rulebook

from dfm.core.base_check import BaseCheck
from dfm.registries import register_check


@register_check(Rulebook.MIN_WALL_THICKNESS, Rulebook.MAX_WALL_THICKNESS)
class ThicknessChecker(BaseCheck):
    @property
    def name(self) -> str:
        return "Thickness Checker"

    @property
    def required_analyzer_id(self) -> str:
        return "THICKNESS_ANALYZER"

    def run_check(
        self, analysis_data_map, parameters: dict[str, Any], check_type
    ) -> list[CheckResult]:
        results: list[CheckResult] = []

        if check_type == Rulebook.MIN_WALL_THICKNESS:
            for face, thicknesses in analysis_data_map.items():
                min_thickness = parameters.get("min_wall_thickness")
                minimum = min(thicknesses)
                if minimum < min_thickness:
                    message = f"Face {face.__hash__()} recorded thickness {minimum:.2f}mm and exceeded minimum thickness of {min_thickness:.2f}mm\n"

                    result = CheckResult(
                        rule_id=Rulebook.MIN_WALL_THICKNESS,
                        message=message,
                        severity=Severity.ERROR,
                        failing_geometry=[face],
                    )
                    results.append(result)
            return results

        elif check_type == Rulebook.MAX_WALL_THICKNESS:
            for face, thicknesses in analysis_data_map.items():
                max_thickness = parameters.get("max_wall_thickness")
                maximum = max(thicknesses)
                if maximum > max_thickness:
                    message = f"Recorded thickness of {maximum:.2f}mm and exceeded maximum thickness of {max_thickness:.2f}mm\n"

                    result = CheckResult(
                        rule_id=Rulebook.MAX_WALL_THICKNESS,
                        message=message,
                        severity=Severity.ERROR,
                        failing_geometry=[face],
                    )
                    results.append(result)
            return results

        elif check_type == "UNIFORM_THICKNESS":
            for face, thickness in analysis_data_map.items():
                uniform_thickness = parameters.get("uniform_thickness")
                # TODO:
            return results
        else:
            raise TypeError(f"Invalid THICKNESS check type {check_type}.")
