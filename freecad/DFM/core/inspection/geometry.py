# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Geometry for the inspection tools (OCP only, no FreeCAD).

* Transforms: a mirror, a rotation or a rigid move, applied to a copy of a
  shape while keeping track of which face became which.
* Mass properties: volume, centre of mass, principal axes, bounding box.
* The difference of two solids, split into separate regions. Each face of
  a region knows where it came from: a face of the first solid ("own") or
  of the second ("tool"), and which one, so a region can be named after
  the features it touches.
* Points on a shape, distances to a shape's surface, and inside tests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex, BRepBuilderAPI_Transform
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.Bnd import Bnd_Box
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_FACE, TopAbs_IN, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopTools import TopTools_IndexedMapOfShape, TopTools_ListOfShape
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Face, TopoDS_Shape
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

Vec = tuple[float, float, float]


# -- small vector helpers --------------------------------------------------------

def vadd(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vsub(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vmul(a: Sequence[float], k: float) -> Vec:
    return (a[0] * k, a[1] * k, a[2] * k)


def vdot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vlen(a: Sequence[float]) -> float:
    return math.sqrt(vdot(a, a))


def vunit(a: Sequence[float]) -> Vec:
    n = vlen(a)
    if n < 1e-12:
        raise ValueError("A direction can't be zero.")
    return (a[0] / n, a[1] / n, a[2] / n)


# -- transforms ------------------------------------------------------------------

def mirror(origin: Sequence[float], normal: Sequence[float]) -> gp_Trsf:
    """Reflection in the plane through *origin* with *normal*."""
    t = gp_Trsf()
    t.SetMirror(gp_Ax2(gp_Pnt(*origin), gp_Dir(*vunit(normal))))
    return t


def rotation(origin: Sequence[float], axis: Sequence[float], degrees: float) -> gp_Trsf:
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(gp_Pnt(*origin), gp_Dir(*vunit(axis))), math.radians(degrees))
    return t


def translation(offset: Sequence[float]) -> gp_Trsf:
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(*offset))
    return t


def rigid(matrix: Sequence[Sequence[float]]) -> gp_Trsf:
    """A rotation and translation from a 3 x 4 matrix (rows of [R | t])."""
    t = gp_Trsf()
    t.SetValues(*matrix[0], *matrix[1], *matrix[2])
    return t


def apply(trsf: gp_Trsf, point: Sequence[float]) -> Vec:
    p = gp_Pnt(*point).Transformed(trsf)
    return (p.X(), p.Y(), p.Z())


def apply_dir(trsf: gp_Trsf, direction: Sequence[float]) -> Vec:
    d = gp_Dir(*direction).Transformed(trsf)
    return (d.X(), d.Y(), d.Z())


@dataclass
class Moved:
    """A transformed copy of a shape, with its faces in the same order."""

    shape: TopoDS_Shape
    #: Face index (1-based) of the copy, for each face index of the original.
    face_map: dict[int, int]


def transformed(shape: TopoDS_Shape, trsf: gp_Trsf) -> Moved:
    """A copy of *shape* moved by *trsf* (mirrors included), with face mapping."""
    op = BRepBuilderAPI_Transform(shape, trsf, True)
    moved = op.Shape()
    src, dst = face_map_of(shape), face_map_of(moved)
    mapping = {}
    for i in range(1, src.Extent() + 1):
        image = op.ModifiedShape(src.FindKey(i))
        j = dst.FindIndex(image)
        if j:
            mapping[i] = j
    return Moved(moved, mapping)


def face_map_of(shape: TopoDS_Shape) -> TopTools_IndexedMapOfShape:
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, m)
    return m


# -- mass properties ------------------------------------------------------------------

@dataclass(frozen=True)
class MassProps:
    volume: float
    area: float
    centre: Vec
    #: Principal axes of inertia, unit vectors (the order OCC gives).
    axes: tuple[Vec, Vec, Vec]


def mass_props(shape: TopoDS_Shape) -> MassProps:
    vp = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, vp)
    sp = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, sp)
    c = vp.CentreOfMass()
    axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    try:
        pp = vp.PrincipalProperties()
        axes = tuple((v.X(), v.Y(), v.Z()) for v in (pp.FirstAxisOfInertia(), pp.SecondAxisOfInertia(),
                                                      pp.ThirdAxisOfInertia()))
    except Exception:
        pass
    return MassProps(abs(vp.Mass()), sp.Mass(), (c.X(), c.Y(), c.Z()), axes)  # type: ignore[arg-type]


