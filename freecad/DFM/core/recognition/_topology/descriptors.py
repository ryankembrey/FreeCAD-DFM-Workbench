# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Face and edge descriptors: the attributes of the adjacency graph."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Curve2d, BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools
from OCP.Bnd import Bnd_Box
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_CurveType, GeomAbs_SurfaceType
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.GeomLProp import GeomLProp_SLProps
from OCP.ShapeAnalysis import ShapeAnalysis_CanonicalRecognition
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_WIRE
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
from OCP.TopoDS import TopoDS, TopoDS_Edge, TopoDS_Face, TopoDS_Shape
from OCP.gp import gp_Cone, gp_Cylinder, gp_Pln, gp_Pnt, gp_Sphere

from ...utils.geometry import EdgeIndex, FaceIndex
from . import vec as V

_ST = GeomAbs_SurfaceType
_CT = GeomAbs_CurveType

SURFACE_KINDS = {
    _ST.GeomAbs_Plane: "plane",
    _ST.GeomAbs_Cylinder: "cylinder",
    _ST.GeomAbs_Cone: "cone",
    _ST.GeomAbs_Sphere: "sphere",
    _ST.GeomAbs_Torus: "torus",
    _ST.GeomAbs_BezierSurface: "bspline",
    _ST.GeomAbs_BSplineSurface: "bspline",
    _ST.GeomAbs_SurfaceOfRevolution: "revolution",
    _ST.GeomAbs_SurfaceOfExtrusion: "extrusion",
    _ST.GeomAbs_OffsetSurface: "offset",
    _ST.GeomAbs_OtherSurface: "other",
}

CURVE_KINDS = {
    _CT.GeomAbs_Line: "line",
    _CT.GeomAbs_Circle: "circle",
    _CT.GeomAbs_Ellipse: "ellipse",
    _CT.GeomAbs_Hyperbola: "hyperbola",
    _CT.GeomAbs_Parabola: "parabola",
    _CT.GeomAbs_BezierCurve: "bspline",
    _CT.GeomAbs_BSplineCurve: "bspline",
    _CT.GeomAbs_OffsetCurve: "offset",
    _CT.GeomAbs_OtherCurve: "other",
}

REVOLVED = ("cylinder", "cone", "sphere", "torus")


def _pnt(p) -> V.Vec:
    return (p.X(), p.Y(), p.Z())


@dataclass
class FaceInfo:
    index: int
    face: TopoDS_Face = field(repr=False)
    kind: str                      # plane, cylinder, cone, sphere, torus, bspline, ...
    native_kind: str               # the surface type before canonical recognition
    reversed: bool
    area: float
    centroid: V.Vec
    bbox: tuple[float, float, float, float, float, float]
    fit_gap: float = 0.0           # canonical recognition gap (0 for native analytic)
    # Plane
    normal: Optional[V.Vec] = None     # outward normal (planes)
    origin: Optional[V.Vec] = None     # a point on the plane / axis / centre
    # Revolved
    axis: Optional[V.Vec] = None
    radius: float = 0.0                # cylinder radius, cone ref radius, sphere radius, torus minor
    major_radius: float = 0.0          # torus
    semi_angle: float = 0.0            # cone, degrees
    internal: Optional[bool] = None    # normal points at the axis/centre (a hole-like face)
    span: float = 0.0                  # degrees of arc around the axis covered by the face
    r_min: float = 0.0                 # radial extent of the boundary (revolved faces)
    r_max: float = 0.0
    axial_min: float = 0.0             # extent along the axis (from origin)
    axial_max: float = 0.0
    # Loops (edge numbers)
    outer: list[int] = field(default_factory=list)
    inner: list[list[int]] = field(default_factory=list)
    # A normal at an interior point, and that point
    sample_point: Optional[V.Vec] = None
    sample_normal: Optional[V.Vec] = None
    # Free-form faces: a constant principal curvature (a rolling-ball blend)
    curvature_radius: float = 0.0

    @property
    def edges(self) -> list[int]:
        out = list(self.outer)
        for w in self.inner:
            out.extend(w)
        return out

    @property
    def revolved(self) -> bool:
        return self.kind in REVOLVED

    @property
    def full_turn(self) -> bool:
        return self.span >= 359.0


