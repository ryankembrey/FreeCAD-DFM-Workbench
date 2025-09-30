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
from typing import Any, Generator

from checks.base_check import BaseCheck
from OCC.Core.TopoDS import TopoDS_Face, TopoDS_Shape


class DraftAngleCheck(BaseCheck):
    """
    A single, flexible class to handle all draft angle related checks.
    Implements checks for minimum draft and maximum draft.
    """

    handled_check_types = ["MIN_DRAFT_ANGLE", "MAX_DRAFT_ANGLE"]
    dependencies = []

    @property
    def name(self) -> str:
        return "Draft Analyzer"

    def run_check(
        self,
        analysis_data_map,
        parameters: dict[str, Any],
        check_type,
    ):
        print("\nRunning Draft Angle Checker\n")
        tolerance = 1e-4  # 0.0001 degrees

        faces = []

        if check_type == "MIN_DRAFT_ANGLE":
            min_angle = parameters.get("min_angle")
            if min_angle is None:
                raise ValueError(f"'MIN_DRAFT_ANGLE' requires a 'min_angle' parameter.")
            for face, draft_result in analysis_data_map.items():
                if draft_result < (min_angle - tolerance) and abs(draft_result) != 90.0:
                    print(
                        f"Face ID: [{face.__hash__()}] | Angle is {draft_result:.2f}°, which is less than the required minimum of {min_angle:.2f}°."
                    )
                    faces.append(face)

        elif check_type == "MAX_DRAFT_ANGLE":
            max_angle = parameters.get("max_angle")
            if max_angle is None:
                raise ValueError(f"'MAX_DRAFT_ANGLE' requires a 'max_angle' parameter.")

            for face, draft_result in analysis_data_map.items():
                is_flat = math.isclose(draft_result, 90.0)

                if not is_flat and draft_result > (max_angle + tolerance):
                    print(
                        f"Face ID: [{face.__hash__()}] | Angle is {draft_result:.2f}°, which is greater than the allowed maximum of {max_angle:.2f}°."
                    )
                    faces.append(face)
        return faces
