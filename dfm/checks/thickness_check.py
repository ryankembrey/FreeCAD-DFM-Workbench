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


@register_check(Rulebook.MIN_WALL_THICKNESS)
class MinThicknessCheck(BaseCheck):
    """
    Checks for walls that are too thin using Ray Casting.
    """

    @property
    def name(self) -> str:
        return "Min Thickness Check"

    @property
    def required_analyzer_id(self) -> str:
        return "RAY_THICKNESS_ANALYZER"

    def run_check(
        self,
        analysis_data_map: dict[TopoDS_Face, list[float]],
        parameters: dict[str, Any],
        check_type,
    ) -> list[CheckResult]:
        results: list[CheckResult] = []

        min_allowed = parameters.get("min_wall_thickness")
        if min_allowed is None:
            return []

        for face, thicknesses in analysis_data_map.items():
            if not thicknesses:
                continue

            # Filter out rays that hit nothing
            valid_thicknesses = [t for t in thicknesses if t != float("inf")]

            if not valid_thicknesses:
                continue

            measured_min = min(valid_thicknesses)

            if measured_min < (min_allowed):
                overview = f"{measured_min:.2f}° < {min_allowed:.2f}°"
                message = (
                    f"Minimum thickness violation. Measured: {measured_min:.2f}mm "
                    f"(Limit: {min_allowed:.2f}mm)"
                )

                result = CheckResult(
                    rule_id=Rulebook.MIN_WALL_THICKNESS,
                    overview=overview,
                    message=message,
                    severity=Severity.ERROR,
                    failing_geometry=[face],
                    ignore=False,
                )
                results.append(result)

        return results


@register_check(Rulebook.MAX_WALL_THICKNESS)
class MaxThicknessCheck(BaseCheck):
    """
    Checks for walls that are too thick using Rolling Ball / Sphere method.
    """

    @property
    def name(self) -> str:
        return "Max Thickness Check"

    @property
    def required_analyzer_id(self) -> str:
        return "SPHERE_THICKNESS_ANALYZER"

    def run_check(
        self, analysis_data_map, parameters: dict[str, Any], check_type
    ) -> list[CheckResult]:
        results: list[CheckResult] = []

        max_allowed = parameters.get("max_wall_thickness")
        if max_allowed is None:
            return []

        for face, diameters in analysis_data_map.items():
            if not diameters:
                continue

            measured_max = max(diameters)

            if measured_max > (max_allowed):
                overview = f"{measured_max:.2f}° > {max_allowed:.2f}°\n"
                message = (
                    f"Maximum thickness violation. Measured: {measured_max:.2f}mm "
                    f"(Limit: {max_allowed:.2f}mm). Risk of sink marks."
                )

                result = CheckResult(
                    rule_id=Rulebook.MAX_WALL_THICKNESS,
                    overview=overview,
                    message=message,
                    severity=Severity.WARNING,
                    failing_geometry=[face],
                    ignore=False,
                )
                results.append(result)

        return results
