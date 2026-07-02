# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import os
import tempfile
import Part

from OCP.TopoDS import TopoDS_Shape
from OCP.BRep import BRep_Builder
from OCP.BRepTools import BRepTools


def freecad_to_ocp(fc_shape: Part.Shape) -> TopoDS_Shape:
    """
    Converts a FreeCAD Part.Shape to an OCP TopoDS_Shape via a temporary file.
    """
    if fc_shape is None or fc_shape.isNull():
        raise ValueError("Cannot convert an empty or Null FreeCAD shape.")

    fd, temp_path = tempfile.mkstemp(suffix=".brep")

    try:
        fc_shape.exportBrep(temp_path)

        ocp_shape = TopoDS_Shape()
        builder = BRep_Builder()

        BRepTools.Read_s(ocp_shape, temp_path, builder)

        if ocp_shape.IsNull():
            raise ValueError("OCP failed to load the BRep file.")

        return ocp_shape

    finally:
        os.close(fd)
        if os.path.exists(temp_path):
            os.remove(temp_path)
