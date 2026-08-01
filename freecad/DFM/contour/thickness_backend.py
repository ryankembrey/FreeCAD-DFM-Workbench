# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


_EPS = 1e-4
_INF = float("inf")


def _add(p, d, s):
    return (p[0] + d[0] * s, p[1] + d[1] * s, p[2] + d[2] * s)


class RayThickness:
    """Wall thickness as the distance between the entry and exit of the solid."""

    def __init__(self, ocp_shape, tol=1e-3):
        from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

        self._it = IntCurvesFace_ShapeIntersector()
        self._it.Load(ocp_shape, tol)

    def _forward_hits(self, origin, direction):
        from OCP.gp import gp_Pnt, gp_Lin, gp_Dir

        ray = gp_Lin(gp_Pnt(*origin), gp_Dir(*direction))
        self._it.Perform(ray, -1.0e30, 1.0e30)
        if not self._it.IsDone() or self._it.NbPnt() == 0:
            return []
        ws = [self._it.WParameter(i) for i in range(1, self._it.NbPnt() + 1)]
        return sorted(w for w in ws if w > _EPS)

    def at(self, point, outward, margin):
        inward = (-outward[0], -outward[1], -outward[2])
        origin = _add(point, outward, margin)
        ws = self._forward_hits(origin, inward)
        if len(ws) < 2:
            return 0.0
        return ws[1] - ws[0]  # exit minus entry


class SphereThickness:
    """Diameter of the largest sphere that fits inward from the true surface."""

    def __init__(self, ocp_shape, tol=1e-3, max_iters=12):
        from OCP.BRep import BRep_Builder
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
        from OCP.BRepExtrema import BRepExtrema_DistShapeShape
        from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
        from OCP.TopoDS import TopoDS_Compound
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopAbs import TopAbs_FACE
        from OCP.gp import gp_Pnt

        self._it = IntCurvesFace_ShapeIntersector()
        self._it.Load(ocp_shape, tol)

        self._builder = BRep_Builder()
        compound = TopoDS_Compound()
        self._builder.MakeCompound(compound)
        explorer = TopExp_Explorer(ocp_shape, TopAbs_FACE)
        while explorer.More():
            self._builder.Add(compound, explorer.Current())
            explorer.Next()

        self._dist = BRepExtrema_DistShapeShape()
        self._dist.SetMultiThread(False)
        self._dist.SetDeflection(1e-3)
        self._dist.LoadS2(compound)

        self._vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(0, 0, 0)).Vertex()
        self._max_iters = max_iters

    def _first_forward(self, origin, direction):
        from OCP.gp import gp_Pnt, gp_Lin, gp_Dir

        ray = gp_Lin(gp_Pnt(*origin), gp_Dir(*direction))
        self._it.Perform(ray, -1.0e30, 1.0e30)
        if not self._it.IsDone() or self._it.NbPnt() == 0:
            return None
        best = _INF
        for i in range(1, self._it.NbPnt() + 1):
            w = self._it.WParameter(i)
            if _EPS < w < best:
                best = w
        return None if best == _INF else best

    def at(self, point, outward, margin):
        from OCP.gp import gp_Pnt, gp_Vec

        inward = (-outward[0], -outward[1], -outward[2])
        origin = _add(point, outward, margin)

        # Ground the measurement on the true surface (first entry into the solid).
        w_entry = self._first_forward(origin, inward)
        if w_entry is None:
            return 0.0
        surface = _add(origin, inward, w_entry)

        # Initial radius from a ray to the far wall.
        w_far = self._first_forward(_add(surface, inward, _EPS), inward)
        if w_far is None:
            return 0.0
        r = (w_far - _EPS) / 2.0
        if r <= 0.0:
            return 0.0

        p_exact = gp_Pnt(*surface)
        for _ in range(self._max_iters):
            center = gp_Pnt(*_add(surface, inward, r))
            self._builder.UpdateVertex(self._vertex, center, 1e-6)
            self._dist.LoadS1(self._vertex)
            self._dist.Perform()
            if not self._dist.IsDone() or self._dist.NbSolution() == 0:
                break
            if self._dist.InnerSolution():
                break
            if self._dist.Value() >= r - _EPS:
                break

            closest = self._dist.PointOnShape2(1)
            best = closest.SquareDistance(center)
            for i in range(2, self._dist.NbSolution() + 1):
                x
                d2 = cand.SquareDistance(center)
                if d2 < best:
                    best, closest = d2, cand

            v = gp_Vec(p_exact, closest)
            v_sq = v.SquareMagnitude()
            v_dot = v.Dot(gp_Vec(inward[0], inward[1], inward[2]))
            if v_sq < _EPS * _EPS:
                break
            if v_dot <= 0:
                return 0.0
            r_new = v_sq / (2.0 * v_dot)
            if r_new >= r or (r - r_new) < _EPS:
                break
            r = r_new

        return r * 2.0


def make_backend(method, ocp_shape):
    m = str(method).lower()
    if "shrink" in m or "sphere" in m:
        return SphereThickness(ocp_shape)
    return RayThickness(ocp_shape)