def volume(shape: TopoDS_Shape) -> float:
    vp = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, vp)
    return abs(vp.Mass())


def centre(shape: TopoDS_Shape) -> Vec:
    vp = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, vp)
    c = vp.CentreOfMass()
    return (c.X(), c.Y(), c.Z())


def face_area(face: TopoDS_Shape) -> float:
    sp = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, sp)
    return abs(sp.Mass())


@dataclass(frozen=True)
class Box:
    lo: Vec
    hi: Vec

    @property
    def centre(self) -> Vec:
        return vmul(vadd(self.lo, self.hi), 0.5)

    @property
    def size(self) -> Vec:
        return vsub(self.hi, self.lo)

    @property
    def diagonal(self) -> float:
        return vlen(self.size)

    def grown(self, margin: float) -> "Box":
        return Box(vsub(self.lo, (margin,) * 3), vadd(self.hi, (margin,) * 3))

    def overlaps(self, other: "Box") -> bool:
        return all(self.lo[i] <= other.hi[i] and other.lo[i] <= self.hi[i] for i in range(3))

    def contains(self, p: Sequence[float]) -> bool:
        return all(self.lo[i] <= p[i] <= self.hi[i] for i in range(3))

    def corners(self) -> list[Vec]:
        return [(x, y, z) for x in (self.lo[0], self.hi[0]) for y in (self.lo[1], self.hi[1])
                for z in (self.lo[2], self.hi[2])]


def bounding_box(shape: TopoDS_Shape, optimal: bool = True) -> Box:
    box = Bnd_Box()
    if optimal:
        BRepBndLib.AddOptimal_s(shape, box, False, False)
    else:
        BRepBndLib.Add_s(shape, box)
    x0, y0, z0, x1, y1, z1 = box.Get()
    return Box((x0, y0, z0), (x1, y1, z1))


# -- shapes --------------------------------------------------------------------------

def compound(shapes: Iterable[TopoDS_Shape]) -> TopoDS_Compound:
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    for s in shapes:
        builder.Add(comp, s)
    return comp


def solids(shape: TopoDS_Shape) -> list[TopoDS_Shape]:
    out = []
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        out.append(exp.Current())
        exp.Next()
    return out


def faces(shape: TopoDS_Shape) -> list[TopoDS_Face]:
    out = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        out.append(TopoDS.Face_s(exp.Current()))
        exp.Next()
    return out


def boundary(shape: TopoDS_Shape) -> TopoDS_Shape:
    """The shells of *shape*: distances to them are distances to its surface."""
    shells = []
    exp = TopExp_Explorer(shape, TopAbs_SHELL)
    while exp.More():
        shells.append(exp.Current())
        exp.Next()
    return compound(shells) if shells else shape


# -- difference regions --------------------------------------------------------------

#: Where a region's face came from.
OWN, TOOL, NEW = "own", "tool", "new"


@dataclass
class Region:
    """One connected piece of material in one solid and not in another."""

    shape: TopoDS_Shape
    volume: float
    box: Box
    #: (origin, face index of that solid (1-based), area of the piece) for
    #: each face of the region. origin is OWN (a face of the first solid),
    #: TOOL (of the second) or NEW (made by the operation).
    face_sources: list[tuple[str, int, float]] = field(default_factory=list)

    def area_from(self, origin: str) -> dict[int, float]:
        """Area of the region's boundary lying on each face of *origin*."""
        out: dict[int, float] = {}
        for o, index, area in self.face_sources:
            if o == origin and index:
                out[index] = out.get(index, 0.0) + area
        return out


def difference(own: TopoDS_Shape, tool: TopoDS_Shape, tolerance: float,
               min_volume: float = 0.0) -> list[Region]:
    """The material of *own* that isn't in *tool*, as separate regions.

    The cut runs with *tolerance* as its fuzzy value, so surfaces closer
    than that count as the same surface. Pieces under *min_volume* (mm³)
    are dropped.
    """
    args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    args.Append(own)
    tools.Append(tool)
    op = BRepAlgoAPI_Cut()
    op.SetArguments(args)
    op.SetTools(tools)
    op.SetFuzzyValue(max(float(tolerance), 1e-7))
    op.SetRunParallel(True)
    op.SetNonDestructive(True)
    op.Build()
    if not op.IsDone():
        raise RuntimeError("The boolean operation failed.")
    result = op.Shape()

    # Every face of the result: which face of which input it came from.
    result_faces = face_map_of(result)
    source: dict[int, tuple[str, int]] = {}
    for origin, shape in ((OWN, own), (TOOL, tool)):
        fm = face_map_of(shape)
        for i in range(1, fm.Extent() + 1):
            f = fm.FindKey(i)
            images = op.Modified(f)
            if images.Size() == 0:
                j = result_faces.FindIndex(f)
                if j:
                    source.setdefault(j, (origin, i))
                continue
            for image in _iter_list(images):
                j = result_faces.FindIndex(image)
                if j:
                    source.setdefault(j, (origin, i))

    regions = []
    for solid in solids(result):
        v = volume(solid)
        if v <= min_volume:
            continue
        srcs = []
        for f in faces(solid):
            j = result_faces.FindIndex(f)
            origin, index = source.get(j, (NEW, 0))
            srcs.append((origin, index, face_area(f)))
        regions.append(Region(solid, v, bounding_box(solid), srcs))
    return regions


