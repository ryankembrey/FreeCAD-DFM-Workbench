# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the DFM addon.

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
#  *   License along with this library; see the file COPYING.LIB. If not,   *
#  *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
#  *   Suite 330, Boston, MA  02111-1307, USA                                *
#  *                                                                         *
#  ***************************************************************************

import Part  # type: ignore


def get_face_index(target_obj, occ_face) -> int:
    """Finds the index of an OCC face in the target object's shape."""
    if not target_obj or not hasattr(target_obj, "Shape"):
        return -1

    for i, f in enumerate(target_obj.Shape.Faces):
        if Part.__toPythonOCC__(f).IsSame(occ_face):
            return i
    return -1


def get_face_name(target_obj, occ_face) -> str:
    """Returns the internal FreeCAD Face name (e.g., 'Face1')."""
    idx = get_face_index(target_obj, occ_face)
    return f"Face{idx + 1}" if idx != -1 else "Unknown Face"
