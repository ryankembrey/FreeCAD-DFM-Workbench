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

# This file defines geometry functions that are often reused between analyzers

from OCC.Core.BRepTools import breptools
from OCC.Core.BRep import BRep_Tool
from OCC.Core.GeomLProp import GeomLProp_SLProps
from OCC.Core.gp import gp_Dir
from OCC.Core.TopAbs import TopAbs_REVERSED
from OCC.Core.TopoDS import TopoDS_Face


def get_face_uv_center(face: TopoDS_Face) -> tuple[(float, float)]:
    """Returns the center of the UV parametric space for a TopoDS_Face."""
    u_min, u_max, v_min, v_max = breptools.UVBounds(face)
    u_mid: float = (u_max + u_min) / 2
    v_mid: float = (v_max + v_min) / 2

    return (u_mid, v_mid)


def get_face_uv_normal(face: TopoDS_Face, u: float, v: float) -> gp_Dir | None:
    """Returns the normal of a TopoDS_Face at UV"""

    surface = BRep_Tool.Surface(face)
    props = GeomLProp_SLProps(surface, u, v, 1, 1e-6)

    if props.IsNormalDefined():
        pnt = props.Value()
        norm = props.Normal()

        if not face.Location().IsIdentity():
            pnt.Transform(face.Location().Transformation())
            norm.Transform(face.Location().Transformation())

        if face.Orientation() == TopAbs_REVERSED:
            norm.Reverse()

        return norm
