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


from typing import Any, Optional, Callable

from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face, topods, TopoDS_Compound
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.BRep import BRep_Builder, BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.gp import gp_Pnt, gp_Lin, gp_Vec
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeVertex

from dfm.core.base_analyzer import BaseAnalyzer
from dfm.registries import register_analyzer
from dfm.utils import get_face_uv_normal, yield_face_uv_grid, get_point_from_uv, get_face_uv_center
from dfm.utils.geometry import get_face_uv_ratios, is_point_on_face


@register_analyzer("SPHERE_THICKNESS_ANALYZER")
class SphereThicknessAnalyzer(BaseAnalyzer):
    @property
    def analysis_type(self) -> str:
        return "SPHERE_THICKNESS_ANALYZER"

    @property
    def name(self) -> str:
        return "Sphere Thickness Analyzer"

    def execute(
        self,
        shape: TopoDS_Shape,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> dict[TopoDS_Face, list[float]]:
        samples = kwargs.get("samples", 25)
        results: dict[TopoDS_Face, list[float]] = {}

        intersector = IntCurvesFace_ShapeIntersector()
        intersector.Load(shape, 1e-6)

        builder = BRep_Builder()
        face_compound = TopoDS_Compound()
        builder.MakeCompound(face_compound)

        face_explorer = TopExp_Explorer(shape, TopAbs_FACE)  # type: ignore
        while face_explorer.More():
            builder.Add(face_compound, face_explorer.Current())
            face_explorer.Next()

        dist_tool = BRepExtrema_DistShapeShape()
        dist_tool.LoadS2(face_compound)

        face_explorer = TopExp_Explorer(shape, TopAbs_FACE)  # type: ignore
        faces_processed = 0

        while face_explorer.More():
            if check_abort and check_abort():
                return results

            current_face = topods.Face(face_explorer.Current())

            thicknesses = self._sphere_cast_for_face(current_face, intersector, dist_tool, samples)
            if thicknesses:
                results[current_face] = thicknesses

            face_explorer.Next()

            faces_processed += 1

            if progress_cb:
                progress_cb(faces_processed)
        return results

    def _sphere_cast_for_face(
        self,
        face: TopoDS_Face,
        intersector: IntCurvesFace_ShapeIntersector,
        dist_tool: BRepExtrema_DistShapeShape,
        samples: int,
    ) -> list[float]:
        thicknesses = []
        best_uv = (0.5, 0.5)
        max_t = -1.0

        u_ratio, v_ratio = get_face_uv_ratios(face)
        adaptor = BRepAdaptor_Surface(face)
        u_min, u_max = adaptor.FirstUParameter(), adaptor.LastUParameter()
        v_min, v_max = adaptor.FirstVParameter(), adaptor.LastVParameter()

        # Check the center
        u_mid, v_mid = get_face_uv_center(face)
        if is_point_on_face(u_mid, v_mid, face):
            mid_result = self._shrink_sphere_at_uv(face, u_mid, v_mid, intersector, dist_tool)
            if mid_result is not None and mid_result != float("inf"):
                thicknesses.append(mid_result)
                max_t = mid_result
                best_uv = (u_mid, v_mid)

        # Coarse grid
        for u, v in yield_face_uv_grid(face, samples, margin=0.05):
            thick = self._shrink_sphere_at_uv(face, u, v, intersector, dist_tool)
            if thick is not None and thick != float("inf"):
                thicknesses.append(thick)
                if thick > max_t:
                    max_t = thick
                    best_uv = (u, v)

        # Iterative hill climb
        step_size = 0.05
        min_step = 0.001
        max_hill_iters = 16

        current_best_uv = best_uv
        current_max_t = max_t

        for _ in range(max_hill_iters):
            improved = False

            du = step_size * u_ratio
            dv = step_size * v_ratio

            neighbors = [
                (current_best_uv[0] + du, current_best_uv[1]),
                (current_best_uv[0] - du, current_best_uv[1]),
                (current_best_uv[0], current_best_uv[1] + dv),
                (current_best_uv[0], current_best_uv[1] - dv),
                (current_best_uv[0] + du, current_best_uv[1] + dv),  # diagonals
                (current_best_uv[0] - du, current_best_uv[1] + dv),
                (current_best_uv[0] + du, current_best_uv[1] - dv),
                (current_best_uv[0] - du, current_best_uv[1] - dv),
            ]
            for u_test, v_test in neighbors:
                # Keep UV coordinates within valid [0, 1] parametric space bounds
                u_test = max(u_min, min(u_max, u_test))
                v_test = max(v_min, min(v_max, v_test))

                thick = self._shrink_sphere_at_uv(face, u_test, v_test, intersector, dist_tool)
                if thick is not None and thick != float("inf"):
                    thicknesses.append(thick)

                    if thick > current_max_t:
                        current_max_t = thick
                        current_best_uv = (u_test, v_test)
                        improved = True

            # If no neighbor is thicker, shrink the step size to dial in the peak
            if not improved:
                step_size /= 2.0
                if step_size < min_step:
                    break  # Found the local peak

        return thicknesses

    def _shrink_sphere_at_uv(
        self,
        face: TopoDS_Face,
        u: float,
        v: float,
        intersector: IntCurvesFace_ShapeIntersector,
        dist_tool: BRepExtrema_DistShapeShape,
    ) -> Optional[float]:
        outward_norm = get_face_uv_normal(face, u, v)
        if not outward_norm:
            return None

        inward_norm = outward_norm.Reversed()

        epsilon = 1e-4
        p_exact = get_point_from_uv(face, inward_norm, u, v, 0.0)
        p_offset = get_point_from_uv(face, inward_norm, u, v, epsilon)

        # Initial candidate sphere
        ray = gp_Lin(p_offset, inward_norm)
        intersector.Perform(ray, 0, float("inf"))

        if not intersector.IsDone() or intersector.NbPnt() == 0:
            return float("inf")

        min_dist = float("inf")
        for i in range(1, intersector.NbPnt() + 1):
            w_dist = intersector.WParameter(i)
            if w_dist > epsilon * 10 and w_dist < min_dist:  # skip self-intersection
                min_dist = w_dist

        if min_dist == float("inf"):
            return float("inf")

        r = (min_dist - epsilon) / 2.0

        # Shrinking loop
        max_iters = 10

        for _ in range(max_iters):
            center = gp_Pnt(
                p_exact.X() + r * inward_norm.X(),
                p_exact.Y() + r * inward_norm.Y(),
                p_exact.Z() + r * inward_norm.Z(),
            )

            vertex = BRepBuilderAPI_MakeVertex(center).Vertex()
            dist_tool.LoadS1(vertex)
            dist_tool.Perform()

            if not dist_tool.IsDone():
                break

            d_min = dist_tool.Value()

            # Condition A: Sphere is fully inscribed without hitting anything else
            if d_min >= r - epsilon:
                break

            # Condition B: Something is inside the sphere. Shrink it.
            # Find the closest point by computing distances manually across all solutions
            p_closest = dist_tool.PointOnShape2(1)
            best_dist = float("inf")
            for i in range(1, dist_tool.NbSolution() + 1):
                p_candidate = dist_tool.PointOnShape2(i)
                center_x = p_exact.X() + r * inward_norm.X()
                center_y = p_exact.Y() + r * inward_norm.Y()
                center_z = p_exact.Z() + r * inward_norm.Z()
                dx = p_candidate.X() - center_x
                dy = p_candidate.Y() - center_y
                dz = p_candidate.Z() - center_z
                dist = (dx * dx + dy * dy + dz * dz) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    p_closest = p_candidate

            v_vec = gp_Vec(p_exact, p_closest)
            v_sq = v_vec.SquareMagnitude()

            if v_sq < epsilon**2:
                break

            v_dot_n = v_vec.Dot(gp_Vec(inward_norm))

            if v_dot_n <= 0:
                break

            r_new = v_sq / (2.0 * v_dot_n)

            if r_new >= r or (r - r_new) < epsilon:
                break

            r = r_new

        return r * 2.0