@dataclass
class EdgeInfo:
    index: int
    edge: TopoDS_Edge = field(repr=False)
    faces: tuple[int, ...]            # 1 (free), 2, or (f, f) for a seam
    curve: str
    length: float
    convexity: str = "none"           # convex, concave, smooth, variable, seam, free, degenerate
    angle: float = 0.0                # signed turn angle, degrees: + convex, - concave
    samples: tuple[float, ...] = ()
    # Circles
    center: Optional[V.Vec] = None
    circle_axis: Optional[V.Vec] = None
    circle_radius: float = 0.0
    points: tuple[V.Vec, ...] = ()    # a few points along the edge (for measurements)
    native_curve: str = ""            # the curve type before fitting (bspline edges)

    @property
    def straight(self) -> bool:
        return self.curve == "line"

    @property
    def is_manifold(self) -> bool:
        return len(self.faces) == 2 and self.faces[0] != self.faces[1]

    def other(self, face: int) -> int:
        a, b = self.faces[0], self.faces[-1]
        return b if a == face else a


@dataclass
class Topology:
    shape: TopoDS_Shape = field(repr=False)
    face_index: FaceIndex = field(repr=False)
    edge_index: EdgeIndex = field(repr=False)
    faces: list[Optional[FaceInfo]]   # [None, Face1, Face2, ...]
    edges: list[Optional[EdgeInfo]]   # [None, Edge1, ...]
    scale: float                      # bounding box diagonal

    @property
    def face_count(self) -> int:
        return len(self.faces) - 1

    @property
    def edge_count(self) -> int:
        return len(self.edges) - 1

    def face(self, i: int) -> FaceInfo:
        return self.faces[i]  # type: ignore[return-value]

    def edge(self, i: int) -> EdgeInfo:
        return self.edges[i]  # type: ignore[return-value]

    def iter_faces(self):
        return (f for f in self.faces[1:] if f is not None)

    def iter_edges(self):
        return (e for e in self.edges[1:] if e is not None)


# -- Faces --------------------------------------------------------------------------


def surface_normal(face: TopoDS_Face, u: float, v: float) -> Optional[V.Vec]:
    """Outward normal (face orientation applied) at (u, v)."""
    surface = BRep_Tool.Surface_s(face)
    props = GeomLProp_SLProps(surface, u, v, 1, 1e-9)
    if not props.IsNormalDefined():
        return None
    n = props.Normal()
    d = (n.X(), n.Y(), n.Z())
    if face.Orientation() == TopAbs_REVERSED:
        d = V.mul(d, -1.0)
    return d


def _interior_sample(face: TopoDS_Face) -> tuple[Optional[V.Vec], Optional[V.Vec]]:
    """A point inside the face and the normal there."""
    from OCP.BRepTopAdaptor import BRepTopAdaptor_FClass2d
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt2d

    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    classifier = BRepTopAdaptor_FClass2d(face, 1e-7)
    surface = BRep_Tool.Surface_s(face)
    candidates = [(0.5, 0.5)]
    for i in (0.25, 0.75, 0.1, 0.9, 0.4, 0.6):
        for j in (0.5, 0.25, 0.75, 0.1, 0.9):
            candidates.append((i, j))
    for fu, fv in candidates:
        u = u0 + (u1 - u0) * fu
        v = v0 + (v1 - v0) * fv
        if classifier.Perform(gp_Pnt2d(u, v)) == TopAbs_IN:
            n = surface_normal(face, u, v)
            if n is not None:
                return _pnt(surface.Value(u, v)), V.unit(n)
    u, v = (u0 + u1) / 2, (v0 + v1) / 2
    n = surface_normal(face, u, v)
    return _pnt(surface.Value(u, v)), (V.unit(n) if n else None)


