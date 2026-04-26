# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import collections
from typing import Any, Optional, Callable

from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape, BRepExtrema_IsInFace
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.BRepTopAdaptor import BRepTopAdaptor_FClass2d
from OCC.Core.gp import gp_Pnt, gp_Lin, gp_Vec, gp_Dir
from OCC.Core.GProp import GProp_GProps
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face, TopoDS_Compound, topods
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE

from ...core.base.base_analyzer import BaseAnalyzer
from ...core.registries import register_analyzer
from ...core.utils.geometry import (
    get_adaptive_sample_count,
    get_face_uv_center,
    get_face_uv_normal,
    get_point_from_uv,
    optimize_face_uv_search,
    is_point_on_face,
    yield_face_uv_grid,
)


@register_analyzer("SPHERE_THICKNESS_ANALYZER")
class SphereThicknessAnalyzer(BaseAnalyzer):
    @property
    def analysis_type(self) -> str:
        return "SPHERE_THICKNESS_ANALYZER"

    @property
    def name(self) -> str:
        return "Sphere Thickness Analyzer"

    def resolve_prefs(self, prefs: dict) -> None:
        self.min_samples = prefs.get("SphereMinSamples", 5)
        self.max_samples = prefs.get("SphereMaxSamples", 10)
        self.margin = prefs.get("SphereMargin", 0.01)
        self.enable_multithread = prefs.get("SphereMultiThreaded", False)
        self.max_shrink_iters = prefs.get("SphereMaxShrinkIters", 10)
        self.intersector_tol = prefs.get("SphereIntersectorTol", 1e-3)

    def execute(
        self,
        shape: TopoDS_Shape,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> dict[TopoDS_Face, list[float]]:
        self.resolve_prefs(kwargs.get("prefs", {}))
        self._setup_kernel_tools(shape)

        results = {}
        for face in self.iter_faces(shape, progress_cb, check_abort):
            thicknesses = self._analyze_face(face)
            if thicknesses:
                results[face] = thicknesses

        return results

    def _setup_kernel_tools(self, shape: TopoDS_Shape):
        """Initializes heavy OpenCascade tools once per shape."""
        self.intersector = IntCurvesFace_ShapeIntersector()
        self.intersector.Load(shape, self.intersector_tol)

        self.builder = BRep_Builder()
        face_compound = TopoDS_Compound()
        self.builder.MakeCompound(face_compound)

        face_explorer = TopExp_Explorer(shape, TopAbs_FACE)  # type: ignore
        while face_explorer.More():
            self.builder.Add(face_compound, face_explorer.Current())
            face_explorer.Next()

        self.dist_tool = BRepExtrema_DistShapeShape()
        self.dist_tool.SetMultiThread(self.enable_multithread)
        self.dist_tool.SetDeflection(1e-3)
        self.dist_tool.LoadS2(face_compound)

        self.shared_vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(0, 0, 0)).Vertex()
        self.face_seeds = collections.defaultdict(list)

    def _analyze_face(self, face: TopoDS_Face) -> list[float]:
        """Orchestrates sampling strategy and broad-phase caching for a face."""
        thicknesses = []
        best_uv = (0.5, 0.5)
        max_t = -1.0

        classifier = BRepTopAdaptor_FClass2d(face, 1e-6)

        props = GProp_GProps()
        brepgprop.SurfaceProperties(face, props)
        adaptive_samples = get_adaptive_sample_count(face, self.min_samples, self.max_samples)
        visited_uvs = {}

        def eval_thickness(test_u: float, test_v: float, min_to_beat: float) -> Optional[float]:
            key = (round(test_u, 5), round(test_v, 5))
            if key in visited_uvs:
                return visited_uvs[key]

            t = self._calculate_sphere_thickness(face, test_u, test_v, min_to_beat)
            visited_uvs[key] = t
            return t

        # Inject seeds from neighbors
        for s_u, s_v, s_thick in self.face_seeds.get(face, []):
            visited_uvs[(round(s_u, 5), round(s_v, 5))] = s_thick
            thicknesses.append(s_thick)
            if s_thick > max_t:
                max_t, best_uv = s_thick, (s_u, s_v)

        # Check center
        u_mid, v_mid = get_face_uv_center(face)
        if is_point_on_face(u_mid, v_mid, face, classifier):
            t = eval_thickness(u_mid, v_mid, max_t)
            if t is not None and t != float("inf"):
                thicknesses.append(t)
                if t > max_t:
                    max_t, best_uv = t, (u_mid, v_mid)

        # Check coarse grid
        for u, v in yield_face_uv_grid(face, adaptive_samples, margin=self.margin):
            t = eval_thickness(u, v, max_t)
            if t is not None and t != float("inf"):
                thicknesses.append(t)
                if t > max_t:
                    max_t, best_uv = t, (u, v)

        # Iterative hill climb
        best_uv, max_t, climb_results = optimize_face_uv_search(
            face=face,
            start_uv=best_uv,
            start_val=max_t,
            eval_func=eval_thickness,
            classifier=classifier,
        )
        thicknesses.extend(climb_results)

        return thicknesses

    def _calculate_sphere_thickness(
        self, face: TopoDS_Face, u: float, v: float, min_to_beat: float
    ) -> Optional[float]:
        """Calculates exact thickness at a point in 2 phases: Raycast & Extrema."""
        outward_norm = get_face_uv_normal(face, u, v)
        if not outward_norm:
            return None

        inward_norm = outward_norm.Reversed()
        p_exact = get_point_from_uv(face, inward_norm, u, v, 0.0)

        # Bounding raycast
        r_init = self._raycast_max_radius(p_exact, inward_norm)
        if r_init == float("inf"):
            return float("inf")

        if (r_init * 2.0) <= min_to_beat:
            return r_init * 2.0

        # Extrema shrink loop
        return self._shrink_to_fit(face, p_exact, inward_norm, r_init, min_to_beat)

    def _raycast_max_radius(
        self, p_exact: gp_Pnt, inward_norm: gp_Dir, epsilon: float = 1e-4
    ) -> float:
        """Fires a ray to find the opposite side of the solid, returning initial max radius."""
        p_offset = gp_Pnt(
            p_exact.X() + inward_norm.X() * epsilon,
            p_exact.Y() + inward_norm.Y() * epsilon,
            p_exact.Z() + inward_norm.Z() * epsilon,
        )

        self.intersector.Perform(gp_Lin(p_offset, inward_norm), 0, float("inf"))

        if not self.intersector.IsDone() or self.intersector.NbPnt() == 0:
            return float("inf")

        min_dist = float("inf")
        for i in range(1, self.intersector.NbPnt() + 1):
            w_dist = self.intersector.WParameter(i)
            if w_dist > epsilon * 10 and w_dist < min_dist:
                min_dist = w_dist

        if min_dist == float("inf"):
            return float("inf")

        return (min_dist - epsilon) / 2.0

    def _shrink_to_fit(
        self,
        origin_face: TopoDS_Face,
        p_exact: gp_Pnt,
        inward_norm: gp_Dir,
        r_init: float,
        min_to_beat: float,
    ) -> float:
        """Iteratively shrinks the sphere radius."""
        r = r_init
        epsilon = 1e-4

        for _ in range(self.max_shrink_iters):
            center = gp_Pnt(
                p_exact.X() + r * inward_norm.X(),
                p_exact.Y() + r * inward_norm.Y(),
                p_exact.Z() + r * inward_norm.Z(),
            )

            self.builder.UpdateVertex(self.shared_vertex, center, 1e-6)
            self.dist_tool.LoadS1(self.shared_vertex)
            self.dist_tool.Perform()

            if not self.dist_tool.IsDone() or self.dist_tool.NbSolution() == 0:
                break
            if self.dist_tool.InnerSolution():
                break

            d_min = self.dist_tool.Value()
            if d_min >= r - epsilon:
                break

            p_closest = self.dist_tool.PointOnShape2(1)
            best_d_sq = p_closest.SquareDistance(center)

            for i in range(2, self.dist_tool.NbSolution() + 1):
                p_cand = self.dist_tool.PointOnShape2(i)
                d_sq = p_cand.SquareDistance(center)
                if d_sq < best_d_sq:
                    best_d_sq, p_closest = d_sq, p_cand

            v_vec = gp_Vec(p_exact, p_closest)
            v_sq = v_vec.SquareMagnitude()
            v_dot_n = v_vec.Dot(gp_Vec(inward_norm))

            if v_sq < epsilon**2:
                break
            if v_dot_n <= 0:
                return float("inf")

            r_new = v_sq / (2.0 * v_dot_n)

            if (r_new * 2.0) <= min_to_beat:
                return -1.0

            if r_new >= r or (r - r_new) < epsilon:
                break
            r = r_new

        thickness = r * 2.0

        # Inject seed points to touching faces
        if self.dist_tool.IsDone() and self.dist_tool.NbSolution() > 0:
            final_c = gp_Pnt(
                p_exact.X() + r * inward_norm.X(),
                p_exact.Y() + r * inward_norm.Y(),
                p_exact.Z() + r * inward_norm.Z(),
            )
            for i in range(1, self.dist_tool.NbSolution() + 1):
                if abs(self.dist_tool.PointOnShape2(i).Distance(final_c) - r) < epsilon * 10:
                    if self.dist_tool.SupportTypeShape2(i) == BRepExtrema_IsInFace:
                        other_face = topods.Face(self.dist_tool.SupportOnShape2(i))
                        if not other_face.IsSame(origin_face):
                            u_other, v_other = self.dist_tool.ParOnFaceS2(i)
                            self.face_seeds[other_face].append((u_other, v_other, thickness))

        return thickness
