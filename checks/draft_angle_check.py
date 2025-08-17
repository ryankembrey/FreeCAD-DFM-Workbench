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
from typing import Dict, Any, Generator, List

# --- Correct, robust imports ---
from checks.base_check import BaseCheck
from enums import AnalysisType, Severity, CheckType
from data_types import CheckResult
from registry import dfm_registry
from OCC.Core.TopoDS import TopoDS_Face


class DraftAngleCheck(BaseCheck):
    """
    A single, flexible class to handle all draft angle related checks.
    Implements checks for minimum draft and maximum draft.
    """

    # This is the contract that the registry uses.
    handled_check_types: List[CheckType] = [
        CheckType.MIN_DRAFT_ANGLE,
        CheckType.MAX_DRAFT_ANGLE,
    ]

    # This is the contract that the AnalysisRunner uses.
    dependencies: List[AnalysisType] = [AnalysisType.DRAFT_ANGLE]

    @property
    def name(self) -> str:
        return "Draft Analyzer"

    def run_check(
        self,
        analysis_data_map: Dict[AnalysisType, Dict],
        parameters: Dict[str, Any],
        check_type: CheckType,
    ) -> Generator[CheckResult, None, None]:
        # --- STEP 1: Unpack the required data from the map ---
        # This check knows it needs the DRAFT data.
        draft_analysis_data: Dict[TopoDS_Face, float] = analysis_data_map[AnalysisType.DRAFT_ANGLE]

        # --- STEP 2: MIN_DRAFT_ANGLE Logic ---
        if check_type == CheckType.MIN_DRAFT_ANGLE:
            min_angle = parameters.get("min_angle")
            if min_angle is None:
                raise ValueError(f"'{check_type.name}' requires a 'min_angle' parameter.")

            for face, draft_result in draft_analysis_data.items():
                if draft_result < min_angle and abs(draft_result) != 90.0:
                    yield CheckResult(
                        message=f"Angle is {draft_result:.2f}°, which is less than the required minimum of {min_angle}°.",
                        offending_geometry=[face],
                        severity=Severity.ERROR,
                        check_name=check_type,
                    )

        # --- STEP 3: MAX_DRAFT_ANGLE Logic ---
        elif check_type == CheckType.MAX_DRAFT_ANGLE:
            max_angle = parameters.get("max_angle")
            if max_angle is None:
                raise ValueError(f"'{check_type.name}' requires a 'max_angle' parameter.")

            for face, draft_result in draft_analysis_data.items():
                # Don't flag flat top/bottom faces as having "too much" draft.
                is_flat = math.isclose(draft_result, 90.0)

                if not is_flat and draft_result > max_angle:
                    yield CheckResult(
                        message=f"Angle is {draft_result:.2f}°, which is greater than the allowed maximum of {max_angle}°.",
                        offending_geometry=[face],
                        severity=Severity.WARNING,  # Max draft is usually a warning
                        check_name=check_type,
                    )


dfm_registry.register_check(DraftAngleCheck())
