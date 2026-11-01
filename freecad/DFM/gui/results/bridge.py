# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from typing import Optional
import math
import time

from pivy import coin
from PySide6.QtCore import QTimer

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ...app.prefs import dfm_params
from ...core.models import CheckResult
from ...gui.results.highlight_scene import HighlightScene, merge_colors
from ...gui.results.overlay import FindingOverlay
from ...gui.utils.view import get_active_view


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
        self._cam_animator = CameraAnimator()
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
        targets = self._highlighted_faces + self._highlighted_edges
        if targets:
            self._cam_animator.zoom_to_subelements(self.target_object, targets)

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


class CameraAnimator:
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

        # Read once from prefs at zoom time; cached per animation.
        self._duration: float = 0.6
        self._padding: float = 1.7

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
        view = get_active_view()
        if view is None:
            return

        cam = view.getCameraNode()  # type: ignore[attr-defined]
        if cam is None:
            return
        self._cam = cam

        # Read animation prefs once per zoom (cached for the tick loop).
        params = dfm_params()
        self._duration = params.GetFloat("ResultsCameraAnimDuration", 0.6)
        self._padding = params.GetFloat("ResultsCameraPadding", 1.7)

        center = bb.Center
        bb_size = max(bb.XMax - bb.XMin, bb.YMax - bb.YMin, bb.ZMax - bb.ZMin)
        frame_size = bb_size * self._padding

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
        raw_t = min(elapsed / self._duration, 1.0) if self._duration > 0 else 1.0
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
