# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Viewport marker overlay — interactive 3D markers on faces with DFM findings.

Shows small coloured dots on failing faces/edges directly in the 3D viewport.
Clicking a marker selects the corresponding finding in the tree; hovering or
clicking a face with multiple findings shows a popup to choose between them.

Architecture
~~~~~~~~~~~~
- **MarkerInfo**          – per-geometry data: world point, normal, findings list.
- **MarkerPlacement**     – computes MarkerInfo items from CheckResult data.
- **MarkerSceneLayer**    – Coin3D scene-graph nodes (SoPointSet inside
                            SoAnnotation) and camera sensor for density / occlusion.
- **MarkerInteraction**   – Qt event filter on the viewport widget; converts
                            mouse events into finding selections or popup menus.
- **ViewportMarkerController** – orchestrator wired by the presenter.

Interactive UI lives entirely in Qt (event filters, popups).  Coin3D is used
only for positioning dumb marker points in the 3D scene.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pivy import coin
from PySide6 import QtCore, QtGui, QtWidgets

import FreeCAD as App  # type: ignore

from ...app.prefs import dfm_params
from ...core.models import CheckResult, Severity
from ...gui.results.visuals import severity_color
from ...gui.utils.view import get_active_view, get_view_widget, project_to_screen

if TYPE_CHECKING:
    pass

# ── Preference-backed constants ────────────────────────────


def _max_show_all() -> int:
    """Show every marker when total count is at or below this."""
    return dfm_params().GetInt("MarkersMaxShowAll", 10)


def _min_screen_dist_px() -> int:
    """Greedy filter: minimum px between visible markers."""
    return dfm_params().GetInt("MarkersMinScreenDist", 30)


def _occlusion_dot_threshold() -> float:
    """Face normal · cam_fwd above this → marker hidden."""
    return dfm_params().GetFloat("MarkersOcclusionThreshold", 0.15)


def _hit_radius_px() -> int:
    """Click / hover tolerance in screen pixels."""
    return dfm_params().GetInt("MarkersHitRadius", 14)


def _normal_point_size() -> float:
    """Coin3D point size for normal markers."""
    return dfm_params().GetFloat("MarkersNormalSize", 10.0)


def _highlight_point_size() -> float:
    """Coin3D point size for highlighted markers."""
    return dfm_params().GetFloat("MarkersHighlightSize", 16.0)

# Severity → Coin3D RGB
_SEVERITY_COIN_RGB: dict[Severity, tuple[float, float, float]] = {
    Severity.ERROR: (0.886, 0.294, 0.290),
    Severity.WARNING: (0.831, 0.565, 0.039),
    Severity.INFO: (0.216, 0.541, 0.867),
    Severity.SUCCESS: (0.388, 0.600, 0.133),
}


# ── MarkerInfo ──────────────────────────────────────────────


@dataclass
class MarkerInfo:
    """One marker in the viewport, tied to a specific geometry sub-element."""

    world_point: App.Vector
    face_normal: App.Vector | None  # None for edges
    geometry_label: str  # e.g. "Face3", "Edge7"
    findings: list[CheckResult] = field(default_factory=list)

    @property
    def worst_severity(self) -> Severity:
        if not self.findings:
            return Severity.SUCCESS
        return max(self.findings, key=lambda f: f.severity.value).severity

    @property
    def color_rgb(self) -> tuple[float, float, float]:
        return _SEVERITY_COIN_RGB.get(self.worst_severity, (0.5, 0.5, 0.5))

    @property
    def active_count(self) -> int:
        return sum(1 for f in self.findings if not f.ignore)


# ── MarkerPlacement ─────────────────────────────────────────