def _canonical(face: TopoDS_Face, tol: float):
    """(kind, gp object, gap) for a B-spline face that is really analytic."""
    try:
        rec = ShapeAnalysis_CanonicalRecognition(face)
        pln = gp_Pln()
        if rec.IsPlane(tol, pln):
            return "plane", pln, max(0.0, rec.GetGap())
        cyl = gp_Cylinder()
        if rec.IsCylinder(tol, cyl):
            return "cylinder", cyl, max(0.0, rec.GetGap())
        cone = gp_Cone()
        if rec.IsCone(tol, cone):
            return "cone", cone, max(0.0, rec.GetGap())
        sph = gp_Sphere()
        if rec.IsSphere(tol, sph):
            return "sphere", sph, max(0.0, rec.GetGap())
    except Exception:  # pragma: no cover - kernel failure on odd input
        pass
    return None, None, 0.0


def _boundary_points(face: TopoDS_Face, per_edge: int = 25) -> list[V.Vec]:
    pts: list[V.Vec] = []
    ex = TopExp_Explorer(face, TopAbs_EDGE)
    while ex.More():
        edge = TopoDS.Edge_s(ex.Current())
        if not BRep_Tool.Degenerated_s(edge):
            c = BRepAdaptor_Curve(edge)
            a, b = c.FirstParameter(), c.LastParameter()
            if math.isfinite(a) and math.isfinite(b):
                for k in range(per_edge):
                    pts.append(_pnt(c.Value(a + (b - a) * k / (per_edge - 1))))
        ex.Next()
    return pts


def _arc_span(points: list[V.Vec], origin: V.Vec, axis: V.Vec, interior: Optional[V.Vec]) -> float:
    """Degrees of arc around the axis that the face covers.

    The boundary points give the angles; the biggest gap between them is
    the part of the circle the face doesn't cover, unless the interior point
    falls in it (then the face covers that gap, and the second-biggest gap
    pattern doesn't apply: the face is the big arc).
    """
    ref = V.any_perpendicular(axis)
    ref2 = V.cross(axis, ref)

    def ang(p):
        r = V.radial(p, origin, axis)
        if V.norm(r) < 1e-9:
            return None
        return math.degrees(math.atan2(V.dot(r, ref2), V.dot(r, ref))) % 360.0

    angles = sorted({round(a, 6) for a in (ang(p) for p in points) if a is not None})
    if len(angles) < 2:
        return 360.0
    gaps = []
    for i, a in enumerate(angles):
        nxt = angles[(i + 1) % len(angles)] + (360.0 if i == len(angles) - 1 else 0.0)
        gaps.append((nxt - a, a))
    gap, start = max(gaps)
    if gap < 20.0:
        return 360.0
    if interior is not None:
        ai = ang(interior)
        if ai is not None:
            rel = (ai - start) % 360.0
            if rel < gap:  # the interior lies in the "gap": the face is that arc
                return gap
    return 360.0 - gap


