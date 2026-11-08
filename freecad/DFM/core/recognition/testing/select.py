# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Face selectors for ground truth: pick faces by geometry, not by number.

Face numbers change between OpenCASCADE versions and after STEP round
trips; "the internal cylinder of radius 3 on the axis through (20, 20)"
does not. Selectors are evaluated with their own small geometry code (not
the engine's descriptors), so ground truth is independent of what is
being tested. They combine with ``|`` (union) and ``&`` (intersection).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_SurfaceType as ST
from OCP.ShapeAnalysis import ShapeAnalysis_CanonicalRecognition
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.gp import gp_Cone, gp_Cylinder, gp_Pln, gp_Pnt

TOL = 1e-3


@dataclass
class _Face:
    index: int
    face: object
    kind: str
    centroid: tuple
    area: float
    axis: Optional[tuple] = None
    origin: Optional[tuple] = None
    radius: float = 0.0
    normal: Optional[tuple] = None


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v) if n else v


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _line_dist(p, o, d):
    v = _sub(p, o)
    t = _dot(v, d)
    w = tuple(v[i] - d[i] * t for i in range(3))
    return math.sqrt(_dot(w, w))


def _describe(shape: TopoDS_Shape) -> list[_Face]:
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, m)
    out = []
    for i in range(1, m.Extent() + 1):
        f = TopoDS.Face_s(m.FindKey(i))
        a = BRepAdaptor_Surface(f, True)
        t = a.GetType()
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f, props)
        c = props.CentreOfMass()
        info = _Face(i, f, "other", (c.X(), c.Y(), c.Z()), abs(props.Mass()))
        geom = None
        if t == ST.GeomAbs_Plane:
            info.kind, geom = "plane", a.Plane()
        elif t == ST.GeomAbs_Cylinder:
            info.kind, geom = "cylinder", a.Cylinder()
        elif t == ST.GeomAbs_Cone:
            info.kind, geom = "cone", a.Cone()
        elif t == ST.GeomAbs_Sphere:
            info.kind, geom = "sphere", a.Sphere()
        elif t == ST.GeomAbs_Torus:
            info.kind, geom = "torus", a.Torus()
        else:
            rec = ShapeAnalysis_CanonicalRecognition(f)
            for kind, obj, test in (("plane", gp_Pln(), rec.IsPlane), ("cylinder", gp_Cylinder(), rec.IsCylinder),
                                    ("cone", gp_Cone(), rec.IsCone)):
                try:
                    if test(1e-3, obj):
                        info.kind, geom = kind, obj
                        break
                except Exception:
                    pass
        if info.kind == "plane":
            d = geom.Axis().Direction()
            info.normal = (d.X(), d.Y(), d.Z())
            loc = geom.Location()
            info.origin = (loc.X(), loc.Y(), loc.Z())
        elif info.kind in ("cylinder", "cone", "torus"):
            ax = geom.Axis()
            d, o = ax.Direction(), ax.Location()
            info.axis = (d.X(), d.Y(), d.Z())
            info.origin = (o.X(), o.Y(), o.Z())
            info.radius = geom.MinorRadius() if info.kind == "torus" else (
                geom.Radius() if info.kind == "cylinder" else geom.RefRadius())
        elif info.kind == "sphere":
            o = geom.Location()
            info.origin = (o.X(), o.Y(), o.Z())
            info.radius = geom.Radius()
        out.append(info)
    return out


_CACHE: dict[int, tuple[TopoDS_Shape, list[_Face]]] = {}


def describe(shape: TopoDS_Shape) -> list[_Face]:
    key = id(shape)
    hit = _CACHE.get(key)
    if hit is not None and hit[0] is shape:
        return hit[1]
    faces = _describe(shape)
    if len(_CACHE) > 64:
        _CACHE.clear()
    _CACHE[key] = (shape, faces)
    return faces