class MarkerPlacement:
    """Computes :class:`MarkerInfo` items from analysis results and a shape.

    Uses UV parameter-space sampling with ``face.isInside()`` to guarantee
    the marker point lies on the face surface (handles faces with holes,
    concave boundaries, etc.).  Reuses the algorithm from
    ``DFMViewProvider._find_on_surface_point``.
    """

    @staticmethod
    def compute(
        results: list[CheckResult],
        shape,
    ) -> list[MarkerInfo]:
        """Return one :class:`MarkerInfo` per unique geometry sub-element.

        Multiple findings on the same face/edge are grouped into a single
        marker whose severity is the worst among them.
        """
        # Group findings by geometry label → list[CheckResult]
        geo_map: dict[str, list[CheckResult]] = {}
        for result in results:
            for ref in result.refs:
                geo_map.setdefault(ref.label, []).append(result)

        markers: list[MarkerInfo] = []
        for label, findings in geo_map.items():
            point, normal = MarkerPlacement._point_and_normal(label, shape)
            if point is None:
                continue
            markers.append(MarkerInfo(
                world_point=point,
                face_normal=normal,
                geometry_label=label,
                findings=findings,
            ))
        return markers

    @staticmethod
    def _point_and_normal(
        label: str, shape
    ) -> tuple[App.Vector | None, App.Vector | None]:
        """Resolve a geometry label to a world-space point and optional normal."""
        if label.startswith("Face"):
            return MarkerPlacement._face_point_and_normal(label, shape)
        if label.startswith("Edge"):
            return MarkerPlacement._edge_point(label, shape), None
        return None, None

    @staticmethod
    def _face_point_and_normal(
        label: str, shape
    ) -> tuple[App.Vector | None, App.Vector | None]:
        """Find a point on the face surface and the outward normal there."""
        try:
            index = int(label[4:]) - 1  # "Face1" → 0
            face = shape.Faces[index]
        except (ValueError, IndexError):
            return None, None

        point = MarkerPlacement._find_on_surface_point(face)
        # Compute outward normal at the point.
        try:
            u, v = face.Surface.parameter(point)
            normal = face.normalAt(u, v)
        except Exception:
            normal = None
        return point, normal

    @staticmethod
    def _edge_point(label: str, shape) -> App.Vector | None:
        """Midpoint of the named edge."""
        try:
            index = int(label[4:]) - 1  # "Edge1" → 0
            edge = shape.Edges[index]
            mid = edge.FirstParameter + (edge.LastParameter - edge.FirstParameter) * 0.5
            return edge.valueAt(mid)
        except Exception:
            try:
                return shape.Edges[int(label[4:]) - 1].CenterOfMass
            except Exception:
                return None

    @staticmethod
    def _find_on_surface_point(face) -> App.Vector:
        """Return a point guaranteed to lie on *face*.

        Samples the UV parameter space at the centroid and a 5×5 grid,
        checking each candidate with ``face.isInside()``.  Falls back to
        ``CenterOfMass`` if nothing passes (degenerate geometry).
        """
        u0, u1, v0, v1 = face.ParameterRange

        # Try the centre first, then a uniform grid.
        candidates: list[tuple[float, float]] = [(0.5, 0.5)]
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


# ── MarkerSceneLayer ────────────────────────────────────────


