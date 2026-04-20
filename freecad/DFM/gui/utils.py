# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

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