def describe_face(index: int, face: TopoDS_Face, edge_index: EdgeIndex, options) -> FaceInfo:
    adaptor = BRepAdaptor_Surface(face, True)
    native = SURFACE_KINDS.get(adaptor.GetType(), "other")
    kind = native
    geom = None
    gap = 0.0
    if native == "plane":
        geom = adaptor.Plane()
    elif native == "cylinder":
        geom = adaptor.Cylinder()
    elif native == "cone":
        geom = adaptor.Cone()
    elif native == "sphere":
        geom = adaptor.Sphere()
    elif native == "torus":
        geom = adaptor.Torus()
    elif native in ("bspline", "revolution", "extrusion", "offset", "other"):
        k, g, gap = _canonical(face, options.canonical_tolerance)
        if k:
            kind, geom = k, g

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    area = abs(props.Mass())
    centroid = _pnt(props.CentreOfMass())
    box = Bnd_Box()
    BRepBndLib.Add_s(face, box, False)
    bbox = box.Get() if not box.IsVoid() else (0, 0, 0, 0, 0, 0)

    sample_point, sample_normal = _interior_sample(face)

    info = FaceInfo(
        index=index, face=face, kind=kind, native_kind=native,
        reversed=face.Orientation() == TopAbs_REVERSED, area=area, centroid=centroid,
        bbox=tuple(bbox), fit_gap=gap, sample_point=sample_point, sample_normal=sample_normal,
    )

    if kind == "plane" and geom is not None:
        info.origin = _pnt(geom.Location())
        # The plane's own axis may be left-handed: take the real outward normal.
        n = sample_normal or _pnt(geom.Axis().Direction())
        info.normal = V.unit(n)
    elif kind in REVOLVED and geom is not None:
        ax = geom.Axis() if kind != "sphere" else geom.Position().Axis()
        info.origin = _pnt(ax.Location()) if kind != "sphere" else _pnt(geom.Location())
        info.axis = V.unit(_pnt(ax.Direction()))
        if kind == "cylinder":
            info.radius = geom.Radius()
        elif kind == "cone":
            info.radius = geom.RefRadius()
            info.semi_angle = math.degrees(abs(geom.SemiAngle()))
        elif kind == "sphere":
            info.radius = geom.Radius()
        elif kind == "torus":
            info.radius = geom.MinorRadius()
            info.major_radius = geom.MajorRadius()
        pts = _boundary_points(face)
        if pts:
            radii = [V.point_line_distance(p, info.origin, info.axis) for p in pts]
            info.r_min, info.r_max = min(radii), max(radii)
            ax_pos = [V.project_on_axis(p, info.origin, info.axis) for p in pts]
            info.axial_min, info.axial_max = min(ax_pos), max(ax_pos)
            info.span = _arc_span(pts, info.origin, info.axis, sample_point)
        if sample_point is not None and sample_normal is not None:
            if kind == "sphere":
                r = V.sub(sample_point, info.origin)
            elif kind == "torus":
                rad = V.radial(sample_point, info.origin, info.axis)
                # The tube's centre circle lies in the torus plane (height 0).
                tube_centre = V.add(info.origin, V.mul(V.unit(rad), info.major_radius))
                r = V.sub(sample_point, tube_centre)
            else:
                r = V.radial(sample_point, info.origin, info.axis)
            info.internal = V.dot(sample_normal, r) < 0.0

    if kind in ("bspline", "revolution", "extrusion", "offset", "other"):
        try:
            radius, concave = _curvature_radius(face)
        except Exception:
            radius, concave = 0.0, None
        if radius > 0:
            info.curvature_radius = radius
            info.internal = concave

    # Loops
    outer = BRepTools.OuterWire_s(face)
    wx = TopExp_Explorer(face, TopAbs_WIRE)
    while wx.More():
        wire = TopoDS.Wire_s(wx.Current())
        ids = []
        ex = TopExp_Explorer(wire, TopAbs_EDGE)
        while ex.More():
            i = edge_index.index_of(TopoDS.Edge_s(ex.Current()))
            if i and i not in ids:
                ids.append(i)
            ex.Next()
        if not outer.IsNull() and wire.IsSame(outer):
            info.outer = ids
        else:
            info.inner.append(ids)
        wx.Next()
    if not info.outer and info.inner:
        info.outer = info.inner.pop(0)
    return info


# -- Edges --------------------------------------------------------------------------


def _edge_orientation_in(face: TopoDS_Face, edge: TopoDS_Edge):
    ex = TopExp_Explorer(face, TopAbs_EDGE)
    while ex.More():
        cur = ex.Current()
        if cur.IsSame(edge):
            return cur.Orientation()
        ex.Next()
    return None


def _uv_on(edge: TopoDS_Edge, face: TopoDS_Face, t: float, pcurve, point: gp_Pnt):
    if pcurve is not None:
        try:
            p = pcurve.Value(t)
            return p.X(), p.Y()
        except Exception:
            pass
    proj = GeomAPI_ProjectPointOnSurf(point, BRep_Tool.Surface_s(face))
    if proj.NbPoints() == 0:
        return None
    return proj.LowerDistanceParameters()


