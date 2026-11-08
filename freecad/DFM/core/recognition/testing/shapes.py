# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""A tiny OCP modelling kit for test parts: primitives, booleans, drills,
fillets, chamfers, extruded profiles, shells, threads, transforms, and
file round trips (BREP, STEP)."""

from __future__ import annotations

import math
import os
import tempfile
from typing import Callable, Iterable, Optional, Sequence

from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
                                BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeWire,
                                BRepBuilderAPI_NurbsConvert, BRepBuilderAPI_Transform)
from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
from OCP.BRepPrimAPI import (BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder,
                             BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol, BRepPrimAPI_MakeSphere)
from OCP.BRepTools import BRepTools
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape, TopTools_ListOfShape
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

Vec = tuple[float, float, float]


def _p(v) -> gp_Pnt:
    return gp_Pnt(*map(float, v))


def _d(v) -> gp_Dir:
    return gp_Dir(*map(float, v))


def clean(shape: TopoDS_Shape) -> TopoDS_Shape:
    """Merge coplanar / co-cylindrical faces left by booleans."""
    u = ShapeUpgrade_UnifySameDomain(shape, True, True, False)
    u.Build()
    return u.Shape()


# -- Primitives -----------------------------------------------------------------------


def box(x0, y0, z0, x1, y1, z1) -> TopoDS_Shape:
    return BRepPrimAPI_MakeBox(_p((x0, y0, z0)), _p((x1, y1, z1))).Shape()


def cylinder(base: Vec, radius: float, height: float, axis: Vec = (0, 0, 1)) -> TopoDS_Shape:
    return BRepPrimAPI_MakeCylinder(gp_Ax2(_p(base), _d(axis)), radius, height).Shape()


def cone(base: Vec, r1: float, r2: float, height: float, axis: Vec = (0, 0, 1)) -> TopoDS_Shape:
    return BRepPrimAPI_MakeCone(gp_Ax2(_p(base), _d(axis)), r1, r2, height).Shape()


def sphere(centre: Vec, radius: float) -> TopoDS_Shape:
    return BRepPrimAPI_MakeSphere(_p(centre), radius).Shape()


def prism(points: Sequence[Vec], vector: Vec) -> TopoDS_Shape:
    """Extrude a closed planar polygon along *vector*."""
    poly = BRepBuilderAPI_MakePolygon()
    for p in points:
        poly.Add(_p(p))
    poly.Close()
    face = BRepBuilderAPI_MakeFace(poly.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(*map(float, vector))).Shape()


def loft(*sections: Sequence[Vec]) -> TopoDS_Shape:
    """A solid through closed polygons with the same number of corners
    (ruled: flat faces between matching edges)."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections

    builder = BRepOffsetAPI_ThruSections(True, True)
    for pts in sections:
        poly = BRepBuilderAPI_MakePolygon()
        for p in pts:
            poly.Add(_p(p))
        poly.Close()
        builder.AddWire(poly.Wire())
    builder.Build()
    return builder.Shape()


def drafted_box(x0, y0, z0, x1, y1, z1, draft_deg: float, grow: bool = False) -> TopoDS_Shape:
    """A box whose four sides lean *draft_deg* inwards going up (outwards
    with *grow*): a moulded block, or the tool for a drafted pocket."""
    k = math.tan(math.radians(draft_deg)) * (z1 - z0) * (-1.0 if grow else 1.0)
    bottom = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)]
    top = [(x0 + k, y0 + k, z1), (x1 - k, y0 + k, z1), (x1 - k, y1 - k, z1), (x0 + k, y1 - k, z1)]
    return loft(bottom, top)


