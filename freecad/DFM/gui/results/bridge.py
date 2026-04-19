# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the DFM addon.

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

from typing import Optional
import math
import time

from pivy import coin
from PySide6.QtCore import QTimer

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore


class DFMViewProvider:
    """Manages 3D visual feedback, including face/edge highlighting and annotations."""

    OVERLAY_HIGHLIGHT_COLORS = {
        "#E24B4A": (0.886, 0.294, 0.290, 0.0),
        "#D4900A": (0.831, 0.565, 0.039, 0.0),
        "#378ADD": (0.216, 0.541, 0.867, 0.0),
        "#639922": (0.388, 0.600, 0.133, 0.0),
    }
    OVERLAY_INACTIVE_COLOR = (0.5, 0.5, 0.5, 0.7)
    OVERLAY_TRANSPARENCY = 30
    OVERLAY_EDGE_WIDTH = 3
    OVERLAY_NAME = "DFM_Highlight_Overlay"
    ANNOTATION_OFFSET = App.Vector(15, 15, 15)

    def __init__(self, target_object):
        self.target_object = target_object
        self.anno = DFMAnnotation()
        self._cam_animator = CameraAnimator()
        self._highlighted_faces: list[str] = []
        self._highlighted_edges: list[str] = []
        self._original_transparency: int = target_object.ViewObject.Transparency

    def highlight_faces_by_index(self, index_color_pairs: list[tuple[int, str]]):
        """Highlights faces by their 0-based index. Clears any previous highlights."""
        self.anno.remove(App.ActiveDocument)
        Gui.Selection.clearSelection()
        self._highlighted_edges = []

        index_color_map: dict[int, str] = {}
        for idx, hex_col in index_color_pairs:
            existing = index_color_map.get(idx)
            if existing is None or hex_col == "#E24B4A":
                index_color_map[idx] = hex_col

        self._highlighted_faces = [f"Face{i + 1}" for i in index_color_map]

        if not index_color_map:
            self._remove_overlay()
            self.target_object.ViewObject.Visibility = True
            self.target_object.ViewObject.Transparency = self._original_transparency
            return

        self.target_object.ViewObject.Visibility = True
        self._update_overlay(face_color_map=index_color_map, edge_color_map={})

    def highlight_edges_by_index(self, index_color_pairs: list[tuple[int, str]]):
        """Highlights edges by their 0-based index. Clears any previous highlights."""
        self.anno.remove(App.ActiveDocument)
        Gui.Selection.clearSelection()
        self._highlighted_faces = []

        index_color_map: dict[int, str] = {}
        for idx, hex_col in index_color_pairs:
            existing = index_color_map.get(idx)
            if existing is None or hex_col == "#E24B4A":
                index_color_map[idx] = hex_col

        self._highlighted_edges = [f"Edge{i + 1}" for i in index_color_map]

        if not index_color_map:
            self._remove_overlay()
            self.target_object.ViewObject.Visibility = True
            self.target_object.ViewObject.Transparency = self._original_transparency
            return

        self.target_object.ViewObject.Visibility = True
        self._update_overlay(face_color_map={}, edge_color_map=index_color_map)

    def highlight_faces_and_edges_by_index(
        self,
        face_color_pairs: list[tuple[int, str]],
        edge_color_pairs: list[tuple[int, str]],
    ):
        """Highlights both faces and edges simultaneously."""
        self.anno.remove(App.ActiveDocument)
        Gui.Selection.clearSelection()

        face_color_map: dict[int, str] = {}
        for idx, hex_col in face_color_pairs:
            existing = face_color_map.get(idx)
            if existing is None or hex_col == "#E24B4A":
                face_color_map[idx] = hex_col

        edge_color_map: dict[int, str] = {}
        for idx, hex_col in edge_color_pairs:
            existing = edge_color_map.get(idx)
            if existing is None or hex_col == "#E24B4A":
                edge_color_map[idx] = hex_col

        self._highlighted_faces = [f"Face{i + 1}" for i in face_color_map]
        self._highlighted_edges = [f"Edge{i + 1}" for i in edge_color_map]

        if not face_color_map and not edge_color_map:
            self._remove_overlay()
            self.target_object.ViewObject.Visibility = True
            self.target_object.ViewObject.Transparency = self._original_transparency
            return

        self.target_object.ViewObject.Visibility = True
        self._update_overlay(face_color_map=face_color_map, edge_color_map=edge_color_map)

    def annotate_by_index(
        self,
        face_index: Optional[int],
        text: str,
        color_hex: str = "#E24B4A",
    ):
        """Places an annotation on the face at the given 0-based index."""
        if face_index is None:
            return

        face = self.target_object.Shape.Faces[face_index]
        base_pos = self._find_on_surface_point(face)
        self._place_annotation(base_pos, text, color_hex)

    def annotate_edge_by_index(
        self,
        edge_index: Optional[int],
        text: str,
        color_hex: str = "#E24B4A",
    ):
        """Places an annotation at the midpoint of the edge at the given 0-based index."""
        if edge_index is None:
            return

        edge = self.target_object.Shape.Edges[edge_index]
        try:
            mid = edge.FirstParameter + (edge.LastParameter - edge.FirstParameter) * 0.5
            base_pos = edge.valueAt(mid)
        except Exception:
            base_pos = edge.CenterOfMass

        self._place_annotation(base_pos, text, color_hex)

    def _place_annotation(self, base_pos: App.Vector, text: str, color_hex: str):
        h = color_hex.lstrip("#")
        r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
        self.anno.create(
            doc=App.ActiveDocument,
            base_pos=base_pos,
            text_pos=base_pos.add(self.ANNOTATION_OFFSET),
            text=text,
            style_cfg={"bg_color": (r, g, b)},
        )
        App.ActiveDocument.recompute()  # type: ignore

    def _update_overlay(
        self,
        face_color_map: dict[int, str],
        edge_color_map: dict[int, str],
    ):
        """Creates or updates the overlay with per-face and per-edge colors."""
        doc = App.ActiveDocument
        overlay = self._get_overlay()

        if overlay is None:
            overlay = doc.addObject("Part::Feature", self.OVERLAY_NAME)  # type: ignore

        overlay.Shape = self.target_object.Shape.copy()
        self.target_object.ViewObject.Visibility = False

        vo = overlay.ViewObject
        if vo is None:
            return

        if hasattr(vo, "ShowInTree"):
            vo.ShowInTree = False

        vo.PointSize = 0

        def hex_to_rgba(hex_color: str) -> tuple:
            h = hex_color.lstrip("#")
            r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
            return (r, g, b, 0.0)

        num_faces = len(self.target_object.Shape.Faces)
        face_colors = [
            hex_to_rgba(face_color_map[i]) if i in face_color_map else self.OVERLAY_INACTIVE_COLOR
            for i in range(num_faces)
        ]
        vo.DiffuseColor = face_colors

        num_edges = len(self.target_object.Shape.Edges)
        edge_colors = [
            hex_to_rgba(edge_color_map[i]) if i in edge_color_map else self.OVERLAY_INACTIVE_COLOR
            for i in range(num_edges)
        ]
        vo.LineColorArray = edge_colors
        vo.LineWidth = self.OVERLAY_EDGE_WIDTH if edge_color_map else 0

        vo.Transparency = self.OVERLAY_TRANSPARENCY
        doc.recompute()  # type: ignore

    def _find_on_surface_point(self, face) -> App.Vector:
        """Returns a point guaranteed to lie on the face surface."""
        u0, u1, v0, v1 = face.ParameterRange

        candidates = [(0.5, 0.5)]
        steps = 5
        for i in range(steps):
            for j in range(steps):
                candidates.append((i / (steps - 1), j / (steps - 1)))

        for u_norm, v_norm in candidates:
            u = u0 + u_norm * (u1 - u0)
            v = v0 + v_norm * (v1 - v0)
            try:
                pt = face.valueAt(u, v)
                if face.isInside(pt, 1e-3, True):
                    return pt
            except Exception:
                continue

        return face.CenterOfMass

    def zoom_to_selection(self):
        """Focuses the 3D camera on all currently highlighted faces and edges."""
        overlay = self._get_overlay()
        if not overlay or not Gui.activeView():
            return

        targets = self._highlighted_faces + self._highlighted_edges
        if targets:
            self._cam_animator.zoom_to_subelements(overlay, targets)

    def restore(self):
        """Removes the overlay and annotation, returning the viewport to its original state."""
        self.anno.remove(App.ActiveDocument)
        self._remove_overlay()
        self.target_object.ViewObject.Visibility = True

    def _remove_overlay(self):
        """Removes the overlay object from the document if it exists."""
        doc = App.ActiveDocument
        if self.OVERLAY_NAME:
            try:
                obj = doc.getObject(self.OVERLAY_NAME)  # type: ignore
                if obj:
                    doc.removeObject(self.OVERLAY_NAME)  # type: ignore
            except Exception as e:
                App.Console.PrintWarning(f"Failed to remove DFM overlay: {e}\n")
        self._highlighted_faces = []
        self._highlighted_edges = []

    def _get_overlay(self):
        """Returns the live overlay document object, or None if it doesn't exist."""
        if self.OVERLAY_NAME:
            return App.ActiveDocument.getObject(self.OVERLAY_NAME)  # type: ignore
        return None


