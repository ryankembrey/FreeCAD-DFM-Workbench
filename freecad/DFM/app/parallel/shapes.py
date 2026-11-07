# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Moving a shape between processes, and checking it arrived the same.

Used by both sides, so OCP only (no FreeCAD).
"""

from OCP.BRep import BRep_Builder
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools
from OCP.GProp import GProp_GProps
from OCP.TopoDS import TopoDS_Shape


def write_shape(shape, path):
    """Write *shape* to *path* (binary BREP when OCP has it, else text)."""
    try:
        from OCP.BinTools import BinTools

        BinTools.Write_s(shape, path)
        return "bin"
    except Exception:
        BRepTools.Write_s(shape, path)
        return "text"


def read_shape(path):
    shape = TopoDS_Shape()
    try:
        from OCP.BinTools import BinTools

        BinTools.Read_s(shape, path)
        if not shape.IsNull():
            return shape
    except Exception:
        pass
    shape = TopoDS_Shape()
    BRepTools.Read_s(shape, path, BRep_Builder())
    if shape.IsNull():
        raise RuntimeError(f"could not read the shape from {path}")
    return shape


def fingerprint(shape, face_index, edge_index):
    """(faces, edges, sum of index x area): equal when the faces arrived in the
    same order with the same geometry, which the results' face numbers rely on."""
    total = 0.0
    for i in range(1, len(face_index) + 1):
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face_index.face_at(i), props)
        total += i * abs(props.Mass())
    return (len(face_index), len(edge_index), total)
