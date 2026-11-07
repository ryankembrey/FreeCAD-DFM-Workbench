# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import collections
from typing import Any, Optional, Callable
import math

from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS, TopoDS_Shape, TopoDS_Face
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
from OCP.gp import gp_Lin
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from freecad.DFM.core.utils.geometry import EdgeIndex, FaceIndex

from ...core.base.base_analyzer import BaseAnalyzer
from ...core.registries import register_analyzer
from ...core.utils import (
    get_adaptive_sample_count,
    get_face_uv_center,
    get_face_uv_normal,
    yield_face_uv_grid,
    get_point_from_uv,
)


# Two hits this close together (mm), on faces that share an edge, are one
# crossing: a ray through an edge is reported once for each face. Counted
# twice, the pair reads as a wall of almost 0 mm and throws off the in/out
# pairing for the rest of the ray. Real thin walls are between opposite
# faces, which don't share an edge.
EDGE_CROSSING_GAP = 0.02


@register_analyzer("RAY_THICKNESS_ANALYZER")
class RayThicknessAnalyzer(BaseAnalyzer):
    parallel = True
    relative_cost = 12  # ~ms per face (see BaseAnalyzer.relative_cost)

    @property
    def analysis_type(self) -> str:
        return "RAY_THICKNESS_ANALYZER"

    @property
    def name(self) -> str:
        return "Ray Thickness Analyzer"

    def resolve_prefs(self, prefs: dict) -> None:
        self.min_samples = prefs.get("RayMinSamples", 5)
        self.max_samples = prefs.get("RayMaxSamples", 10)
        self.margin = prefs.get("RayMargin", 0.0)
        self.intersector_tol = prefs.get("RayIntersectorTol", 1e-3)
        cone_deg = prefs.get("RayNormalCone", 5.0)
        self.normal_cone_cos = math.cos(math.radians(180.0 - cone_deg))

    def prepare(
        self,
        shape: TopoDS_Shape,
        face_index: FaceIndex,
        edge_index: EdgeIndex,
        **kwargs: Any,
    ) -> None:
        super().prepare(shape, face_index, edge_index, **kwargs)
        self.face_index = face_index

        self.intersector = IntCurvesFace_ShapeIntersector()
        self.intersector.Load(shape, self.intersector_tol)
        self._shared_edges = self._build_shared_edges(shape, face_index)

    def analyze(self, face: TopoDS_Face) -> Optional[list[float]]:
        """The wall thicknesses measured by rays from one face.

        A ray that crosses a wall also measures the faces it enters and exits
        through: those values are contributed to them (see BaseAnalyzer.merge()),
        so every face gets its own rays' values and every other ray's through it.
        """
        return self._ray_cast_for_face(face) or None

    @staticmethod
    def _build_shared_edges(shape: TopoDS_Shape, face_index: FaceIndex) -> dict[tuple[int, int], list]:
        """{(face, neighbour): [edges they share]} for every pair of faces meeting at an edge.

        Used to recognise one ray crossing an edge, reported once per face.
        """
        edge_faces = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, edge_faces)
        shared: dict[tuple[int, int], list] = collections.defaultdict(list)
        for i in range(1, edge_faces.Extent() + 1):
            edge = edge_faces.FindKey(i)
            faces = sorted({face_index.index_of(TopoDS.Face_s(f)) for f in edge_faces.FindFromIndex(i)})
            for a in faces:
                for b in faces:
                    if a != b:
                        shared[(a, b)].append(edge)
        return shared

    def _merge_edge_crossings(self, hits: list) -> list:
        """Count a ray through an edge once, not once per face that meets there."""
        merged: list = []
        for hit in hits:
            if merged:
                w0, face0 = merged[-1][0], merged[-1][1]
                if hit[0] - w0 < EDGE_CROSSING_GAP and (
                    hit[1].IsSame(face0)
                    or (self.face_index.index_of(face0), self.face_index.index_of(hit[1]))
                    in self._shared_edges
                ):
                    continue
            merged.append(hit)
        return merged

    def _ray_cast_for_face(self, face: TopoDS_Face) -> list[float]:
        """
        Ray cast the given face and return a list of thickness values.
        """
        adaptive_samples = get_adaptive_sample_count(face, self.min_samples, self.max_samples)
        thicknesses: list[float] = []

        _R = 3  # round value
        visited_uvs: dict[tuple[float, float], float] = {}

        # Always sample the face centre
        u_mid, v_mid = get_face_uv_center(face)
        key_mid = (round(u_mid, _R), round(v_mid, _R))
        if key_mid not in visited_uvs:
            for t in self.ray_cast_at_uv(face, u_mid, v_mid):
                if t > 0:
                    thicknesses.append(t)
                    visited_uvs[key_mid] = t

        for u, v in yield_face_uv_grid(face, adaptive_samples, margin=self.margin):
            key = (round(u, _R), round(v, _R))
            if key in visited_uvs:
                continue
            for t in self.ray_cast_at_uv(face, u, v):
                if t > 0:
                    thicknesses.append(t)
            visited_uvs[key] = 0.0

        return thicknesses

    def ray_cast_at_uv(
        self,
        face: TopoDS_Face,
        u: float,
        v: float,
    ) -> list[float]:
        """
        Returns all wall thicknesses measured along the inward ray from (u, v).

        A hit is only used for seeding if the ray is within _NORMAL_CONE_DEG of
        normal to the hit face, ensuring seeds land at meaningful UV locations.
        """
        outward_norm = get_face_uv_normal(face, u, v)
        if not outward_norm:
            return []

        inward_norm = outward_norm.Reversed()
        epsilon = 1e-4
        point = get_point_from_uv(face, inward_norm, u, v, epsilon)
        ray = gp_Lin(point, inward_norm)

        self.intersector.Perform(ray, -1e30, 1e30)

        if not self.intersector.IsDone() or self.intersector.NbPnt() == 0:
            return []

        # Collect and sort all valid forward hits by W parameter
        hits: list[tuple[float, TopoDS_Face, float, float]] = []
        for i in range(1, self.intersector.NbPnt() + 1):
            w = self.intersector.WParameter(i)
            if w > epsilon * 10:
                hits.append(
                    (
                        w,
                        self.intersector.Face(i),
                        self.intersector.UParameter(i),
                        self.intersector.VParameter(i),
                    )
                )
        hits.sort(key=lambda h: h[0])
        hits = self._merge_edge_crossings(hits)

        if not hits:
            return []

        thicknesses: list[float] = []

        exit_w, exit_face, exit_u, exit_v = hits[0]
        exit_normal = get_face_uv_normal(exit_face, exit_u, exit_v)

        if exit_normal:
            ray_dot_exit = outward_norm.Dot(exit_normal)

            if ray_dot_exit < self.normal_cone_cos:
                thicknesses.append(exit_w)

                if not exit_face.IsSame(face):
                    self.contribute(("Face", self.face_index.index_of(exit_face)), exit_w)

        i = 1
        while i + 1 < len(hits):
            entry_w, entry_face, entry_u, entry_v = hits[i]
            exit_w, exit_face, exit_u, exit_v = hits[i + 1]

            wall_thickness = exit_w - entry_w

            if wall_thickness <= epsilon:
                i += 2
                continue

            entry_normal = get_face_uv_normal(entry_face, entry_u, entry_v)
            exit_normal = get_face_uv_normal(exit_face, exit_u, exit_v)

            # At least one face must be roughly normal to the ray for a valid wall.
            entry_ok = (
                entry_normal is not None and outward_norm.Dot(entry_normal) > -self.normal_cone_cos
            )
            exit_ok = (
                exit_normal is not None and outward_norm.Dot(exit_normal) < self.normal_cone_cos
            )

            if entry_ok or exit_ok:
                thicknesses.append(wall_thickness)

                if entry_ok and not entry_face.IsSame(face):
                    self.contribute(("Face", self.face_index.index_of(entry_face)), wall_thickness)

                if exit_ok and not exit_face.IsSame(face):
                    self.contribute(("Face", self.face_index.index_of(exit_face)), wall_thickness)

            i += 2

        return thicknesses
