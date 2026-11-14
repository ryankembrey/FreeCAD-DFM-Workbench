# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Zebra stripes: the mesh and the stripe math (no Coin, no Qt).

Each vertex gets one number, its stripe coordinate *t*. The overlay draws a
two-color texture that repeats every 1.0 of *t*, so a vertex with t = 3.25
sits a quarter of the way into the fourth stripe pair, and stripe edges stay
sharp inside a triangle.

Two ways to work *t* out from a vertex's surface normal n:

* Fixed to the part (isophotes): the angle between n and a direction.
  The same angle Draft Analysis measures (draft = 90 deg minus it), so the
  stripes stay put when the view turns.
* Follow the view (reflection): the view ray reflected off the surface,
  r = d - 2 (d . n) n, and its angle from the camera's right axis (vertical
  stripes) or up axis (horizontal ones). As the view turns, the stripes
  slide over the part, like the reflection of a row of strip lights.

Either angle (0 to 180 deg) is scaled so ``count`` stripe pairs fit in it,
then moved on by three quarters of a pair (``PHASE``). Without that, a flat
face at a round angle (a top face at 0 deg, a wall at 90 deg) would sit
exactly on a stripe's edge, and texture filtering would paint it gray. With
it, such a face sits in the middle of the second (light) color, so flat faces
stay light and the stripes show where the surface curves.

The mesh keeps every face's vertices apart, each with the exact surface
normal of its own face. Where two faces meet at a crease or a tangent-only
join, the vertices on the shared edge carry two different normals, and that
is what makes the stripes jump or bend there.
"""

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

#: Tessellation per quality: (linear deflection as a share of the part's
#: diagonal, angular deflection in degrees). Stripes follow the normals, so
#: the angle matters most: finer angles give smoother stripes on curves.
QUALITY = {
    "Normal": (2.0e-3, 12.0),
    "Fine": (1.0e-3, 6.0),
    "Very fine": (5.0e-4, 3.0),
}
DEFAULT_QUALITY = "Fine"

#: Added to every stripe coordinate: three quarters of a stripe pair (see above).
PHASE = 0.75


@dataclass
class ZebraMesh:
    """Triangles to stripe, every face's vertices kept apart."""

    points: np.ndarray  # (n, 3) float64
    normals: np.ndarray  # (n, 3) float64, unit, pointing out of the part
    triangles: np.ndarray  # (m, 3) int32, counterclockwise seen from outside
    # Each edge of the faces, as a polyline: (points (k, 3), normals (k, 3)),
    # the normals averaged over the faces that share the edge.
    edges: list = field(default_factory=list)
    diagonal: float = 0.0

    @property
    def triangle_count(self) -> int:
        return int(len(self.triangles))