def clip(shape: TopoDS_Shape, box: Box) -> tuple[TopoDS_Shape, dict[int, int]]:
    """*shape* inside *box*, and for each of its faces the face of *shape* it came from."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    solid = BRepPrimAPI_MakeBox(gp_Pnt(*box.lo), gp_Pnt(*box.hi)).Shape()
    args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    args.Append(shape)
    tools.Append(solid)
    op = BRepAlgoAPI_Common()
    op.SetArguments(args)
    op.SetTools(tools)
    op.SetRunParallel(True)
    op.SetNonDestructive(True)
    op.Build()
    if not op.IsDone():
        raise RuntimeError("The boolean operation failed.")
    result = op.Shape()
    result_faces = face_map_of(result)
    origin: dict[int, int] = {}
    fm = face_map_of(shape)
    for i in range(1, fm.Extent() + 1):
        f = fm.FindKey(i)
        images = op.Modified(f)
        if images.Size() == 0:
            j = result_faces.FindIndex(f)
            if j:
                origin.setdefault(j, i)
            continue
        for image in _iter_list(images):
            j = result_faces.FindIndex(image)
            if j:
                origin.setdefault(j, i)
    return result, origin


def local_difference(own: TopoDS_Shape, tool: TopoDS_Shape, boxes: Sequence[Box], tolerance: float,
                     min_volume: float = 0.0) -> list[Region]:
    """:func:`difference`, inside *boxes* only (the shapes are the same outside them).

    Each box is cut out of both shapes first, so the slow cut sees only
    the faces near a difference.
    """
    regions: list[Region] = []
    for box in boxes:
        own_c, own_map = clip(own, box)
        if not solids(own_c):
            continue
        tool_c, tool_map = clip(tool, box)
        if solids(tool_c):
            found = difference(own_c, tool_c, tolerance, min_volume)
        else:
            found = [Region(s, volume(s), bounding_box(s), [(OWN, j, face_area(f)) for f, j in
                                                            ((f, face_map_of(own_c).FindIndex(f)) for f in faces(s))])
                     for s in solids(own_c) if volume(s) > min_volume]
        for r in found:
            remapped = []
            for origin, index, area in r.face_sources:
                if origin == OWN:
                    j = own_map.get(index, 0)
                elif origin == TOOL:
                    j = tool_map.get(index, 0)
                else:
                    j = 0
                remapped.append((origin if j else NEW, j, area))
            r.face_sources = remapped
            regions.append(r)
    return regions


def _iter_list(lst: TopTools_ListOfShape):
    """Iterate a TopTools_ListOfShape (OCP exposes it as an iterable)."""
    try:
        for item in lst:
            yield item
    except TypeError:  # older OCP: no Python iteration
        from OCP.TopTools import TopTools_ListIteratorOfListOfShape
        it = TopTools_ListIteratorOfListOfShape(lst)
        while it.More():
            yield it.Value()
            it.Next()


# -- points and distances ---------------------------------------------------------------

@dataclass
class Mesh:
    vertices: list[Vec]
    triangles: list[tuple[int, int, int]]


def triangulate(shape: TopoDS_Shape, deflection: Optional[float] = None,
                angle: float = 0.35) -> Mesh:
    """Triangles of *shape*'s faces, outward-facing (for display and sampling)."""
    if deflection is None:
        deflection = max(bounding_box(shape, optimal=False).diagonal / 400.0, 1e-3)
    BRepMesh_IncrementalMesh(shape, deflection, False, angle, True)
    vertices: list[Vec] = []
    triangles: list[tuple[int, int, int]] = []
    for face in faces(shape):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            continue
        trsf = loc.Transformation()
        base = len(vertices)
        for i in range(1, tri.NbNodes() + 1):
            p = tri.Node(i).Transformed(trsf)
            vertices.append((p.X(), p.Y(), p.Z()))
        reverse = face.Orientation() == TopAbs_REVERSED
        for i in range(1, tri.NbTriangles() + 1):
            a, b, c = tri.Triangle(i).Get()
            if reverse:
                b, c = c, b
            triangles.append((base + a - 1, base + b - 1, base + c - 1))
    return Mesh(vertices, triangles)