class MarkerSceneLayer:
    """Manages Coin3D scene-graph nodes for the marker dots.

    Renders markers as a ``SoPointSet`` inside an ``SoAnnotation`` (always
    on top, unlit).  A camera sensor triggers density filtering and
    occlusion culling on every view change.
    """

    def __init__(self) -> None:
        self._root: coin.SoAnnotation | None = None
        self._coords: coin.SoCoordinate3 | None = None
        self._material: coin.SoMaterial | None = None
        self._draw_style: coin.SoDrawStyle | None = None
        self._point_set: coin.SoPointSet | None = None

        # Highlight ring (separate separator so its style is independent).
        self._hl_sep: coin.SoSeparator | None = None
        self._hl_coords: coin.SoCoordinate3 | None = None
        self._hl_material: coin.SoMaterial | None = None
        self._hl_draw_style: coin.SoDrawStyle | None = None
        self._hl_point_set: coin.SoPointSet | None = None

        self._cam_sensor: coin.SoNodeSensor | None = None
        self._active_camera: coin.SoNode | None = None

        # Full marker set and the currently visible subset.
        self._all_markers: list[MarkerInfo] = []
        self._visible_indices: list[int] = []

        self._view = None
        self._highlighted_index: int = -1

    # ── Public API ──────────────────────────────────────

    def build(self, markers: list[MarkerInfo]) -> None:
        """Create (or rebuild) the Coin3D nodes and start camera tracking."""
        self.remove()
        if not markers:
            return

        view = get_active_view()
        if not view:
            return
        self._view = view

        self._all_markers = markers
        self._build_scenegraph()
        self._attach_camera_sensor()
        self._update_visible()

    def set_highlighted(self, index: int) -> None:
        """Highlight the marker at *index* (-1 clears)."""
        self._highlighted_index = index
        self._sync_highlight()

    def remove(self) -> None:
        """Tear down all Coin3D nodes and detach the camera sensor."""
        if self._cam_sensor:
            self._cam_sensor.detach()
            self._cam_sensor = None
        self._active_camera = None

        if self._root and self._view:
            try:
                sg = self._view.getSceneGraph()
                if sg:
                    sg.removeChild(self._root)
            except Exception:
                pass
        self._root = None
        self._coords = None
        self._material = None
        self._draw_style = None
        self._point_set = None
        self._hl_sep = None
        self._hl_coords = None
        self._hl_material = None
        self._hl_draw_style = None
        self._hl_point_set = None
        self._view = None
        self._all_markers = []
        self._visible_indices = []
        self._highlighted_index = -1

    @property
    def visible_markers(self) -> list[tuple[int, MarkerInfo]]:
        """Return (original_index, MarkerInfo) pairs for currently visible markers."""
        return [(i, self._all_markers[i]) for i in self._visible_indices]

    # ── Scene-graph construction ────────────────────────

    def _build_scenegraph(self) -> None:
        self._root = coin.SoAnnotation()

        # Unlit, always-on-top rendering.
        lm = coin.SoLightModel()
        lm.model.setValue(coin.SoLightModel.BASE_COLOR)
        self._root.addChild(lm)

        db = coin.SoDepthBuffer()
        db.test.setValue(False)
        self._root.addChild(db)

        # Per-vertex colouring.
        mb = coin.SoMaterialBinding()
        mb.value.setValue(coin.SoMaterialBinding.PER_VERTEX)
        self._root.addChild(mb)

        self._material = coin.SoMaterial()
        self._root.addChild(self._material)

        self._draw_style = coin.SoDrawStyle()
        self._draw_style.pointSize.setValue(_normal_point_size())
        self._root.addChild(self._draw_style)

        self._coords = coin.SoCoordinate3()
        self._root.addChild(self._coords)

        self._point_set = coin.SoPointSet()
        self._root.addChild(self._point_set)

        # Highlight layer (drawn on top of the regular dots).
        self._hl_sep = coin.SoSeparator()

        hl_lm = coin.SoLightModel()
        hl_lm.model.setValue(coin.SoLightModel.BASE_COLOR)
        self._hl_sep.addChild(hl_lm)

        hl_db = coin.SoDepthBuffer()
        hl_db.test.setValue(False)
        self._hl_sep.addChild(hl_db)

        self._hl_material = coin.SoMaterial()
        self._hl_sep.addChild(self._hl_material)

        self._hl_draw_style = coin.SoDrawStyle()
        self._hl_draw_style.pointSize.setValue(_highlight_point_size())
        self._hl_sep.addChild(self._hl_draw_style)

        self._hl_coords = coin.SoCoordinate3()
        self._hl_sep.addChild(self._hl_coords)

        self._hl_point_set = coin.SoPointSet()
        self._hl_sep.addChild(self._hl_point_set)

        self._root.addChild(self._hl_sep)

        # Add to scene graph.
        sg = self._view.getSceneGraph()
        if sg:
            sg.addChild(self._root)

    # ── Camera sensor ───────────────────────────────────

    def _attach_camera_sensor(self) -> None:
        camera = self._view.getCameraNode()
        if not camera:
            return
        self._active_camera = camera
        self._cam_sensor = coin.SoNodeSensor(self._on_camera_changed, None)
        self._cam_sensor.attach(camera)

    def _on_camera_changed(self, *_args) -> None:
        """Called by the Coin3D sensor whenever the camera moves."""
        self._update_visible()

    # ── Density & occlusion filtering ───────────────────

    def _update_visible(self) -> None:
        """Recompute which markers are visible based on occlusion and density."""
        if not self._all_markers or not self._view:
            return

        cam_fwd = self._camera_forward()

        # Phase 1: occlusion filter (back-facing markers).
        candidates: list[tuple[int, float, float]] = []
        for i, m in enumerate(self._all_markers):
            if m.face_normal is not None and cam_fwd is not None:
                dot = (
                    m.face_normal.x * cam_fwd.x
                    + m.face_normal.y * cam_fwd.y
                    + m.face_normal.z * cam_fwd.z
                )
                # Negative dot → normal faces camera; positive → faces away.
                if dot > _occlusion_dot_threshold():
                    continue

            # Project to screen for density filtering.
            sp = project_to_screen(self._view, m.world_point)
            if sp is None:
                continue
            candidates.append((i, sp[0], sp[1]))

        # Phase 2: density filter (greedy screen-space).
        if len(candidates) <= _max_show_all():
            self._visible_indices = [c[0] for c in candidates]
        else:
            self._visible_indices = self._density_filter(candidates)

        self._sync_coin_nodes()
        self._sync_highlight()

    def _density_filter(
        self, candidates: list[tuple[int, float, float]]
    ) -> list[int]:
        """Greedy screen-space filter: keep markers ≥ _MIN_SCREEN_DIST_PX apart.

        Higher-severity markers are placed first so they win the spacing
        contest.  Returns original indices of the kept markers.
        """
        # Sort by severity descending (worst first).
        def _severity_key(c: tuple[int, float, float]) -> int:
            return -self._all_markers[c[0]].worst_severity.value

        ordered = sorted(candidates, key=_severity_key)
        kept: list[tuple[int, float, float]] = []
        dist = _min_screen_dist_px()
        min_dist_sq = dist * dist

        for c in ordered:
            _, sx, sy = c
            too_close = False
            for k in kept:
                dx = sx - k[1]
                dy = sy - k[2]
                if dx * dx + dy * dy < min_dist_sq:
                    too_close = True
                    break
            if not too_close:
                kept.append(c)

        return [c[0] for c in kept]

    # ── Coin node synchronisation ───────────────────────

    def _sync_coin_nodes(self) -> None:
        """Push the visible marker subset into the Coin3D coordinate and material nodes."""
        if not self._coords or not self._material or not self._point_set:
            return

        n = len(self._visible_indices)
        if n == 0:
            self._coords.point.setNum(0)
            self._point_set.numPoints.setValue(0)
            return

        self._coords.point.setNum(n)
        self._material.diffuseColor.setNum(n)
        for slot, idx in enumerate(self._visible_indices):
            m = self._all_markers[idx]
            self._coords.point.set1Value(
                slot, m.world_point.x, m.world_point.y, m.world_point.z
            )
            r, g, b = m.color_rgb
            self._material.diffuseColor.set1Value(slot, r, g, b)

        self._point_set.numPoints.setValue(n)

    def _sync_highlight(self) -> None:
        """Show or hide the highlight ring around the selected marker."""
        if not self._hl_coords or not self._hl_material or not self._hl_point_set:
            return

        if self._highlighted_index < 0 or self._highlighted_index >= len(self._all_markers):
            self._hl_coords.point.setNum(0)
            self._hl_point_set.numPoints.setValue(0)
            return

        m = self._all_markers[self._highlighted_index]
        self._hl_coords.point.setNum(1)
        self._hl_coords.point.set1Value(0, m.world_point.x, m.world_point.y, m.world_point.z)
        r, g, b = m.color_rgb
        self._hl_material.diffuseColor.setValue(r, g, b)
        self._hl_point_set.numPoints.setValue(1)

    # ── Helpers ─────────────────────────────────────────

    def _camera_forward(self) -> App.Vector | None:
        """Return the camera's forward direction as an App.Vector."""
        try:
            camera = self._view.getCameraNode()
            orientation = camera.orientation.getValue()
            # Default forward in Coin3D is -Z.
            fwd = coin.SbVec3f(0, 0, -1)
            rotated = orientation.multVec(fwd)
            x, y, z = rotated.getValue()
            return App.Vector(x, y, z)
        except Exception:
            return None

    def _project_to_viewport(
        self, point: App.Vector
    ) -> tuple[float, float] | None:
        """Project a world point to (x, y) viewport pixels."""
        return project_to_screen(self._view, point)


