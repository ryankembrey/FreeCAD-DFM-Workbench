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


class Rulebook(Enum):
    """
    A list of all DFM rule types supported by the workbench.

    Definition Format:
        MEMBER_NAME = ("YAML_ID", "User Friendly Label")
    """

    MIN_DRAFT_ANGLE = ("MIN_DRAFT_ANGLE", "Minimum Draft Angle")
    MIN_WALL_THICKNESS = ("MIN_WALL_THICKNESS", "Minimum Wall Thickness")
    MAX_WALL_THICKNESS = ("MAX_WALL_THICKNESS", "Maximum Wall Thickness")
    NO_UNDERCUTS = ("NO_UNDERCUTS", "Undercut")

    def __init__(self, id_str, label):
        self._value_ = id_str
        self.label = label