class DFMAnnotation:
    """Annotation class for DFM issues in FreeCAD."""

    # Default Styles
    TEXT_COLOR = (0.95, 0.95, 0.95)
    BG_COLOR = (0.6, 0.0, 0.0)
    FONT_SIZE = 14
    DISPLAY_MODE = "Line"

    def __init__(self, name="DFM_Issue_Annotation"):
        self.name = name
        self._active_name = None

    def remove(self, doc):
        """Removes the current annotation from the document."""
        if self._active_name:
            try:
                if obj := doc.getObject(self._active_name):
                    doc.removeObject(obj.Name)
            except Exception:
                pass
            self._active_name = None

    def create(self, doc, base_pos, text_pos, text, style_cfg=None):
        """Creates a new annotation with leader lines."""
        style = style_cfg or {}
        self.remove(doc)

        label = doc.addObject("App::AnnotationLabel", self.name)
        self._active_name = label.Name

        label.LabelText = [text]
        label.BasePosition = base_pos
        label.TextPosition = text_pos

        if vo := label.ViewObject:
            vo.TextColor = style.get("text_color", self.TEXT_COLOR)
            vo.BackgroundColor = style.get("bg_color", self.BG_COLOR)

            if hasattr(vo, "ShowInTree"):
                vo.ShowInTree = False

            if "DisplayMode" in vo.PropertiesList:
                vo.DisplayMode = self.DISPLAY_MODE

            f_size = style.get("font_size", self.FONT_SIZE)
            if hasattr(vo, "FontSize"):
                vo.FontSize = f_size
            if hasattr(label, "FontSize"):
                label.FontSize = f_size

        return label


