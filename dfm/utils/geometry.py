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

from typing import Generator

from OCC.Core.BRepTools import breptools
from OCC.Core.BRep import BRep_Tool
from OCC.Core.GeomLProp import GeomLProp_SLProps
from OCC.Core.gp import gp_Dir, gp_Pnt
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


def yield_face_uv_grid(
    face: TopoDS_Face, samples: int, margin: float = 0.00
) -> Generator[tuple[float, float], None, None]:
    """
    Generates (u, v) coordinates for a grid covering the face.

    Args:
        face: The face to sample.
        samples: Number of points along each axis.
        margin: Percentage (0.0 to 0.5) to crop from edges to avoid corner noise.
                Default 0.05 (5%) ensures rays don't graze adjacent faces.
    """
    u_min, u_max, v_min, v_max = breptools.UVBounds(face)

    # Apply margin (Default to none)
    u_len = u_max - u_min
    v_len = v_max - v_min

    s_u_min = u_min + (u_len * margin)
    s_u_max = u_max - (u_len * margin)
    s_v_min = v_min + (v_len * margin)
    s_v_max = v_max - (v_len * margin)

    # Handle single point (center)
    if samples <= 1:
        yield (s_u_min + s_u_max) / 2.0, (s_v_min + s_v_max) / 2.0
        return

    # Calculate step
    u_step = (s_u_max - s_u_min) / (samples - 1)
    v_step = (s_v_max - s_v_min) / (samples - 1)

    # Iterate
    for i in range(samples):
        u = s_u_min + i * u_step
        for j in range(samples):
            v = s_v_min + j * v_step
            yield u, v


def get_point_from_uv(
    face: TopoDS_Face, normal: gp_Dir, u: float, v: float, epsilon: float
) -> gp_Pnt:
    """
    Returns the geometric point in 3D space for a given UV coordinate on a face.

    Epsilon controls the distance the point is from the face in the normal direction.
    This can be useful to cast rays that do not intersect with the face of origin.
    """
    surface = BRep_Tool.Surface(face)
    p_surf = surface.Value(u, v)

    point = gp_Pnt(
        p_surf.X() + normal.X() * epsilon,
        p_surf.Y() + normal.Y() * epsilon,
        p_surf.Z() + normal.Z() * epsilon,
    )

    return point
