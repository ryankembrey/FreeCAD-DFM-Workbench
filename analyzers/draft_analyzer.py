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

import math
from typing import Any

import FreeCAD

from OCC.Core.Geom import Geom_Surface
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face, topods, TopoDS_Solid
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.gp import gp_Dir, gp_Pnt, gp_Vec
from OCC.Core.GeomAbs import GeomAbs_Plane
from OCC.Core.BRepTools import breptools
from OCC.Core.BRep import BRep_Tool
from OCC.Core.GeomLProp import GeomLProp_SLProps

from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.StlAPI import StlAPI_Writer

from analyzers import BaseAnalyzer


class DraftAnalyzer(BaseAnalyzer):
    """
    Analyzes the minimum draft angle for all faces of a shape.
    """

    @property
    def analysis_type(self) -> str:
        return "DRAFT_ANALYZER"

    @property
    def name(self) -> str:
        return "Draft Analyzer"

    def execute(self, shape: TopoDS_Shape, **kwargs: Any) -> dict[TopoDS_Face, float]:
        """
        Runs a draft analysis on the inputted TopoDS_Shape.
        """
        pull_direction = kwargs.get("pull_direction", gp_Dir(0, 0, 1))
        samples = kwargs.get("samples", 20)  # Used by UV method

        if not isinstance(pull_direction, gp_Dir):
            raise ValueError(f"{self.name} requires a 'pull_direction' of type gp_Dir.")

        face_explorer = TopExp_Explorer(shape, TopAbs_FACE)
        results: dict[TopoDS_Face, float] = {}

        while face_explorer.More():
            current_face = topods.Face(face_explorer.Current())
            # The parent_solid is needed if we re-introduce verification later
            draft_result = self.get_draft_for_face(current_face, pull_direction, samples, shape)
            if draft_result is not None:
                results[current_face] = draft_result
            face_explorer.Next()
        return results

    def get_draft_for_face(
        self, face: TopoDS_Face, pull_direction: gp_Dir, samples: int, parent_solid
    ) -> float | None:
        """
        Returns the draft angle for any TopoDS_Face.
        This function now dispatches to the desired analysis method.
        """
        # METHOD 1: Your original UV Sampling
        # draft_angle = self._get_draft_by_uv_sampling(face, pull_direction, samples)

        # METHOD 2: The new Mesh-based analysis
        draft_angle = self.get_draft_for_mesh(face, pull_direction)

        FreeCAD.Console.PrintMessage(f"Draft angle: {draft_angle}\n")
        return draft_angle

    # --- NEW SPECIALIZED MESH-BASED FUNCTIONS ---

    def get_draft_for_mesh(
        self, face: TopoDS_Face, pull_direction: gp_Dir, linear_deflection: float = 0.1
    ) -> float | None:
        """
        Analyzes a face by generating a mesh and checking the draft of each triangle.
        """
        # 1. Generate a mesh for the face. The linear_deflection controls accuracy.
        mesh = BRepMesh_IncrementalMesh(face, linear_deflection)
        mesh.Perform()
        self._export_face_mesh_to_stl(face, "inspect")

        # 2. Get the triangulation data from the face
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation(face, location)

        if triangulation is None:
            FreeCAD.Console.PrintWarning(
                f"Could not get triangulation for face {face.__hash__()}\n"
            )
            return None

        min_draft_angle = 180.0

        FreeCAD.Console.PrintMessage(f"Scanning face: {face.__hash__()}\n")

        # 3. Iterate through each triangle in the mesh
        for i in range(1, triangulation.NbTriangles() + 1):
            p1, p2, p3 = triangulation.Triangle(i).Get()

            # Get the 3D vertex coordinates for the triangle
            v1 = triangulation.Node(p1)
            v2 = triangulation.Node(p2)
            v3 = triangulation.Node(p3)

            # 4. Calculate the normal for this triangle
            triangle_normal = self.get_triangle_normal(v1, v2, v3)
            if not triangle_normal:
                continue

            # IMPORTANT: The calculated normal's direction is based on vertex order.
            # We must respect the face's orientation flag to get the true outward normal.
            # if face.Orientation() == TopAbs_REVERSED:
            #     triangle_normal.Reverse()

            # 5. Calculate the draft for this triangle's normal
            draft_angle = self.get_draft_for_dir(triangle_normal, pull_direction)

            FreeCAD.Console.PrintMessage(f"Triangle {i} draft is {draft_angle} deg.\n")

            # 6. Keep track of the minimum draft found so far
            if draft_angle < min_draft_angle:
                min_draft_angle = draft_angle

        return min_draft_angle

    def get_triangle_normal(self, p1: gp_Pnt, p2: gp_Pnt, p3: gp_Pnt) -> gp_Dir | None:
        """Calculates the normal vector of a single triangle defined by three points."""
        vec1 = gp_Vec(p1, p2)
        vec2 = gp_Vec(p1, p3)

        normal_vec = vec1.Crossed(vec2)

        if normal_vec.Magnitude() < 1e-2:
            return None  # Degenerate triangle with no area

        return gp_Dir(normal_vec)

    # ------------ UV SAMPLING FUNCTIONS ---------------- #

    def _get_draft_by_uv_sampling(
        self, face: TopoDS_Face, pull_direction: gp_Dir, samples: int
    ) -> float:
        """Wrapper for your original UV-based analysis method."""
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() == GeomAbs_Plane:
            return self.get_draft_for_plane(face, pull_direction)
        else:
            return self.get_draft_for_curve(face, pull_direction, samples)

    def get_draft_for_curve(self, face: TopoDS_Face, pull_direction: gp_Dir, samples: int) -> float:
        surface = BRepAdaptor_Surface(face, True)
        u_min, u_max = surface.FirstUParameter(), surface.LastUParameter()
        v_min, v_max = surface.FirstVParameter(), surface.LastVParameter()
        u_range = u_max - u_min
        v_range = v_max - v_min
        if samples < 2:
            samples = 2
        u_step = u_range / (samples - 1)
        v_step = v_range / (samples - 1)
        min_draft_angle = 180
        for i in range(samples):
            u = u_min + i * u_step
            for j in range(samples):
                v = v_min + j * v_step
                normal_dir = self.get_face_uv_normal(face, u, v)
                if not normal_dir:
                    FreeCAD.Console.PrintError(f"Normal returned None for face {face.__hash__()}")
                    continue
                draft_angle = self.get_draft_for_dir(normal_dir, pull_direction)
                min_draft_angle = min(min_draft_angle, draft_angle)
        return min_draft_angle

    def get_draft_for_plane(self, face: TopoDS_Face, pull_direction: gp_Dir) -> float:
        u_mid, v_mid = self.get_face_uv_center(face)
        normal_dir = self.get_face_uv_normal(face, u_mid, v_mid)
        if not normal_dir:
            return 999
        return self.get_draft_for_dir(normal_dir, pull_direction)

    def get_draft_for_dir(self, normal_dir: gp_Dir, pull_direction: gp_Dir) -> float:
        angle_deg = math.degrees(pull_direction.Angle(normal_dir))
        if math.isclose(angle_deg, 0.0, abs_tol=1e-5):
            return 90.0
        if math.isclose(angle_deg, 180.0, abs_tol=1e-5):
            return -90.0
        return angle_deg - 90

    def get_face_uv_center(self, face: TopoDS_Face) -> tuple[float, float] | None:
        try:
            u_min, u_max, v_min, v_max = breptools.UVBounds(face)
        except RuntimeError:
            return None
        u_mid: float = (u_max + u_min) / 2
        v_mid: float = (v_max + v_min) / 2
        return (u_mid, v_mid)

    def get_face_uv_normal(self, face: TopoDS_Face, u: float, v: float) -> gp_Dir | None:
        surface: Geom_Surface = BRep_Tool.Surface(face)
        if not surface:
            return None
        props = GeomLProp_SLProps(surface, u, v, 1, 1e-6)
        if props.IsNormalDefined():
            return props.Normal()

    def _export_face_mesh_to_stl(self, face: TopoDS_Face, filename: str):
        """
        Generates a mesh for a single face and saves it as an STL file
        for inspection in software like Blender.
        """
        # Ensure the face has a mesh
        mesh = BRepMesh_IncrementalMesh(face, 0.1)
        mesh.Perform()
        filename = filename + "_" + str(face.__hash__()) + ".stl"

        # Create an STL writer and write the single face
        stl_writer = StlAPI_Writer()
        stl_writer.Write(face, filename)
        FreeCAD.Console.PrintMessage(
            f"==> EXPORTED MESH for face {face.__hash__()} to {filename}\n"
        )