def _fit_curve(pts: list[V.Vec], tol: float):
    """("line",) or ("circle", centre, axis, radius) if all points fit, else None."""
    p0, p1 = pts[0], pts[-1]
    d = V.sub(p1, p0)
    if V.norm(d) > tol:
        u = V.unit(d)
        if all(V.point_line_distance(p, p0, u) <= tol for p in pts):
            return ("line",)
    # circle through three well spread points
    n = len(pts)
    a, b, c = pts[0], pts[n // 3], pts[(2 * n) // 3]
    ab, ac = V.sub(b, a), V.sub(c, a)
    normal = V.cross(ab, ac)
    nn = V.dot(normal, normal)
    if nn < 1e-18:
        return None
    centre = V.add(a, V.mul(V.add(V.mul(V.cross(normal, ab), V.dot(ac, ac)),
                                  V.mul(V.cross(ac, normal), V.dot(ab, ab))), 0.5 / nn))
    r = V.dist(centre, a)
    axis = V.unit(normal)
    for p in pts:
        if abs(V.dist(p, centre) - r) > tol or abs(V.dot(V.sub(p, centre), axis)) > tol:
            return None
    return ("circle", centre, axis, r)


def _curvature_radius(face: TopoDS_Face) -> tuple[float, Optional[bool]]:
    """(radius, concave?) when the face has one principal curvature that is
    the same everywhere (and the other smaller): a blend in disguise."""
    from OCP.BRepTopAdaptor import BRepTopAdaptor_FClass2d
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt2d

    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    surface = BRep_Tool.Surface_s(face)
    classifier = BRepTopAdaptor_FClass2d(face, 1e-7)
    sign = -1.0 if face.Orientation() == TopAbs_REVERSED else 1.0
    ks = []
    for fu in (0.2, 0.5, 0.8):
        for fv in (0.2, 0.5, 0.8):
            u, v = u0 + (u1 - u0) * fu, v0 + (v1 - v0) * fv
            if classifier.Perform(gp_Pnt2d(u, v)) != TopAbs_IN:
                continue
            props = GeomLProp_SLProps(surface, u, v, 2, 1e-9)
            if not props.IsCurvatureDefined():
                continue
            k1, k2 = sign * props.MaxCurvature(), sign * props.MinCurvature()
            big, small = (k1, k2) if abs(k1) >= abs(k2) else (k2, k1)
            if abs(big) < 1e-9 or abs(small) > 0.8 * abs(big):
                return 0.0, None
            ks.append(big)
    if len(ks) < 3:
        return 0.0, None
    if not (all(k > 0 for k in ks) or all(k < 0 for k in ks)):
        return 0.0, None
    mags = [abs(k) for k in ks]
    mean = sum(mags) / len(mags)
    if max(mags) - min(mags) > 0.05 * mean:
        return 0.0, None
    return 1.0 / mean, ks[0] > 0


def describe_edge(index: int, edge: TopoDS_Edge, faces: tuple[int, ...], topo_faces, options) -> EdgeInfo:
    curve = BRepAdaptor_Curve(edge)
    ckind = CURVE_KINDS.get(curve.GetType(), "other")
    props = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, props)
    info = EdgeInfo(index=index, edge=edge, faces=faces, curve=ckind, length=abs(props.Mass()))
    a, b = curve.FirstParameter(), curve.LastParameter()
    if BRep_Tool.Degenerated_s(edge):
        info.convexity = "degenerate"
        return info
    info.points = tuple(_pnt(curve.Value(a + (b - a) * k / 4.0)) for k in range(5))
    info.native_curve = ckind
    if ckind in ("bspline", "other", "offset"):
        dense = [_pnt(curve.Value(a + (b - a) * k / 16.0)) for k in range(17)]
        fitted = _fit_curve(dense, max(options.canonical_tolerance, 1e-7))
        if fitted is not None:
            ckind = info.curve = fitted[0]
            if fitted[0] == "circle":
                info.center, info.circle_axis, info.circle_radius = fitted[1], fitted[2], fitted[3]
    if ckind == "circle" and info.center is None:
        circ = curve.Circle()
        info.center = _pnt(circ.Location())
        info.circle_axis = V.unit(_pnt(circ.Axis().Direction()))
        info.circle_radius = circ.Radius()

    if len(faces) == 1:
        info.convexity = "free"
        return info
    if faces[0] == faces[1]:
        info.convexity = "seam"
        return info

    f1 = topo_faces[faces[0]].face
    f2 = topo_faces[faces[1]].face
    o1 = _edge_orientation_in(f1, edge)
    try:
        pc1 = BRepAdaptor_Curve2d(edge, f1)
    except Exception:
        pc1 = None
    try:
        pc2 = BRepAdaptor_Curve2d(edge, f2)
    except Exception:
        pc2 = None

    n = max(1, int(options.edge_samples))
    fractions = [0.5] if n == 1 else [0.05 + 0.9 * k / (n - 1) for k in range(n)]
    smooth = options.smooth_angle
    samples = []
    for fr in fractions:
        t = a + (b - a) * fr
        p = gp_Pnt()
        from OCP.gp import gp_Vec
        d1 = gp_Vec()
        curve.D1(t, p, d1)
        tangent = V.unit((d1.X(), d1.Y(), d1.Z()))
        if V.norm(tangent) == 0.0:
            continue
        if o1 == TopAbs_REVERSED:
            tangent = V.mul(tangent, -1.0)
        uv1 = _uv_on(edge, f1, t, pc1, p)
        uv2 = _uv_on(edge, f2, t, pc2, p)
        if uv1 is None or uv2 is None:
            continue
        n1 = surface_normal(f1, *uv1)
        n2 = surface_normal(f2, *uv2)
        if n1 is None or n2 is None:
            continue
        n1, n2 = V.unit(n1), V.unit(n2)
        turn = V.angle(n1, n2)
        sign = V.dot(V.cross(n1, n2), tangent)
        samples.append(turn if sign >= 0 else -turn)
    info.samples = tuple(samples)
    if not samples:
        info.convexity = "smooth"
        return info
    convex = [s for s in samples if s > smooth]
    concave = [s for s in samples if s < -smooth]
    if convex and concave:
        info.convexity = "variable"
        info.angle = sum(samples) / len(samples)
    elif convex:
        info.convexity = "convex"
        info.angle = sum(convex) / len(convex)
    elif concave:
        info.convexity = "concave"
        info.angle = sum(concave) / len(concave)
    else:
        info.convexity = "smooth"
        info.angle = sum(samples) / len(samples)
    return info