def sample_points(shape: TopoDS_Shape, deflection: Optional[float] = None, grid: int = 7) -> list[Vec]:
    """Points on *shape*'s surface: its mesh nodes and triangle centres, and
    a grid over each face (a flat face meshes as a few long triangles, whose
    nodes all sit on its edges)."""
    mesh = triangulate(shape, deflection)
    pts = list(mesh.vertices)
    for a, b, c in mesh.triangles:
        va, vb, vc = mesh.vertices[a], mesh.vertices[b], mesh.vertices[c]
        pts.append(((va[0] + vb[0] + vc[0]) / 3, (va[1] + vb[1] + vc[1]) / 3, (va[2] + vb[2] + vc[2]) / 3))
    if grid > 1:
        for face in faces(shape):
            pts.extend(face_grid(face, grid))
    return pts


def face_grid(face: TopoDS_Face, n: int = 7) -> list[Vec]:
    """An n x n grid of points over *face*'s parameters, those on the face."""
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_ON
    from OCP.gp import gp_Pnt2d

    try:
        u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
        surface = BRep_Tool.Surface_s(face)
    except Exception:
        return []
    cls = BRepClass_FaceClassifier()
    out = []
    for i in range(n):
        u = u0 + (u1 - u0) * (i + 0.5) / n
        for j in range(n):
            v = v0 + (v1 - v0) * (j + 0.5) / n
            cls.Perform(face, gp_Pnt2d(u, v), 1e-7)
            if cls.State() in (TopAbs_IN, TopAbs_ON):
                p = surface.Value(u, v)
                out.append((p.X(), p.Y(), p.Z()))
    return out


class SurfaceDistance:
    """Distances from points to the surface of a shape (its faces near a box)."""

    def __init__(self, shape: TopoDS_Shape, near: Optional[Box] = None):
        target = shape
        if near is not None:
            picked = [f for f in faces(shape) if bounding_box(f, optimal=False).overlaps(near)]
            target = compound(picked) if picked else boundary(shape)
        else:
            target = boundary(shape)
        self._calc = BRepExtrema_DistShapeShape()
        self._calc.LoadS1(target)

    def __call__(self, point: Sequence[float]) -> float:
        vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*point)).Vertex()
        self._calc.LoadS2(vertex)
        self._calc.Perform()
        if not self._calc.IsDone() or self._calc.NbSolution() == 0:
            return math.inf
        return self._calc.Value()


class Inside:
    """Whether points are inside a solid."""

    def __init__(self, shape: TopoDS_Shape, tolerance: float = 1e-6):
        self._cls = BRepClass3d_SolidClassifier(shape)
        self._tol = tolerance

    def __call__(self, point: Sequence[float]) -> bool:
        self._cls.Perform(gp_Pnt(*point), self._tol)
        return self._cls.State() == TopAbs_IN


def spread_points(shape: TopoDS_Shape, limit: int = 400) -> list[Vec]:
    """Up to *limit* points over *shape*'s surface: its mesh nodes first
    (corners and edges, where a region's furthest points are), then face
    grids, evenly thinned."""
    mesh = triangulate(shape)
    nodes = list(dict.fromkeys((round(x, 6), round(y, 6), round(z, 6)) for x, y, z in mesh.vertices))
    if len(nodes) >= limit:
        step = len(nodes) / limit
        return [nodes[int(i * step)] for i in range(limit)]
    rest = []
    for face in faces(shape):
        rest.extend(face_grid(face, 4))
    room = limit - len(nodes)
    if len(rest) > room:
        step = len(rest) / room
        rest = [rest[int(i * step)] for i in range(room)]
    return nodes + rest


def max_distance(points: Iterable[Sequence[float]], to: TopoDS_Shape, near: Optional[Box] = None) -> float:
    """Largest distance from *points* to *to*'s surface (near *near*)."""
    dist = SurfaceDistance(to, near)
    best = 0.0
    for p in points:
        d = dist(p)
        if math.isfinite(d):
            best = max(best, d)
    return best
