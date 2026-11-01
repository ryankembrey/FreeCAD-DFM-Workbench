# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ...core.models import CheckResult
from ...gui.results.camera import ResultsCamera
from ...gui.results.highlight_scene import HighlightScene, merge_colors
from ...gui.results.overlay import FindingOverlay


class DFMViewProvider:
    """3D feedback for the results panel: highlighted findings, the finding card and zooming.

    The model is drawn by a :class:`HighlightScene` Coin node while findings
    are highlighted; the model itself is hidden meanwhile and shown again
    when nothing is highlighted or the panel closes.
    """

    # The document object older versions used for highlighting. Removed if
    # a crash left one behind.
    LEGACY_OVERLAY_NAME = "DFM_Highlight_Overlay"

    def __init__(self, target_object):
        self.target_object = target_object
        self._overlay = FindingOverlay()
        self.camera = ResultsCamera(target_object)
        self._scene = HighlightScene(target_object)
        self._highlighted_faces: list[str] = []
        self._highlighted_edges: list[str] = []
        self._highlight_pairs: tuple[list, list] = ([], [])
        self._annotations = True
        try:
            self._target_visible = bool(target_object.ViewObject.Visibility)
        except Exception:
            self._target_visible = True
        self._remove_legacy_overlay()

    # -- Highlighting ------------------------------------------------------

    def highlight_faces_by_index(self, index_color_pairs: list[tuple[int, str]]):
        """Highlights faces by their 0-based index. Clears any previous highlights."""
        self.highlight_faces_and_edges_by_index(index_color_pairs, [])

    def highlight_edges_by_index(self, index_color_pairs: list[tuple[int, str]]):
        """Highlights edges by their 0-based index. Clears any previous highlights."""
        self.highlight_faces_and_edges_by_index([], index_color_pairs)

    def highlight_faces_and_edges_by_index(
        self,
        face_color_pairs: list[tuple[int, str]],
        edge_color_pairs: list[tuple[int, str]],
    ):
        """Highlights faces and edges together; the most severe color wins per element."""
        self._highlight_pairs = (list(face_color_pairs), list(edge_color_pairs))
        self._overlay.remove()
        Gui.Selection.clearSelection()

        face_color_map = merge_colors(face_color_pairs)
        edge_color_map = merge_colors(edge_color_pairs)
        self._highlighted_faces = [f"Face{i + 1}" for i in face_color_map]
        self._highlighted_edges = [f"Edge{i + 1}" for i in edge_color_map]

        if not face_color_map and not edge_color_map:
            # Nothing to show: the model as the designer made it.
            self._scene.hide()
            self._set_target_visible(self._target_visible)
            return

        self._scene.set_highlight(face_color_map, edge_color_map)
        self._scene.show()
        if self._scene.visible:
            self._set_target_visible(False)

    def set_display_style(self, rest_color=None, rest_transparency=None, xray=None) -> None:
        """Override how the rest of the model looks (for report images); None restores the default."""
        self._scene.set_style(rest_color, rest_transparency, xray)

    def current_highlight(self) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
        """The face and edge pairs last passed to ``highlight_faces_and_edges_by_index``."""
        faces, edges = self._highlight_pairs
        return (list(faces), list(edges))

    # -- Finding card ------------------------------------------------------

    def show_finding_overlay(
        self,
        finding: CheckResult,
        color_hex: str = "#E24B4A",
    ):
        """Shows the viewport overlay card for a finding, anchored to its geometry."""
        if not self._annotations:
            return
        face_refs = [ref for ref in finding.refs if ref.type == "Face"]
        edge_refs = [ref for ref in finding.refs if ref.type == "Edge"]

        anchor = None
        if face_refs:
            try:
                face = self.target_object.Shape.Faces[face_refs[0].index]
                anchor = self._find_on_surface_point(face)
            except (IndexError, Exception):
                pass
        elif edge_refs:
            try:
                edge = self.target_object.Shape.Edges[edge_refs[0].index]
                mid = edge.FirstParameter + (edge.LastParameter - edge.FirstParameter) * 0.5
                anchor = edge.valueAt(mid)
            except Exception:
                try:
                    anchor = edge.CenterOfMass
                except Exception:
                    pass

        if anchor is None:
            return

        self._overlay.show(anchor, finding, color_hex)

    def set_annotations_enabled(self, on: bool) -> None:
        """Turn the info cards in the 3D view on or off (the toolbar toggle)."""
        self._annotations = bool(on)
        if not on:
            self._overlay.remove()

    def remove_finding_overlay(self):
        """Hide the viewport overlay card, if shown."""
        self._overlay.remove()

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

    # -- Camera and cleanup ----------------------------------------------------

    def zoom_to_selection(self):
        """Focuses the 3D camera on all currently highlighted faces and edges."""
        if not Gui.activeView():
            return
        faces, edges = self._highlight_pairs
        refs = [("Face", i) for i, _ in faces] + [("Edge", i) for i, _ in edges]
        if refs:
            self.camera.zoom_to(refs)

    def restore(self):
        """Removes the card and the highlight, returning the viewport to its original state."""
        self._overlay.remove()
        self._scene.remove()
        self._highlighted_faces = []
        self._highlighted_edges = []
        self._highlight_pairs = ([], [])
        self._set_target_visible(self._target_visible)

    def _set_target_visible(self, visible: bool) -> None:
        try:
            vo = self.target_object.ViewObject
            if bool(vo.Visibility) != bool(visible):
                vo.Visibility = bool(visible)
        except Exception:
            pass

    def _remove_legacy_overlay(self) -> None:
        doc = App.ActiveDocument
        try:
            if doc is not None and doc.getObject(self.LEGACY_OVERLAY_NAME):
                doc.removeObject(self.LEGACY_OVERLAY_NAME)
        except Exception as e:
            App.Console.PrintWarning(f"Failed to remove an old DFM overlay: {e}\n")