# ── MarkerInteraction ───────────────────────────────────────


class MarkerInteraction(QtCore.QObject):
    """Qt event filter that converts mouse events on viewport markers into
    finding selections or multi-finding popup menus.

    Signals
    -------
    finding_selected(CheckResult)
        Emitted when a single-finding marker is clicked or a popup choice
        is made.
    finding_hovered(int)
        Emitted with the marker index on hover (−1 when leaving).
    """

    finding_selected = QtCore.Signal(object)  # CheckResult
    finding_hovered = QtCore.Signal(int)  # marker index or -1

    def __init__(self, scene_layer: MarkerSceneLayer, parent: QtCore.QObject | None = None):
        super().__init__(parent)
        self._scene = scene_layer
        self._view_widget: QtWidgets.QWidget | None = None
        self._popup: QtWidgets.QMenu | None = None
        self._installed_widgets: list[QtWidgets.QWidget] = []

    # ── Install / Uninstall ─────────────────────────────

    def install(self) -> None:
        """Attach the event filter to the viewport widget and its children."""
        widget = get_view_widget()
        if not widget:
            return
        self._view_widget = widget
        self._install_on_tree(widget)

    def uninstall(self) -> None:
        """Remove the event filter from all widgets."""
        for w in self._installed_widgets:
            try:
                w.removeEventFilter(self)
            except RuntimeError:
                pass  # Widget already deleted.
        self._installed_widgets.clear()
        self._view_widget = None
        if self._popup:
            self._popup.close()
            self._popup = None

    def _install_on_tree(self, widget: QtWidgets.QWidget) -> None:
        """Recursively install on *widget* and all its children.

        The actual OpenGL viewport is often a child widget; we install on
        the whole tree so we reliably catch events.
        """
        widget.installEventFilter(self)
        self._installed_widgets.append(widget)
        for child in widget.findChildren(QtWidgets.QWidget):
            child.installEventFilter(self)
            self._installed_widgets.append(child)

    # ── Event filter ────────────────────────────────────

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        etype = event.type()

        if etype == QtCore.QEvent.Type.MouseMove:
            self._handle_hover(event)
            # Never consume mouse-move — preserves FreeCAD navigation.
            return False

        if etype == QtCore.QEvent.Type.MouseButtonPress:
            if event.button() == QtCore.Qt.MouseButton.LeftButton:
                hit = self._hit_test(event.position())
                if hit is not None:
                    idx, marker = hit
                    self._handle_click(idx, marker, event.globalPosition().toPoint())
                    return True  # Consume the click.

        return False

    # ── Mouse handling ──────────────────────────────────

    def _handle_hover(self, event) -> None:
        hit = self._hit_test(event.position())
        if hit is not None:
            self.finding_hovered.emit(hit[0])
        else:
            self.finding_hovered.emit(-1)

    def _handle_click(
        self, index: int, marker: MarkerInfo, global_pos: QtCore.QPoint
    ) -> None:
        """Handle a left-click on a marker dot."""
        active = [f for f in marker.findings if not f.ignore]
        if not active:
            # All findings on this face are ignored — still select one.
            active = marker.findings

        if len(active) == 1:
            self.finding_selected.emit(active[0])
        elif len(active) > 1:
            self._show_finding_popup(active, marker, global_pos)

    def _show_finding_popup(
        self,
        findings: list[CheckResult],
        marker: MarkerInfo,
        global_pos: QtCore.QPoint,
    ) -> None:
        """Show a popup menu listing all findings for a multi-finding marker."""
        if self._popup:
            self._popup.close()

        self._popup = QtWidgets.QMenu()
        self._popup.setStyleSheet(
            "QMenu { background: #2b2b2b; border: 1px solid #555; }"
            "QMenu::item { color: #ddd; padding: 4px 16px 4px 8px; }"
            "QMenu::item:selected { background: rgba(128,128,128,0.3); }"
        )

        for finding in findings:
            color = severity_color(finding.severity)
            label = f"[{finding.rule_id.label}] {finding.overview}"
            action = self._popup.addAction(label)
            # Coloured dot in front of the text.
            pm = QtGui.QPixmap(12, 12)
            pm.fill(QtCore.Qt.GlobalColor.transparent)
            painter = QtGui.QPainter(pm)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(color))
            painter.drawEllipse(1, 1, 10, 10)
            painter.end()
            action.setIcon(QtGui.QIcon(pm))

            # Capture *finding* in the lambda closure properly.
            action.triggered.connect(
                lambda checked=False, f=finding: self.finding_selected.emit(f)
            )

        self._popup.popup(global_pos)

    # ── Hit testing ─────────────────────────────────────

    def _hit_test(
        self, pos: QtCore.QPointF
    ) -> tuple[int, MarkerInfo] | None:
        """Return (original_index, MarkerInfo) of the nearest visible marker
        within ``_HIT_RADIUS_PX`` of *pos*, or None."""
        view = get_active_view()
        if not view:
            return None

        mx, my = pos.x(), pos.y()
        best: tuple[int, MarkerInfo] | None = None
        radius = _hit_radius_px()
        best_dist_sq = radius * radius

        for idx, marker in self._scene.visible_markers:
            sp = project_to_screen(view, marker.world_point)
            if sp is None:
                continue
            dx = mx - sp[0]
            dy = my - sp[1]
            d2 = dx * dx + dy * dy
            if d2 < best_dist_sq:
                best_dist_sq = d2
                best = (idx, marker)

        return best


