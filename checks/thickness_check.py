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

from checks import BaseCheck


class ThicknessChecker(BaseCheck):
    handled_check_types = ["MIN_THICKNESS", "MAX_THICKNESS", "UNIFORM_THICKNESS"]
    dependencies = []

    @property
    def name(self) -> str:
        return "Thickness Checker"

    def run_check(self, analysis_data_map, parameters: dict[str, Any], check_type):
        faces: list[TopoDS_Face] = []

        if check_type == "MIN_THICKNESS":
            for face, thicknesses in analysis_data_map.items():
                min_thickness = parameters.get("min_thickness")
                minimum = min(thicknesses)
                if minimum < min_thickness:
                    FreeCAD.Console.PrintMessage(
                        f"Face {face.__hash__()} recorded thickness {minimum:.2f}mm and exceeded minimum thickness of {min_thickness:.2f}mm\n"
                    )
                    faces.append(face)
            return faces

        elif check_type == "MAX_THICKNESS":
            for face, thicknesses in analysis_data_map.items():
                max_thickness = parameters.get("max_thickness")
                maximum = max(thicknesses)
                if maximum > max_thickness:
                    FreeCAD.Console.PrintMessage(
                        f"Face {face.__hash__()} recorded thickness {maximum:.2f}mm and exceeded maximum thickness of {max_thickness:.2f}mm\n"
                    )
                    faces.append(face)
            return faces

        elif check_type == "UNIFORM_THICKNESS":
            for face, thickness in analysis_data_map.items():
                uniform_thickness = parameters.get("uniform_thickness")
                # TODO:
            return faces
        else:
            FreeCAD.Console.PrintDeveloperError(f"Invalid thickness check type {check_type}.")
            return faces
