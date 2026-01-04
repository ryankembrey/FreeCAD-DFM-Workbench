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

from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face

from dfm.core.base_analyzer import BaseAnalyzer
from dfm.registries import register_analyzer


# Unregistered until implemented
# @register_analyzer("SPHERE_THICKNESS_ANALYZER")
class SphereThicknessAnalyzer(BaseAnalyzer):
    @property
    def analysis_type(self) -> str:
        return "SPHERE_THICKNESS_ANALYZER"

    @property
    def name(self) -> str:
        return "Sphere Thickness Analyzer"

    def execute(self, shape: TopoDS_Shape, **kwargs: Any) -> dict[TopoDS_Face, list[float]]:
        results: dict[TopoDS_Face, list[float]] = {}
        return results
