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

import collections
from typing import Any, Optional, Callable

from scipy.spatial import KDTree
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face, TopoDS_Compound, topods
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_OUT
from OCC.Core.BRep import BRep_Builder, BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.gp import gp_Pnt, gp_Lin, gp_Vec
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape, BRepExtrema_IsInFace
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier

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
        samples = kwargs.get("samples", 10)

        intersector = IntCurvesFace_ShapeIntersector()
        intersector.Load(shape, 1e-6)

        classifier = BRepClass3d_SolidClassifier(shape)

        builder = BRep_Builder()
        face_compound = TopoDS_Compound()
        builder.MakeCompound(face_compound)

        face_explorer = TopExp_Explorer(shape, TopAbs_FACE)  # type: ignore
        while face_explorer.More():
            builder.Add(face_compound, face_explorer.Current())
            face_explorer.Next()

        dist_tool = BRepExtrema_DistShapeShape()
        dist_tool.LoadS2(face_compound)

        face_seeds = collections.defaultdict(list)

        results = {}
        for face in self.iter_faces(shape, progress_cb, check_abort):
            thicknesses = self._sphere_cast_for_face(
                face, intersector, dist_tool, classifier, samples, face_seeds
            )
            if thicknesses:
                results[face] = thicknesses
        return results

    def _sphere_cast_for_face(
        self,
        face: TopoDS_Face,
        intersector: IntCurvesFace_ShapeIntersector,
        dist_tool: BRepExtrema_DistShapeShape,
        classifier: BRepClass3d_SolidClassifier,
        samples: int,
        face_seeds: dict[TopoDS_Face, list[tuple[float, float, float]]],
    ) -> list[float]:
        thicknesses = []
        best_uv = (0.5, 0.5)
        max_t = -1.0

        u_ratio, v_ratio = get_face_uv_ratios(face)
        adaptor = BRepAdaptor_Surface(face)
        u_min, u_max = adaptor.FirstUParameter(), adaptor.LastUParameter()
        v_min, v_max = adaptor.FirstVParameter(), adaptor.LastVParameter()

        # Adaptive sampling based on face area
        props = GProp_GProps()
        brepgprop.SurfaceProperties(face, props)
        face_area = props.Mass()
        adaptive_samples = int(max(5, min(samples, 2 + (face_area**0.5) / 10)))

        # Inject seeds found by previous faces and build spatial index
        seeds = face_seeds.get(face, [])
        tree = KDTree([(s[0], s[1]) for s in seeds]) if seeds else None

        if seeds:
            for s_u, s_v, s_thick in seeds:
                thicknesses.append(s_thick)
                if s_thick > max_t:
                    max_t, best_uv = s_thick, (s_u, s_v)

        # Check the center
        u_mid, v_mid = get_face_uv_center(face)
        if is_point_on_face(u_mid, v_mid, face):
            mid_result = None
            skip = False
            if tree:
                dist, idx = tree.query((u_mid, v_mid))
                if dist < 1e-7:
                    mid_result = seeds[idx][2]
                    skip = True

            if not skip:
                mid_result = self._shrink_sphere_at_uv(
                    face, u_mid, v_mid, intersector, dist_tool, classifier, face_seeds
                )

            if mid_result is not None and mid_result != float("inf"):
                thicknesses.append(mid_result)
                if mid_result > max_t:
                    max_t, best_uv = mid_result, (u_mid, v_mid)

        # Coarse grid
        for u, v in yield_face_uv_grid(face, adaptive_samples, margin=0.01):
            thick = None
            skip = False
            if tree:
                dist, idx = tree.query((u, v))
                if dist < 1e-7:
                    thick = seeds[idx][2]
                    skip = True

            if not skip:
                thick = self._shrink_sphere_at_uv(
                    face, u, v, intersector, dist_tool, classifier, face_seeds, max_t
                )

            if thick is not None and thick != float("inf"):
                thicknesses.append(thick)
                if thick > max_t:
                    max_t, best_uv = thick, (u, v)

        # Iterative hill climb
        face_width_mm = (u_max - u_min) / u_ratio
        max_step_limit = 5.0
        step_size = max(0.1, min(face_width_mm * 0.02, max_step_limit))
        min_step = min(0.01, step_size * 0.001)

        plateau_patience = 3
        plateau_hits = 0
        gain_threshold = 0.0001

        current_best_uv, current_max_t = best_uv, max_t
        last_dir = None  # Stores the direction multiplier

        for _ in range(50):
            improved = False
            prev_t = current_max_t

            du = step_size * u_ratio
            dv = step_size * v_ratio

            cardinals = [(1, 0), (-1, 0), (0, 1), (0, -1)]
            diagonals = [(1, 1), (-1, 1), (1, -1), (-1, -1)]

            # Try the last successful direction first
            if last_dir:
                u_test = max(u_min, min(u_max, current_best_uv[0] + last_dir[0] * du))
                v_test = max(v_min, min(v_max, current_best_uv[1] + last_dir[1] * dv))

                thick = None
                skip = False
                if tree:
                    dist, idx = tree.query((u_test, v_test))
                    if dist < 1e-7:
                        thick = seeds[idx][2]
                        skip = True
                if not skip:
                    thick = self._shrink_sphere_at_uv(
                        face,
                        u_test,
                        v_test,
                        intersector,
                        dist_tool,
                        classifier,
                        face_seeds,
                        current_max_t,
                    )

                if thick is not None and thick > current_max_t and thick != float("inf"):
                    current_max_t, current_best_uv, improved = thick, (u_test, v_test), True
                    thicknesses.append(thick)

            # If momentum didn't work, try Cardinals
            if not improved:
                for d_u_m, d_v_m in cardinals:
                    u_test = max(u_min, min(u_max, current_best_uv[0] + d_u_m * du))
                    v_test = max(v_min, min(v_max, current_best_uv[1] + d_v_m * dv))

                    thick = None
                    skip = False
                    if tree:
                        dist, idx = tree.query((u_test, v_test))
                        if dist < 1e-7:
                            thick = seeds[idx][2]
                            skip = True
                    if not skip:
                        thick = self._shrink_sphere_at_uv(
                            face,
                            u_test,
                            v_test,
                            intersector,
                            dist_tool,
                            classifier,
                            face_seeds,
                            current_max_t,
                        )

                    if thick is not None and thick > current_max_t and thick != float("inf"):
                        current_max_t, current_best_uv, improved = thick, (u_test, v_test), True
                        last_dir = (d_u_m, d_v_m)
                        thicknesses.append(thick)
                        break  # Short-circuit

            # If cardinals didn't work, try Diagonals
            if not improved:
                for d_u_m, d_v_m in diagonals:
                    u_test = max(u_min, min(u_max, current_best_uv[0] + d_u_m * du))
                    v_test = max(v_min, min(v_max, current_best_uv[1] + d_v_m * dv))

                    thick = None
                    skip = False
                    if tree:
                        dist, idx = tree.query((u_test, v_test))
                        if dist < 1e-7:
                            thick = seeds[idx][2]
                            skip = True
                    if not skip:
                        thick = self._shrink_sphere_at_uv(
                            face,
                            u_test,
                            v_test,
                            intersector,
                            dist_tool,
                            classifier,
                            face_seeds,
                            current_max_t,
                        )

                    if thick is not None and thick > current_max_t and thick != float("inf"):
                        current_max_t, current_best_uv, improved = thick, (u_test, v_test), True
                        last_dir = (d_u_m, d_v_m)
                        thicknesses.append(thick)
                        break  # Short-circuit

            if improved:
                relative_gain = (current_max_t - prev_t) / max(prev_t, 1e-6)
                if relative_gain < gain_threshold:
                    plateau_hits += 1
                else:
                    plateau_hits = 0

                if plateau_hits >= plateau_patience:
                    break
            else:
                last_dir = None  # Reset direction
                step_size /= 2.0
                if step_size < min_step:
                    break

        return thicknesses

    def _shrink_sphere_at_uv(
        self,
        face: TopoDS_Face,
        u: float,
        v: float,
        intersector: IntCurvesFace_ShapeIntersector,
        dist_tool: BRepExtrema_DistShapeShape,
        classifier: BRepClass3d_SolidClassifier,
        face_seeds: dict[TopoDS_Face, list[tuple[float, float, float]]],
        min_to_beat: float = 0.0,
    ) -> Optional[float]:
        outward_norm = get_face_uv_normal(face, u, v)
        if not outward_norm:
            return None

        inward_norm = outward_norm.Reversed()

        epsilon = 1e-4
        p_exact = get_point_from_uv(face, inward_norm, u, v, 0.0)
        p_offset = get_point_from_uv(face, inward_norm, u, v, epsilon)

        classifier.Perform(p_offset, 1e-6)
        if classifier.State() == TopAbs_OUT:
            return None

        # Initial candidate sphere
        ray = gp_Lin(p_offset, inward_norm)
        intersector.Perform(ray, 0, float("inf"))

        if not intersector.IsDone() or intersector.NbPnt() == 0:
            return float("inf")

        min_dist = float("inf")
        for i in range(1, intersector.NbPnt() + 1):
            w_dist = intersector.WParameter(i)
            if w_dist > epsilon * 10 and w_dist < min_dist:
                min_dist = w_dist

        if min_dist == float("inf"):
            return float("inf")

        r = (min_dist - epsilon) / 2.0
        if (r * 2.0) <= min_to_beat:
            return r * 2.0

        # Shrinking loop
        for _ in range(10):
            center = gp_Pnt(
                p_exact.X() + r * inward_norm.X(),
                p_exact.Y() + r * inward_norm.Y(),
                p_exact.Z() + r * inward_norm.Z(),
            )

            vertex = BRepBuilderAPI_MakeVertex(center).Vertex()
            dist_tool.LoadS1(vertex)
            dist_tool.Perform()

            if not dist_tool.IsDone() or dist_tool.NbSolution() == 0:
                break

            d_min = dist_tool.Value()
            if d_min >= r - epsilon:
                break

            p_closest = dist_tool.PointOnShape2(1)
            best_d_sq = p_closest.SquareDistance(center)

            for i in range(2, dist_tool.NbSolution() + 1):
                p_cand = dist_tool.PointOnShape2(i)
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

            if r_new >= r or (r - r_new) < epsilon:
                break

            r = r_new

        thickness = r * 2.0
        final_center = gp_Pnt(
            p_exact.X() + r * inward_norm.X(),
            p_exact.Y() + r * inward_norm.Y(),
            p_exact.Z() + r * inward_norm.Z(),
        )

        if dist_tool.IsDone() and dist_tool.NbSolution() > 0:
            for i in range(1, dist_tool.NbSolution() + 1):
                p_contact = dist_tool.PointOnShape2(i)
                if abs(p_contact.Distance(final_center) - r) < epsilon * 10:
                    if dist_tool.SupportTypeShape2(i) == BRepExtrema_IsInFace:
                        support_shape = dist_tool.SupportOnShape2(i)
                        other_face = topods.Face(support_shape)

                        if not other_face.IsSame(face):
                            surf = BRep_Tool.Surface(other_face)
                            projector = GeomAPI_ProjectPointOnSurf(p_contact, surf)
                            if projector.IsDone() and projector.NbPoints() > 0:
                                u_other, v_other = projector.LowerDistanceParameters()
                                face_seeds[other_face].append((u_other, v_other, thickness))

        return thickness
