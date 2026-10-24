# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Resolve a pull direction from a picked face or edge.

Shared by the task setup panel and the contour tools so both pick the same way:

- Cylinder / cone / torus faces give their axis.
- Other non-planar, non-spherical faces (e.g. fillets) give the axis of an arc or
  circle edge on the face, if there is one.
- Planar and spherical faces give the face normal at the middle of the face.
- Circle / arc edges give the circle's axis.
- Any other edge gives its tangent at the middle of the edge.
"""

import FreeCAD as App  # type: ignore
import Part  # type: ignore

# Red arrow used for pull direction in both setup and the contour tools.
PULL_DIRECTION_COLOR = (1.0, 0.15, 0.15)


def axis_from_face_edges(face) -> "App.Vector | None":
    """Extract an axis from arc/circle edges on a face (e.g. fillet faces)."""
    for edge in face.Edges:
        curve = edge.Curve
        if hasattr(curve, "Axis"):
            return App.Vector(curve.Axis).normalize()
    return None


def compute_direction(sub_obj) -> "tuple[App.Vector, App.Vector] | None":
    """Compute (point, direction) from a face or edge. Returns None on failure."""
    if isinstance(sub_obj, Part.Face):
        surface = sub_obj.Surface
        if isinstance(surface, (Part.Cylinder, Part.Cone, Part.Toroid)):
            dir_vec = surface.Axis.normalize()
            pnt = sub_obj.CenterOfMass
        elif not isinstance(surface, (Part.Plane, Part.Sphere)):
            axis = axis_from_face_edges(sub_obj)
            if axis is not None:
                dir_vec = axis
                pnt = sub_obj.CenterOfMass
            else:
                u0, u1, v0, v1 = sub_obj.ParameterRange
                u, v = (u0 + u1) * 0.5, (v0 + v1) * 0.5
                pnt = sub_obj.valueAt(u, v)
                dir_vec = sub_obj.normalAt(u, v).normalize()
        else:
            u0, u1, v0, v1 = sub_obj.ParameterRange
            u, v = (u0 + u1) * 0.5, (v0 + v1) * 0.5
            pnt = sub_obj.valueAt(u, v)
            dir_vec = sub_obj.normalAt(u, v).normalize()

    elif isinstance(sub_obj, Part.Edge):
        curve = sub_obj.Curve
        if hasattr(curve, "Axis"):
            dir_vec = App.Vector(curve.Axis).normalize()
            pnt = App.Vector(curve.Center) if hasattr(curve, "Center") else sub_obj.CenterOfMass
        else:
            p0, p1 = sub_obj.ParameterRange
            p_mid = (p0 + p1) * 0.5
            pnt = sub_obj.valueAt(p_mid)
            tangent = sub_obj.tangentAt(p_mid)
            if tangent.Length == 0:
                return None
            dir_vec = tangent.normalize()
    else:
        return None

    return (pnt, dir_vec)
