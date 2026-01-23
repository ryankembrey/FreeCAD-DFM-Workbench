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
import math

from dfm.models import CheckResult, Severity

from dfm.core.base_check import BaseCheck
from dfm.rules import Rulebook
from dfm.registries import register_check


@register_check(Rulebook.MIN_DRAFT_ANGLE)
class DraftAngleCheck(BaseCheck):
    """
    A single, flexible class to handle all draft angle related checks.
    Implements checks for minimum draft and maximum draft.
    """

    @property
    def name(self) -> str:
        return "Draft Checker"

    @property
    def required_analyzer_id(self) -> str:
        return "DRAFT_ANALYZER"

    def run_check(
        self,
        analysis_data_map,
        parameters: dict[str, Any],
        check_type,
    ) -> list[CheckResult]:
        tolerance = 1e-4  # 0.0001 degrees

        results: list[CheckResult] = []

        if check_type == Rulebook.MIN_DRAFT_ANGLE:
            min_allowed = parameters.get("min_draft_angle")
            if min_allowed is None:
                raise ValueError(f"'MIN_DRAFT_ANGLE' requires a 'min_draft_angle' parameter.")
            for face, measured_min in analysis_data_map.items():
                if measured_min < (min_allowed - tolerance) and abs(measured_min) != 90.0:
                    if measured_min < 0:
                        severity = Severity.ERROR
                        message = (
                            f"<b>Reverse Draft ({measured_min:.2f}°):</b> This face tapers inward relative to the "
                            f"mold opening direction. This is a critical error because the part is physically "
                            f"trapped; attempting to eject it will likely tear the plastic or damage the mold."
                            f"<div style='margin-top: 8px; font-style: italic; color: #aaaaaa;'>"
                            f"Suggestion: Increase draft angle or change the pull direction.</div>"
                        )
                    elif math.isclose(measured_min, 0.0, abs_tol=1e-3):
                        severity = Severity.ERROR
                        message = (
                            f"<b>Vertical Face (0.00°):</b> This surface has no taper. Because plastic shrinks "
                            f"as it cools, it will grip the mold tightly. Without an angle to create an immediate "
                            f"air gap, friction during ejection will cause 'drag marks' or scratches on the part."
                            f"<div style='margin-top: 8px; font-style: italic; color: #aaaaaa;'>"
                            f"Suggestion: Apply a positive draft angle to the face.</div>"
                        )
                    else:
                        severity = Severity.WARNING
                        message = (
                            f"<b>Insufficient Draft ({measured_min:.2f}°):</b> This angle is below the required "
                            f"{min_allowed:.2f}°. While tilted correctly, it is too shallow to guarantee a clean "
                            f"release. Increasing the angle will improve surface quality and reduce the risk of "
                            f"the part deforming during ejection."
                            f"<div style='margin-top: 8px; font-style: italic; color: #aaaaaa;'>"
                            f"Suggestion: Increase draft angle.</div>"
                        )
                    result = CheckResult(
                        rule_id=Rulebook.MIN_DRAFT_ANGLE,
                        message=message,
                        severity=severity,
                        failing_geometry=[face],
                        ignore=False,
                    )
                    results.append(result)
            return results
        else:
            raise TypeError(f"Invalid DRAFT ANGLE check type {check_type}.")
