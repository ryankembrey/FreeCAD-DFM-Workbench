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

from dataclasses import dataclass, field
from typing import Any
from OCC.Core.TopoDS import TopoDS_Face
from .rules import Rulebook

from enum import Enum, auto


class Severity(Enum):
    SUCCESS = auto()
    INFO = auto()
    WARNING = auto()
    ERROR = auto()


@dataclass
class GeometryRef:
    """
    A serialisable reference to a sub-element of a FreeCAD shape.
    The GUI layer works exclusively with GeometryRef — never raw OCC objects.
    """

    type: str  # "Face", "Edge", "Vertex"
    index: int  # 0-based index into the shape's sub-element list
    label: str  # "Face1", "Edge3", 1-based

    def __str__(self) -> str:
        return self.label

    def to_dict(self) -> dict:
        return {"type": self.type, "index": self.index, "label": self.label}

    @classmethod
    def from_dict(cls, d: dict) -> "GeometryRef":
        return cls(type=d["type"], index=d["index"], label=d["label"])


@dataclass
class CheckResult:
    """
    A data class to hold the result of a single DFM check finding.
    """

    rule_id: Rulebook
    severity: Severity
    overview: str
    message: str
    ignore: bool
    value: float
    limit: float
    comparison: str = ""
    unit: str = ""

    failing_geometry: list[Any] = field(default_factory=list)

    refs: list[GeometryRef] = field(default_factory=list)

    @property
    def is_resolved(self) -> bool:
        return len(self.refs) > 0


class ProcessRequirement(Enum):
    NONE = "NONE"
    PULL_DIRECTION = "PULL_DIRECTION"
    NEUTRAL_PLANE = "NEUTRAL_PLANE"

    @classmethod
    def from_str(cls, label: str):
        try:
            return cls[label.upper()]
        except KeyError:
            return cls.NONE
