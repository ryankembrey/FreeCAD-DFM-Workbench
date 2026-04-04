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

from enum import Enum
from dataclasses import dataclass
from typing import Optional


@dataclass
class RuleType:
    label: str
    unit: Optional[str] = "mm"
    is_binary: bool = False  # If True, Target/Limit columns are irrelevant
    comparison: str = "min"  # "min" or "max"


class Rulebook(Enum):
    MIN_DRAFT_ANGLE = RuleType("Minimum Draft Angle", unit="°", comparison="min")
    MIN_WALL_THICKNESS = RuleType("Minimum Wall Thickness", unit="mm", comparison="min")
    MAX_WALL_THICKNESS = RuleType("Maximum Wall Thickness", unit="mm", comparison="max")
    NO_UNDERCUTS = RuleType("Undercut", unit=None, is_binary=True)
    SHARP_INTERNAL_CORNERS = RuleType(
        "Sharp Internal Corners", unit="", comparison="max", is_binary=True
    )
    SHARP_EXTERNAL_CORNERS = RuleType(
        "Sharp External Corners", unit="", comparison="max", is_binary=True
    )

    @property
    def id(self) -> str:
        return self.name

    @property
    def label(self) -> str:
        return self.value.label

    @property
    def unit(self) -> str:
        return self.value.unit or ""

    @property
    def is_binary(self) -> bool:
        return self.value.is_binary

    @property
    def comparison(self) -> str:
        return self.value.comparison