def tessellate(shape, quality: str = DEFAULT_QUALITY, faces: Optional[Sequence[int]] = None) -> ZebraMesh:
    """Mesh an OCP ``TopoDS_Shape`` for zebra stripes.

    *faces*: 1-based face numbers (Face3 is 3, in FreeCAD's order) to keep,
    or None for all. The shape's own triangulation is replaced, so pass a
    copy when the shape belongs to someone else.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepLib import BRepLib_ToolTriangulatedShape
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.Bnd import Bnd_Box
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape, TopTools_IndexedMapOfShape

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    diagonal = float(np.linalg.norm([xmax - xmin, ymax - ymin, zmax - zmin])) or 1.0
    linear, angular = QUALITY.get(quality, QUALITY[DEFAULT_QUALITY])
    BRepTools.Clean_s(shape)
    BRepMesh_IncrementalMesh(shape, linear * diagonal, False, np.radians(angular), True)

    face_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, face_map)
    wanted = range(1, face_map.Extent() + 1) if faces is None else [i for i in faces if 1 <= i <= face_map.Extent()]

    points, normals, triangles = [], [], []
    # Per face: (face, triangulation, location, first vertex number, reversed).
    meshed = {}
    base = 0
    for i in wanted:
        face = TopoDS.Face_s(face_map.FindKey(i))
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None or tri.NbTriangles() == 0:
            continue
        if not tri.HasNormals():
            BRepLib_ToolTriangulatedShape.ComputeNormals_s(face, tri)
        trsf = loc.Transformation()
        moved = not loc.IsIdentity()
        reversed_ = face.Orientation() == TopAbs_REVERSED
        sign = -1.0 if reversed_ else 1.0
        n_nodes = tri.NbNodes()
        for k in range(1, n_nodes + 1):
            p = tri.Node(k)
            d = tri.Normal(k)
            if moved:
                p = p.Transformed(trsf)
                d = d.Transformed(trsf)
            points.append(p.Coord())
            nx, ny, nz = d.Coord()
            normals.append((sign * nx, sign * ny, sign * nz))
        for k in range(1, tri.NbTriangles() + 1):
            a, b, c = tri.Triangle(k).Get()
            if reversed_:
                b, c = c, b
            triangles.append((base + a - 1, base + b - 1, base + c - 1))
        meshed[i] = (face, tri, loc, base)
        base += n_nodes

    if not triangles:
        raise RuntimeError("Nothing to show: the shape has no faces that could be meshed.")

    mesh = ZebraMesh(
        points=np.asarray(points, dtype=np.float64),
        normals=_unit(np.asarray(normals, dtype=np.float64)),
        triangles=np.asarray(triangles, dtype=np.int32),
        diagonal=diagonal,
    )

    # Edges of the kept faces, from the same nodes as the triangles, so they
    # sit exactly on the faces' boundaries.
    edge_faces = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, edge_faces)
    for e in range(1, edge_faces.Extent() + 1):
        edge = TopoDS.Edge_s(edge_faces.FindKey(e))
        if BRep_Tool.Degenerated_s(edge):
            continue
        polylines = []
        for f in edge_faces.FindFromIndex(e):
            fi = face_map.FindIndex(f)
            if fi not in meshed:
                continue
            _face, tri, loc, first = meshed[fi]
            poly = BRep_Tool.PolygonOnTriangulation_s(edge, tri, loc)
            if poly is None:
                continue
            polylines.append([first + poly.Node(j) - 1 for j in range(1, poly.NbNodes() + 1)])
        if not polylines:
            continue
        idx = np.asarray(polylines[0], dtype=np.int64)
        normal = mesh.normals[idx].copy()
        for other in polylines[1:]:
            other = np.asarray(other, dtype=np.int64)
            if len(other) != len(idx):
                continue
            # The same edge seen from the next face, possibly walked the other way.
            if np.linalg.norm(mesh.points[other[0]] - mesh.points[idx[0]]) > np.linalg.norm(
                    mesh.points[other[-1]] - mesh.points[idx[0]]):
                other = other[::-1]
            normal += mesh.normals[other]
        mesh.edges.append((mesh.points[idx].copy(), _unit(normal)))
    return mesh


def _unit(v: np.ndarray) -> np.ndarray:
    length = np.linalg.norm(v, axis=-1, keepdims=True)
    length[length < 1e-12] = 1.0
    return v / length


def _angle_deg(cos: np.ndarray) -> np.ndarray:
    return np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))


def fixed_coordinates(normals: np.ndarray, direction: Sequence[float], count: float) -> np.ndarray:
    """Stripe coordinates fixed to the part: the angle between each normal
    and *direction*, with *count* stripe pairs in 180 degrees."""
    d = _unit(np.asarray(direction, dtype=np.float64))
    return _angle_deg(normals @ d) * (float(count) / 180.0) + PHASE


def view_coordinates(
    normals: np.ndarray,
    view_direction: Sequence[float],
    axis: Sequence[float],
    count: float,
    points: Optional[np.ndarray] = None,
    eye: Optional[Sequence[float]] = None,
) -> np.ndarray:
    """Stripe coordinates that follow the view.

    *view_direction*: where the camera looks (unit, world). *axis*: the
    camera's right axis for vertical stripes, its up axis for horizontal
    ones. With *points* and *eye* (a perspective camera), each vertex is
    seen along its own ray from the eye instead.
    """
    if points is not None and eye is not None:
        d = _unit(points - np.asarray(eye, dtype=np.float64))
        dn = np.einsum("ij,ij->i", d, normals)
        r_axis = d @ np.asarray(axis, dtype=np.float64) - 2.0 * dn * (normals @ np.asarray(axis, dtype=np.float64))
    else:
        d = _unit(np.asarray(view_direction, dtype=np.float64))
        a = _unit(np.asarray(axis, dtype=np.float64))
        dn = normals @ d
        r_axis = float(d @ a) - 2.0 * dn * (normals @ a)
    return _angle_deg(r_axis) * (float(count) / 180.0) + PHASE


def stripe_image(width_share: float, first=(0, 0, 0), second=(255, 255, 255), size: int = 256) -> bytes:
    """One stripe pair as an RGB row of *size* pixels: *width_share* of it
    in the *first* color (0.1 to 0.9), the rest in the *second*."""
    share = min(max(float(width_share), 0.0), 1.0)
    split = int(round(share * size))
    row = bytearray()
    for x in range(size):
        r, g, b = first if x < split else second
        row += bytes((int(r), int(g), int(b)))
    return bytes(row)