# ── ViewportMarkerController ────────────────────────────────


class ViewportMarkerController:
    """Orchestrator: creates markers, manages the scene layer and interaction,
    and bridges between viewport clicks and the results-tree selection.

    Parameters
    ----------
    target_object
        The FreeCAD document object whose shape is being analysed.
    on_finding_selected
        Callback invoked when the user picks a finding via a viewport marker.
        The presenter wires this to select the finding in the tree and show
        the annotation overlay.
    """

    def __init__(
        self,
        target_object,
        on_finding_selected=None,
        on_finding_hover_started=None,
        on_finding_hover_ended=None,
    ):
        self._target = target_object
        self._on_finding_selected = on_finding_selected
        self._on_finding_hover_started = on_finding_hover_started
        self._on_finding_hover_ended = on_finding_hover_ended
        self._scene = MarkerSceneLayer()
        self._interaction = MarkerInteraction(self._scene)
        self._all_markers: list[MarkerInfo] = []
        self._results: list[CheckResult] = []
        self._hovered_index: int = -1

        # Wire interaction signals.
        self._interaction.finding_selected.connect(self._handle_finding_selected)
        self._interaction.finding_hovered.connect(self._handle_hover)

    # ── Public API ──────────────────────────────────────

    def activate(
        self,
        results: list[CheckResult],
        *,
        show_errors: bool = True,
        show_warnings: bool = True,
        show_ignored: bool = True,
    ) -> None:
        """Compute and display markers for the given results.

        Called by the presenter after ``render_tree`` to show marker dots
        for all visible findings.
        """
        self._results = results
        filtered = self._filter_results(
            results,
            show_errors=show_errors,
            show_warnings=show_warnings,
            show_ignored=show_ignored,
        )
        shape = self._target.Shape
        self._all_markers = MarkerPlacement.compute(filtered, shape)
        self._scene.build(self._all_markers)
        self._interaction.install()

    def update_filters(
        self,
        *,
        show_errors: bool = True,
        show_warnings: bool = True,
        show_ignored: bool = True,
    ) -> None:
        """Re-filter existing results when toolbar toggles change."""
        if not self._results:
            return
        filtered = self._filter_results(
            self._results,
            show_errors=show_errors,
            show_warnings=show_warnings,
            show_ignored=show_ignored,
        )
        shape = self._target.Shape
        self._all_markers = MarkerPlacement.compute(filtered, shape)
        self._scene.build(self._all_markers)
        # Re-install interaction (widget references may have changed).
        self._interaction.uninstall()
        self._interaction = MarkerInteraction(self._scene)
        self._interaction.finding_selected.connect(self._handle_finding_selected)
        self._interaction.finding_hovered.connect(self._handle_hover)
        self._interaction.install()
        self._hovered_index = -1

    def highlight_finding(self, finding: CheckResult | None) -> None:
        """Highlight the marker for *finding* (or clear if None)."""
        if finding is None:
            self._scene.set_highlighted(-1)
            return

        for i, m in enumerate(self._all_markers):
            if any(f is finding for f in m.findings):
                self._scene.set_highlighted(i)
                return
        self._scene.set_highlighted(-1)

    def clear_highlight(self) -> None:
        """Remove any marker highlight."""
        self._scene.set_highlighted(-1)

    def marker_for_finding(self, finding: CheckResult) -> MarkerInfo | None:
        """Return the MarkerInfo that contains *finding*, or None."""
        for m in self._all_markers:
            if any(f is finding for f in m.findings):
                return m
        return None

    def deactivate(self) -> None:
        """Remove all markers and interaction from the viewport."""
        self._interaction.uninstall()
        self._scene.remove()
        self._all_markers = []
        self._results = []
        self._hovered_index = -1

    # ── Internal ────────────────────────────────────────

    def _handle_finding_selected(self, finding: CheckResult) -> None:
        if self._on_finding_selected:
            self._on_finding_selected(finding)

    def _handle_hover(self, index: int) -> None:
        if index == self._hovered_index:
            return
        prev = self._hovered_index
        self._hovered_index = index
        self._scene.set_highlighted(index)

        if index >= 0 and index < len(self._all_markers):
            marker = self._all_markers[index]
            if self._on_finding_hover_started:
                self._on_finding_hover_started(marker)
        elif prev >= 0:
            if self._on_finding_hover_ended:
                self._on_finding_hover_ended()

    @staticmethod
    def _filter_results(
        results: list[CheckResult],
        *,
        show_errors: bool,
        show_warnings: bool,
        show_ignored: bool,
    ) -> list[CheckResult]:
        """Filter results for viewport marker display.

        Ignored findings are always excluded from markers — they are
        visual noise on the viewport and the user cannot interact with
        them meaningfully.  Severity filters apply to the remaining
        active findings.
        """
        out: list[CheckResult] = []
        for f in results:
            # Never show ignored findings as viewport markers.
            if f.ignore:
                continue
            if f.severity == Severity.ERROR and not show_errors:
                continue
            if f.severity == Severity.WARNING and not show_warnings:
                continue
            out.append(f)
        return out