class Sel:
    """A face selector: call it with a shape to get face numbers."""

    def __init__(self, pred: Callable[[_Face, TopoDS_Shape], bool], text: str):
        self.pred = pred
        self.text = text

    def __call__(self, shape: TopoDS_Shape) -> set[int]:
        return {f.index for f in describe(shape) if self.pred(f, shape)}

    def __or__(self, other: "Sel") -> "Sel":
        return Sel(lambda f, s: self.pred(f, s) or other.pred(f, s), f"({self.text} | {other.text})")

    def __and__(self, other: "Sel") -> "Sel":
        return Sel(lambda f, s: self.pred(f, s) and other.pred(f, s), f"({self.text} & {other.text})")

    def __repr__(self) -> str:
        return f"Sel<{self.text}>"


def _parallel(a, b, tol_deg=1.0):
    return abs(_dot(_unit(a), _unit(b))) >= math.cos(math.radians(tol_deg))


def cyl(r: Optional[float] = None, axis=None, at=None, tol: float = TOL) -> Sel:
    """Cylinders of radius r whose axis is parallel to *axis* through point *at*."""
    def p(f, s):
        if f.kind != "cylinder":
            return False
        if r is not None and abs(f.radius - r) > tol:
            return False
        if axis is not None and not _parallel(f.axis, axis):
            return False
        if at is not None and _line_dist(at, f.origin, _unit(f.axis)) > max(tol, 1e-4):
            return False
        return True
    return Sel(p, f"cyl(r={r}, axis={axis}, at={at})")


def cone(axis=None, at=None, tol: float = TOL) -> Sel:
    def p(f, s):
        if f.kind != "cone":
            return False
        if axis is not None and not _parallel(f.axis, axis):
            return False
        if at is not None and _line_dist(at, f.origin, _unit(f.axis)) > max(tol, 1e-4):
            return False
        return True
    return Sel(p, f"cone(axis={axis}, at={at})")


def torus(minor: Optional[float] = None, tol: float = TOL) -> Sel:
    return Sel(lambda f, s: f.kind == "torus" and (minor is None or abs(f.radius - minor) <= tol),
               f"torus({minor})")


def sphere(r: Optional[float] = None, tol: float = TOL) -> Sel:
    return Sel(lambda f, s: f.kind == "sphere" and (r is None or abs(f.radius - r) <= tol), f"sphere({r})")


def plane(normal=None, at=None, tol: float = TOL) -> Sel:
    """Planes with *normal* (either sign) containing point *at*."""
    def p(f, s):
        if f.kind != "plane":
            return False
        if normal is not None and not _parallel(f.normal, normal):
            return False
        if at is not None and abs(_dot(_sub(at, f.origin), _unit(f.normal))) > tol:
            return False
        return True
    return Sel(p, f"plane(n={normal}, at={at})")


def inside(x0, y0, z0, x1, y1, z1) -> Sel:
    """Faces whose centroid is in the box."""
    def p(f, s):
        x, y, z = f.centroid
        return x0 - TOL <= x <= x1 + TOL and y0 - TOL <= y <= y1 + TOL and z0 - TOL <= z <= z1 + TOL
    return Sel(p, f"inside({x0},{y0},{z0},{x1},{y1},{z1})")


def touching(point, tol: float = 1e-3) -> Sel:
    """Faces passing within tol of a point."""
    def p(f, s):
        v = BRepBuilderAPI_MakeVertex(gp_Pnt(*map(float, point))).Vertex()
        d = BRepExtrema_DistShapeShape(v, f.face)
        return d.IsDone() and d.NbSolution() > 0 and d.Value() <= tol
    return Sel(p, f"touching({point})")


def kind(*kinds: str) -> Sel:
    return Sel(lambda f, s: f.kind in kinds, f"kind{kinds}")


def all_faces() -> Sel:
    return Sel(lambda f, s: True, "all")


def none() -> Sel:
    return Sel(lambda f, s: False, "none")
