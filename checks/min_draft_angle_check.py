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

from typing import Dict, Any, Generator

from OCC.Core.TopoDS import TopoDS_Face

from checks import BaseCheck
from enums import Severity, CheckType
from data_types import CheckResult


class MinDraftAngleCheck(BaseCheck):
    @property
    def check_type(self) -> CheckType:
        return CheckType.MIN_DRAFT_ANGLE

    @property
    def name(self) -> str:
        return "Draft Angle Check"

    def run_check(
        self, analysis_data: Dict[TopoDS_Face, Any], parameters: float
    ) -> Generator[CheckResult, None, None]:
        min_angle = parameters

        if min_angle is None:
            raise ValueError("MinDraftAngleCheck requires a 'min_angle' parameter.")

        for i, (face, measured_angle) in enumerate(analysis_data.items()):
            if measured_angle < min_angle:
                yield CheckResult(
                    message=f"Offending face: #{i + 1}. Angle is {measured_angle:.2f}°, required min {min_angle}°.",
                    offending_geometry=[face],
                    severity=Severity.ERROR,
                    check_name=CheckType.MIN_DRAFT_ANGLE,
                )