def drafted_prism(profile: Sequence[tuple[float, float]], z0: float, z1: float, draft_deg: float,
                  grow: bool = False) -> TopoDS_Shape:
    """A convex polygon (x, y) extruded from z0 to z1, its sides leaning
    *draft_deg* inwards going up (outwards with *grow*)."""
    n = len(profile)
    cx = sum(p[0] for p in profile) / n
    cy = sum(p[1] for p in profile) / n
    k = math.tan(math.radians(draft_deg)) * (z1 - z0) * (-1.0 if grow else 1.0)
    # offset each edge inwards by k, then intersect neighbouring edges
    lines = []
    for i in range(n):
        (ax, ay), (bx, by) = profile[i], profile[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        ln = math.hypot(dx, dy)
        nx, ny = -dy / ln, dx / ln
        if (cx - ax) * nx + (cy - ay) * ny < 0:
            nx, ny = -nx, -ny
        lines.append(((ax + nx * k, ay + ny * k), (dx, dy)))
    top = []
    for i in range(n):
        (p1, d1), (p2, d2) = lines[i - 1], lines[i]
        den = d1[0] * d2[1] - d1[1] * d2[0]
        t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / den
        top.append((p1[0] + d1[0] * t, p1[1] + d1[1] * t, z1))
    return loft([(x, y, z0) for x, y in profile], top)


def revolve(points: Sequence[Vec], axis_point: Vec = (0, 0, 0), axis: Vec = (0, 0, 1),
            angle_deg: float = 360.0) -> TopoDS_Shape:
    poly = BRepBuilderAPI_MakePolygon()
    for p in points:
        poly.Add(_p(p))
    poly.Close()
    face = BRepBuilderAPI_MakeFace(poly.Wire()).Face()
    return BRepPrimAPI_MakeRevol(face, gp_Ax1(_p(axis_point), _d(axis)), math.radians(angle_deg)).Shape()


# -- Booleans --------------------------------------------------------------------------


def cut(a: TopoDS_Shape, *tools: TopoDS_Shape, unify: bool = True) -> TopoDS_Shape:
    for t in tools:
        op = BRepAlgoAPI_Cut(a, t)
        op.Build()
        if not op.IsDone():
            raise RuntimeError("cut failed")
        a = op.Shape()
    return clean(a) if unify else a


def fuse(a: TopoDS_Shape, *others: TopoDS_Shape, unify: bool = True) -> TopoDS_Shape:
    for t in others:
        op = BRepAlgoAPI_Fuse(a, t)
        op.Build()
        if not op.IsDone():
            raise RuntimeError("fuse failed")
        a = op.Shape()
    return clean(a) if unify else a


# -- Hole tools (solids to cut) ---------------------------------------------------------------


def drill(entry: Vec, axis: Vec, diameter: float, depth: float, point_angle: Optional[float] = None,
          spherical: bool = False, extra: float = 1.0) -> TopoDS_Shape:
    """A drill body entering at *entry* along *axis* (into the material).
    Starts *extra* above the entry so the cut is clean."""
    ax = _unit(axis)
    start = _add(entry, _mul(ax, -extra))
    body = cylinder(start, diameter / 2, depth + extra, ax)
    if point_angle:
        h = (diameter / 2) / math.tan(math.radians(point_angle / 2))
        tip = cone(_add(entry, _mul(ax, depth)), diameter / 2, 0.0, h, ax)
        body = fuse(body, tip, unify=False)
    if spherical:
        body = fuse(body, sphere(_add(entry, _mul(ax, depth)), diameter / 2), unify=False)
    return body


def counterbore_tool(entry: Vec, axis: Vec, d: float, depth: float, cb_d: float, cb_depth: float,
                     point_angle: Optional[float] = None) -> TopoDS_Shape:
    ax = _unit(axis)
    return fuse(drill(entry, ax, d, depth, point_angle), drill(entry, ax, cb_d, cb_depth), unify=False)


def countersink_tool(entry: Vec, axis: Vec, d: float, depth: float, cs_d: float, cs_angle: float = 90.0,
                     point_angle: Optional[float] = None) -> TopoDS_Shape:
    ax = _unit(axis)
    h = (cs_d / 2) / math.tan(math.radians(cs_angle / 2))
    extra = 1.0
    r_top = (cs_d / 2) + extra * math.tan(math.radians(cs_angle / 2))
    sink = cone(_add(entry, _mul(ax, -extra)), r_top, 0.0, h + extra, ax)
    return fuse(drill(entry, ax, d, depth, point_angle), sink, unify=False)


# -- Blends ---------------------------------------------------------------------------------


def edges_of(shape: TopoDS_Shape) -> list:
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, m)
    return [TopoDS.Edge_s(m.FindKey(i)) for i in range(1, m.Extent() + 1)]


def edge_midpoint(edge) -> Vec:
    c = BRepAdaptor_Curve(edge)
    p = c.Value((c.FirstParameter() + c.LastParameter()) / 2)
    return (p.X(), p.Y(), p.Z())


def edges_where(shape: TopoDS_Shape, pred: Callable[[Vec, object], bool]) -> list:
    out = []
    for e in edges_of(shape):
        if BRep_Tool.Degenerated_s(e):
            continue
        if pred(edge_midpoint(e), e):
            out.append(e)
    return out


def edges_near(shape: TopoDS_Shape, points: Iterable[Vec], tol: float = 1e-3) -> list:
    pts = list(points)
    return edges_where(shape, lambda m, e: any(_dist(m, p) <= tol for p in pts))


def fillet(shape: TopoDS_Shape, radius: float, edges: Iterable) -> TopoDS_Shape:
    mk = BRepFilletAPI_MakeFillet(shape)
    n = 0
    for e in edges:
        mk.Add(radius, e)
        n += 1
    if not n:
        raise ValueError("no edges to fillet")
    mk.Build()
    if not mk.IsDone():
        raise RuntimeError("fillet failed")
    return mk.Shape()


def chamfer(shape: TopoDS_Shape, distance: float, edges: Iterable) -> TopoDS_Shape:
    mk = BRepFilletAPI_MakeChamfer(shape)
    n = 0
    for e in edges:
        mk.Add(distance, e)
        n += 1
    if not n:
        raise ValueError("no edges to chamfer")
    mk.Build()
    if not mk.IsDone():
        raise RuntimeError("chamfer failed")
    return mk.Shape()


