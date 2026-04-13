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

from typing import Generator, Optional, Callable

from OCC.Core.BRepTools import breptools
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepTopAdaptor import BRepTopAdaptor_FClass2d
from OCC.Core.GeomLProp import GeomLProp_SLProps
from OCC.Core.gp import gp_Dir, gp_Pnt, gp_Pnt2d
from OCC.Core.TopAbs import TopAbs_REVERSED, TopAbs_IN, TopAbs_ON
from OCC.Core.TopoDS import TopoDS_Face


def get_face_uv_center(face: TopoDS_Face) -> tuple[float, float]:
    """Returns the center of the UV parametric space for a TopoDS_Face."""
    u_min, u_max, v_min, v_max = breptools.UVBounds(face)
    u_mid: float = (u_max + u_min) / 2
    v_mid: float = (v_max + v_min) / 2

    return (u_mid, v_mid)


def get_face_uv_normal(face: TopoDS_Face, u: float, v: float) -> Optional[gp_Dir]:
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
    """
    classifier = BRepTopAdaptor_FClass2d(face, 1e-6)

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
            if is_point_on_face(u, v, face, classifier):
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


def get_face_uv_ratios(face: TopoDS_Face):
    """
    Calculates the ratio of parametric UV space to physical 3D space.
    Returns (u_ratio, v_ratio) where ratio * target_mm = uv_step.
    """
    u_min, u_max, v_min, v_max = breptools.UVBounds(face)
    u_range = abs(u_max - u_min)
    v_range = abs(v_max - v_min)

    bbox = Bnd_Box()
    brepbndlib.Add(face, bbox)
    x_min, y_min, z_min, x_max, y_max, z_max = bbox.Get()

    phys_width = abs(x_max - x_min)
    phys_height = abs(y_max - y_min)
    phys_depth = abs(z_max - z_min)

    dims = sorted([phys_width, phys_height, phys_depth], reverse=True)
    phys_u_estimate = dims[0] if dims[0] > 1e-6 else 1.0
    phys_v_estimate = dims[1] if dims[1] > 1e-6 else 1.0

    u_ratio = u_range / phys_u_estimate
    v_ratio = v_range / phys_v_estimate

    return u_ratio, v_ratio


def is_point_on_face(
    u: float, v: float, face: TopoDS_Face, classifier: BRepTopAdaptor_FClass2d | None = None
) -> bool:
    """Checks if a point by UV is on a face."""
    if not classifier:
        classifier = BRepTopAdaptor_FClass2d(face, 1e-6)

    state = classifier.Perform(gp_Pnt2d(u, v))

    if state == TopAbs_IN or state == TopAbs_ON:
        return True

    return False


def optimize_face_uv_search(
    face: TopoDS_Face,
    start_uv: tuple[float, float],
    start_val: float,
    eval_func: Callable[[float, float, float], Optional[float]],
    classifier: BRepTopAdaptor_FClass2d,
    max_iterations: int = 50,
    max_step_mm: float = 5.0,
) -> tuple[tuple[float, float], float, list[float]]:
    """
    A generic Hill-Climbing algorithm that searches a face's UV space to find the
    local maximum of a given scalar field (e.g. thickness, clearance).

    Args:
        face: The topological face to search.
        start_uv: The initial (u, v) parameter coordinates.
        start_val: The initial scalar value at start_uv.
        eval_func: A callable `fn(u, v, current_max)` that returns the scalar value.
        classifier: Boundary classifier for the face.
        max_iterations: Maximum steps before aborting.
        max_step_mm: Maximum physical step size in millimeters.

    Returns:
        Tuple containing (best_uv, max_val, list_of_improvements).
    """
    u_ratio, v_ratio = get_face_uv_ratios(face)
    u_min, u_max, v_min, v_max = breptools.UVBounds(face)

    face_width_mm = (u_max - u_min) / u_ratio
    step_size = max(0.1, min(face_width_mm * 0.02, max_step_mm))
    min_step = min(0.01, step_size * 0.001)

    plateau_patience = 3
    plateau_hits = 0
    gain_threshold = 0.0001

    current_best_uv, current_max_val = start_uv, start_val
    last_dir: Optional[tuple[int, int]] = None
    improvements: list[float] = []

    cardinals = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    diagonals = [(1, 1), (-1, 1), (1, -1), (-1, -1)]

    for _ in range(max_iterations):
        improved = False
        prev_val = current_max_val

        du = step_size * u_ratio
        dv = step_size * v_ratio

        def try_dir(d_u, d_v):
            nonlocal current_max_val, current_best_uv, improved, last_dir
            u_test = max(u_min, min(u_max, current_best_uv[0] + d_u * du))
            v_test = max(v_min, min(v_max, current_best_uv[1] + d_v * dv))

            if not is_point_on_face(u_test, v_test, face, classifier):
                return False

            val = eval_func(u_test, v_test, current_max_val)
            if val is not None and val > current_max_val and val != float("inf"):
                current_max_val = val
                current_best_uv = (u_test, v_test)
                improved = True
                last_dir = (d_u, d_v)
                improvements.append(val)
                return True
            return False

        # Try momentum first
        if last_dir and try_dir(*last_dir):
            pass

        # Try cardinals
        if not improved:
            for d_u_m, d_v_m in cardinals:
                if try_dir(d_u_m, d_v_m):
                    break

        # Try diagonals
        if not improved:
            for d_u_m, d_v_m in diagonals:
                if try_dir(d_u_m, d_v_m):
                    break

        if improved:
            relative_gain = (current_max_val - prev_val) / max(prev_val, 1e-6)
            if relative_gain < gain_threshold:
                plateau_hits += 1
            else:
                plateau_hits = 0

            if plateau_hits >= plateau_patience:
                break
        else:
            last_dir = None
            step_size /= 2.0
            if step_size < min_step:
                break

    return current_best_uv, current_max_val, improvements