class CameraAnimator:
    DURATION = 0.6
    PADDING = 1.7
    FPS = 60

    def __init__(self) -> None:
        self._timer: Optional[QTimer] = None
        self._start: Optional[float] = None

        self._cam = None

        self._is_ortho: bool = True

        self._pos0: Optional[App.Vector] = None
        self._pos1: Optional[App.Vector] = None
        self._h0: float = 1.0
        self._h1: float = 1.0

    def zoom_to_subelement(self, shape_object, subelement_name: str) -> None:
        """Pan and zoom to frame a single subelement."""
        bb = self._subelement_bounding_box(shape_object, subelement_name)
        if bb is None:
            App.Console.PrintWarning(
                f"CameraAnimator: could not resolve subelement '{subelement_name}'\n"
            )
            return
        self._zoom_to_boundbox(bb)

    def zoom_to_subelements(self, shape_object, subelement_names: list[str]) -> None:
        """Pan and zoom to frame all listed subelements together."""
        merged: Optional[App.BoundBox] = None
        for name in subelement_names:
            bb = self._subelement_bounding_box(shape_object, name)
            if bb is None:
                continue
            merged = App.BoundBox(bb) if merged is None else (merged.add(bb) or merged)

        if merged is None:
            return
        self._zoom_to_boundbox(merged)

    def _subelement_bounding_box(self, shape_object, name: str) -> Optional[App.BoundBox]:
        shape = shape_object.Shape
        try:
            if name.startswith("Face"):
                sub = shape.Faces[int(name[4:]) - 1]
            elif name.startswith("Edge"):
                sub = shape.Edges[int(name[4:]) - 1]
            elif name.startswith("Vertex"):
                sub = shape.Vertexes[int(name[6:]) - 1]
            else:
                return None
            return sub.BoundBox
        except (IndexError, ValueError):
            return None

    def _zoom_to_boundbox(self, bb: App.BoundBox) -> None:
        view = Gui.ActiveDocument.ActiveView  # type: ignore[union-attr]
        if view is None:
            return

        cam = view.getCameraNode()  # type: ignore[attr-defined]
        if cam is None:
            return
        self._cam = cam

        center = bb.Center
        bb_size = max(bb.XMax - bb.XMin, bb.YMax - bb.YMin, bb.ZMax - bb.ZMin)
        frame_size = bb_size * self.PADDING

        cp = cam.position.getValue()
        pos0 = App.Vector(cp[0], cp[1], cp[2])

        orient = cam.orientation.getValue()
        fwd_coin = orient.multVec(coin.SbVec3f(0.0, 0.0, -1.0))
        fwd = App.Vector(fwd_coin[0], fwd_coin[1], fwd_coin[2]).normalize()

        cam_type = str(cam.getTypeId().getName())
        self._is_ortho = "Orthographic" in cam_type
        if "Orthographic" in cam_type:
            dist = float(cam.focalDistance.getValue())
            self._h0 = float(cam.height.getValue())
            self._h1 = float(frame_size)
        else:
            fov_half = float(cam.heightAngle.getValue()) / 2.0
            dist = (frame_size / 2.0) / math.tan(fov_half) if fov_half > 1e-6 else frame_size * 2.0
            self._h0 = 0.0
            self._h1 = 0.0

        self._pos0 = pos0
        self._pos1 = App.Vector(
            center.x - fwd.x * dist,
            center.y - fwd.y * dist,
            center.z - fwd.z * dist,
        )

        if self._timer is not None:
            self._timer.stop()
            self._timer = None

        self._start = time.perf_counter()
        timer = QTimer()
        timer.setInterval(max(1, int(1000 / self.FPS)))
        timer.timeout.connect(self._tick)
        timer.start()
        self._timer = timer

    @staticmethod
    def _smoothstep(t: float) -> float:
        t = max(0.0, min(1.0, t))
        return t * t * (3.0 - 2.0 * t)

    def _tick(self) -> None:
        assert self._start is not None
        assert self._pos0 is not None
        assert self._pos1 is not None

        elapsed = time.perf_counter() - self._start
        raw_t = min(elapsed / self.DURATION, 1.0)
        t = self._smoothstep(raw_t)

        cam = self._cam
        p0, p1 = self._pos0, self._pos1

        cam.position.setValue(  # type: ignore[union-attr]
            p0.x + (p1.x - p0.x) * t,
            p0.y + (p1.y - p0.y) * t,
            p0.z + (p1.z - p0.z) * t,
        )

        if self._is_ortho:
            cam.height.setValue(self._h0 + (self._h1 - self._h0) * t)  # type: ignore[union-attr]

        if raw_t >= 1.0:
            if self._timer is not None:
                self._timer.stop()
            self._timer = None