def faces_of(shape: TopoDS_Shape) -> list:
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, m)
    return [TopoDS.Face_s(m.FindKey(i)) for i in range(1, m.Extent() + 1)]


def shell(shape: TopoDS_Shape, thickness: float, open_face_pred: Callable) -> TopoDS_Shape:
    """Hollow a solid, removing the faces open_face_pred(face) selects; walls
    of *thickness* grow inwards."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    remove = TopTools_ListOfShape()
    for f in faces_of(shape):
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f, props)
        c = props.CentreOfMass()
        if open_face_pred((c.X(), c.Y(), c.Z())):
            remove.Append(f)
    mk = BRepOffsetAPI_MakeThickSolid()
    mk.MakeThickSolidByJoin(shape, remove, -thickness, 1e-4)
    mk.Build()
    return mk.Shape()


# -- Threads ----------------------------------------------------------------------------------


def helical_thread(centre: Vec, radius: float, pitch: float, length: float, depth: float,
                   external: bool = False) -> TopoDS_Shape:
    """A V-thread ridge swept along a helix about +Z through *centre*.

    For an internal thread cut it from a hole of radius *radius*; for an
    external one, cut it from a cylinder of that radius."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
    from OCP.Geom import Geom_CylindricalSurface
    from OCP.Geom2d import Geom2d_Line
    from OCP.gp import gp_Ax3, gp_Dir2d, gp_Pnt2d
    from OCP.BRepLib import BRepLib

    ax3 = gp_Ax3(_p(centre), gp_Dir(0, 0, 1))
    surf = Geom_CylindricalSurface(ax3, radius)
    turns = length / pitch
    line = Geom2d_Line(gp_Pnt2d(0, 0), gp_Dir2d(2 * math.pi, pitch))
    seg_len = math.sqrt((2 * math.pi) ** 2 + pitch ** 2) * turns
    edge = BRepBuilderAPI_MakeEdge(line, surf, 0.0, seg_len).Edge()
    BRepLib.BuildCurves3d_s(edge)
    spine = BRepBuilderAPI_MakeWire(edge).Wire()
    half = pitch * 0.4
    x0 = centre[0] + radius
    sgn = -1.0 if external else 1.0
    tri = BRepBuilderAPI_MakePolygon(
        gp_Pnt(x0 - sgn * 0.05, centre[1], centre[2] - half),
        gp_Pnt(x0 + sgn * depth, centre[1], centre[2]),
        gp_Pnt(x0 - sgn * 0.05, centre[1], centre[2] + half), True).Wire()
    pipe = BRepOffsetAPI_MakePipeShell(spine)
    pipe.SetMode(gp_Dir(0, 0, 1))
    pipe.Add(tri)
    pipe.Build()
    pipe.MakeSolid()
    return pipe.Shape()


# -- Transforms and files ---------------------------------------------------------------------


def moved(shape: TopoDS_Shape, translate: Vec = (0, 0, 0), axis: Vec = (0, 0, 1),
          angle_deg: float = 0.0, about: Vec = (0, 0, 0)) -> TopoDS_Shape:
    t = gp_Trsf()
    if angle_deg:
        t.SetRotation(gp_Ax1(_p(about), _d(axis)), math.radians(angle_deg))
    t2 = gp_Trsf()
    t2.SetTranslation(gp_Vec(*map(float, translate)))
    t2.Multiply(t)
    return BRepBuilderAPI_Transform(shape, t2, True).Shape()


def nurbs(shape: TopoDS_Shape) -> TopoDS_Shape:
    return BRepBuilderAPI_NurbsConvert(shape, True).Shape()


def write_brep(shape: TopoDS_Shape, path: str) -> None:
    BRepTools.Write_s(shape, path)


def read_brep(path: str) -> TopoDS_Shape:
    s = TopoDS_Shape()
    BRepTools.Read_s(s, path, BRep_Builder())
    if s.IsNull():
        raise RuntimeError(f"could not read {path}")
    return s


def write_step(shape: TopoDS_Shape, path: str) -> None:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCP.Interface import Interface_Static

    Interface_Static.SetCVal_s("write.step.schema", "AP214")
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    if w.Write(path) != IFSelect_RetDone:
        raise RuntimeError(f"could not write {path}")


def read_step(path: str) -> TopoDS_Shape:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    r = STEPControl_Reader()
    if r.ReadFile(path) != IFSelect_RetDone:
        raise RuntimeError(f"could not read {path}")
    r.TransferRoots()
    return r.OneShape()


def step_roundtrip(shape: TopoDS_Shape) -> TopoDS_Shape:
    fd, path = tempfile.mkstemp(suffix=".step")
    os.close(fd)
    try:
        write_step(shape, path)
        return read_step(path)
    finally:
        os.remove(path)


# -- Small vector helpers ----------------------------------------------------------------------


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def _add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _mul(a, s):
    return tuple(x * s for x in a)


def _dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
