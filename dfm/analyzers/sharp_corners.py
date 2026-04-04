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

from typing import Any, Callable, Optional
import math

from OCC.Core.TopoDS import TopoDS_Edge, TopoDS_Face, TopoDS_Shape, topods
from OCC.Core.TopExp import topexp
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCC.Core.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRep import BRep_Tool
from OCC.Core.GeomLProp import GeomLProp_CLProps
from OCC.Core.TopAbs import TopAbs_REVERSED
from OCC.Core.TopExp import TopExp_Explorer


from OCC.Core.gp import gp_Dir
from dfm.core.base_analyzer import BaseAnalyzer
from dfm.models import ProcessRequirement
from dfm.registries.analyzers_registry import register_analyzer
from dfm.utils.geometry import get_face_uv_normal


@register_analyzer("SHARP_CORNER_ANALYZER")
class SharpCornersAnalyzer(BaseAnalyzer):
    @property
    def name(self) -> str:
        return "Sharp Corner Analyzer"

    @property
    def analysis_type(self) -> str:
        return "SHARP_CORNER_ANALYZER"

    @property
    def requirements(self) -> set[ProcessRequirement]:
        return set()

    def execute(
        self,
        shape: TopoDS_Shape,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> dict[TopoDS_Edge, tuple[float, bool]]:
        edge_face_map = TopTools_IndexedDataMapOfShapeListOfShape()
        topexp.MapShapesAndAncestors(
            shape,
            TopAbs_EDGE,  # type: ignore
            TopAbs_FACE,  # type: ignore
            edge_face_map,
        )

        results: dict[TopoDS_Edge, tuple[float, bool]] = {}

        for edge in self.iter_edges(shape, progress_cb, check_abort):
            result = self._analyze_edge(edge, edge_face_map)
            if result is not None:
                results[edge] = result

        return results

    def _analyze_edge(
        self,
        edge: TopoDS_Edge,
        edge_face_map: TopTools_IndexedDataMapOfShapeListOfShape,
    ) -> Optional[tuple[float, bool]]:
        if not edge_face_map.Contains(edge):
            return None

        adjacent_faces = edge_face_map.FindFromKey(edge)
        if adjacent_faces.Size() != 2:
            return None

        if BRep_Tool.Degenerated(edge):
            return None

        face1 = topods.Face(adjacent_faces.First())
        face2 = topods.Face(adjacent_faces.Last())

        try:
            first, last = BRep_Tool.Range(edge)
            mid_param = (first + last) / 2.0
        except Exception:
            return None

        n1 = self._get_normal_at_edge(face1, edge, mid_param)
        n2 = self._get_normal_at_edge(face2, edge, mid_param)

        if n1 is None or n2 is None:
            return None

        angle_deg = math.degrees(n1.Angle(n2))

        concave = self.is_concave_edge(edge, face1, face2)
        if concave is None:
            return None

        return angle_deg, concave

    def _get_normal_at_edge(
        self,
        face,
        edge: TopoDS_Edge,
        param: float,
    ) -> Optional[Any]:
        """
        Returns the face normal at the point where the edge midpoint projects
        onto the face's UV space.
        """
        try:
            curve_on_surf = BRep_Tool.CurveOnSurface(edge, face)
            if not curve_on_surf or curve_on_surf[0] is None:
                return None

            pcurve = curve_on_surf[0]
            uv = pcurve.Value(param)

            return get_face_uv_normal(face, uv.X(), uv.Y())

        except Exception:
            return None

    def is_concave_edge(
        self, edge: TopoDS_Edge, face1: TopoDS_Face, face2: TopoDS_Face
    ) -> Optional[bool]:
        """Determines if a shared edge is concave (internal corner) using cross products."""
        try:
            curve_handle, first, last = BRep_Tool.Curve(edge)
            mid_param = (first + last) / 2.0

            n1 = self._get_normal_at_edge(face1, edge, mid_param)
            n2 = self._get_normal_at_edge(face2, edge, mid_param)
            if not n1 or not n2:
                return None

            if n1.IsParallel(n2, 1e-4):
                return False

            props = GeomLProp_CLProps(curve_handle, 1, 1e-6)
            props.SetParameter(mid_param)
            if not props.IsTangentDefined():
                return None

            tangent = gp_Dir(1, 0, 0)
            props.Tangent(tangent)

            exp = TopExp_Explorer(face1, TopAbs_EDGE)  # type: ignore
            edge_in_face1 = None
            while exp.More():
                e = topods.Edge(exp.Current())
                if e.IsSame(edge):
                    edge_in_face1 = e
                    break
                exp.Next()

            if edge_in_face1 and edge_in_face1.Orientation() == TopAbs_REVERSED:
                tangent.Reverse()

            cross_norm = n1.Crossed(n2)
            return cross_norm.Dot(tangent) < 0.0

        except Exception:
            return None
