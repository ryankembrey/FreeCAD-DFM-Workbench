# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from enum import Enum, auto

from OCC.Core.TopoDS import TopoDS_Face
from OCC.Core.gp import gp_Dir, gp_Lin, gp_Vec
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector

from ...dfm.utils import get_face_uv_normal, get_face_uv_center, get_point_from_uv


class MoldSide(Enum):
    CORE = auto()
    CAVITY = auto()


def moldside_of_face(
    face: TopoDS_Face, intersector: IntCurvesFace_ShapeIntersector, pull_direction: gp_Dir
) -> MoldSide:
    """
    Classifies a face as CORE or CAVITY strictly via Topological Visibility.
    """
    u, v = get_face_uv_center(face)
    norm = get_face_uv_normal(face, u, v)
    if not norm:
        return MoldSide.CAVITY

    offset = 1e-4
    point = get_point_from_uv(face, norm, u, v, offset)

    # Up (+ pull dir)
    intersector.Perform(gp_Lin(point, pull_direction), 0.0, float("inf"))
    hits_up = False
    if intersector.IsDone():
        for i in range(1, intersector.NbPnt() + 1):
            if not intersector.Face(i).IsSame(face):
                hits_up = True
                break

    # Down (+ pull dir)
    intersector.Perform(gp_Lin(point, pull_direction.Reversed()), 0.0, float("inf"))
    hits_down = False
    if intersector.IsDone():
        for i in range(1, intersector.NbPnt() + 1):
            if not intersector.Face(i).IsSame(face):
                hits_down = True
                break

    if hits_down and not hits_up:
        return MoldSide.CORE

    elif hits_up and not hits_down:
        return MoldSide.CAVITY

    else:  # Zero hits and double hits
        pull_vec = gp_Vec(pull_direction)
        norm_vec = gp_Vec(norm)

        # Get horizontal component of vector
        dot_product = norm_vec.Dot(pull_vec)
        vertical_component = pull_vec.Multiplied(dot_product)
        horiz_vec = norm_vec.Subtracted(vertical_component)

        # No horizontal component (flat)
        if horiz_vec.Magnitude() < 1e-4:
            return MoldSide.CORE if norm.Dot(pull_direction) > 0.0 else MoldSide.CAVITY

        horiz_dir = gp_Dir(horiz_vec)

        # Cast ray horizontally outward
        intersector.Perform(gp_Lin(point, horiz_dir), 0.0, float("inf"))
        hits_horiz = False

        if intersector.IsDone():
            for i in range(1, intersector.NbPnt() + 1):
                if not intersector.Face(i).IsSame(face):
                    hits_horiz = True
                    break

        return MoldSide.CORE if hits_horiz else MoldSide.CAVITY