# -- Whole shape ----------------------------------------------------------------------


def build_topology(shape: TopoDS_Shape, options, check_abort: Optional[Callable[[], bool]] = None) -> Topology:
    face_index = FaceIndex(shape)
    edge_index = EdgeIndex(shape)
    faces: list[Optional[FaceInfo]] = [None]
    for i in range(1, len(face_index) + 1):
        if check_abort and check_abort():
            raise InterruptedError
        faces.append(describe_face(i, face_index.face_at(i), edge_index, options))

    ancestors = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, ancestors)
    edges: list[Optional[EdgeInfo]] = [None]
    for i in range(1, len(edge_index) + 1):
        if check_abort and check_abort():
            raise InterruptedError
        edge = edge_index.edge_at(i)
        owners: list[int] = []
        if ancestors.Contains(edge):
            for f in ancestors.FindFromKey(edge):
                owners.append(face_index.index_of(TopoDS.Face_s(f)))
        owners = [o for o in owners if o]
        if len(owners) > 2:  # non-manifold: keep the first two distinct
            seen = []
            for o in owners:
                if o not in seen:
                    seen.append(o)
            owners = seen[:2]
        edges.append(describe_edge(i, edge, tuple(owners), faces, options))

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box, False)
    if box.IsVoid():
        scale = 1.0
    else:
        x0, y0, z0, x1, y1, z1 = box.Get()
        scale = max(1e-6, math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2))
    return Topology(shape=shape, face_index=face_index, edge_index=edge_index,
                    faces=faces, edges=edges, scale=scale)
