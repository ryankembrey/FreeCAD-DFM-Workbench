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

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore


class DFMViewProvider:
    """Manages 3D visual feedback, including face highlighting and annotations."""

    OVERLAY_HIGHLIGHT_COLORS = {
        "#E24B4A": (0.886, 0.294, 0.290, 0.0),
        "#D4900A": (0.831, 0.565, 0.039, 0.0),
        "#378ADD": (0.216, 0.541, 0.867, 0.0),
        "#639922": (0.388, 0.600, 0.133, 0.0),
    }
    OVERLAY_INACTIVE_COLOR = (0.5, 0.5, 0.5, 0.7)
    OVERLAY_TRANSPARENCY = 30
    OVERLAY_NAME = "DFM_Highlight_Overlay"
    ANNOTATION_OFFSET = App.Vector(15, 15, 15)

    def __init__(self, target_object):
        self.target_object = target_object
        self.anno = DFMAnnotation()
        self._highlighted_faces: list[str] = []
        self._original_transparency: int = target_object.ViewObject.Transparency

    def highlight_by_index(self, index_color_pairs: list[tuple[int, str]]):
        """
        Highlights faces by their 0-based index in the target shape.
        index_color_pairs: list of (face_index, hex_color).
        """
        self._current_color_hex = "#E24B4A"
        self.anno.remove(App.ActiveDocument)
        Gui.Selection.clearSelection()

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
        self._update_overlay(index_color_map)

    def annotate_by_index(
        self,
        face_index: Optional[int],
        text: str,
        color_hex: str = "#E24B4A",
    ):
        """
        Places an annotation on the face at the given 0-based index.
        """
        if face_index is None:
            return

        face = self.target_object.Shape.Faces[face_index]
        base_pos = self._find_on_surface_point(face)

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

    def _update_overlay(self, index_color_map: dict[int, str]):
        """Creates or updates the overlay with per-face colors from an index→hex map."""
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

        vo.Transparency = self.OVERLAY_TRANSPARENCY
        vo.LineWidth = 0
        vo.PointSize = 0

        def hex_to_rgba(hex_color: str) -> tuple:
            h = hex_color.lstrip("#")
            r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
            return (r, g, b, 0.0)

        num_faces = len(self.target_object.Shape.Faces)
        colors = [
            hex_to_rgba(index_color_map[i]) if i in index_color_map else self.OVERLAY_INACTIVE_COLOR
            for i in range(num_faces)
        ]

        vo.DiffuseColor = colors
        vo.Transparency = self.OVERLAY_TRANSPARENCY
        doc.recompute()  # type: ignore

    def _find_on_surface_point(self, face) -> App.Vector:
        """Returns a point guaranteed to lie on the face surface.

        Tries the UV midpoint first. If that falls in a hole or outside the face
        boundary (common on annular and trimmed faces), samples a grid until a
        valid point is found. Falls back to CenterOfMass if nothing works.
        """
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

        return face.CenterOfMass  # fallback

    def zoom_to_selection(self):
        """Focuses the 3D camera on the currently highlighted faces."""
        overlay = self._get_overlay()
        if self._highlighted_faces and overlay and Gui.activeView():
            Gui.Selection.addSelection(overlay, self._highlighted_faces)
            Gui.SendMsgToActiveView("ViewSelection")
            Gui.SendMsgToActiveView("AlignToSelection")
            Gui.Selection.clearSelection()

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
