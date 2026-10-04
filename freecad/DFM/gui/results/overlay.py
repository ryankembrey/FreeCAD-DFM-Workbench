# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from __future__ import annotations

import math
from typing import Optional

from pivy import coin
from PySide6 import QtCore, QtGui, QtWidgets

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ...core.models import CheckResult, Severity


_SEVERITY_BG = {
    Severity.ERROR: "#E24B4A",
    Severity.WARNING: "#D4900A",
    Severity.INFO: "#378ADD",
    Severity.SUCCESS: "#639922",
}

_SEVERITY_ICON = {
    Severity.ERROR: ":/icons/dfm_error.svg",
    Severity.WARNING: ":/icons/dfm_warning.svg",
    Severity.INFO: ":/icons/dfm_info.svg",
    Severity.SUCCESS: ":/icons/dfm_success.svg",
}


class FindingCard(QtWidgets.QFrame):
    """A compact card showing severity, value comparison, and expandable feedback."""

    _CARD_CSS = """
    FindingCard {{
        background: {card_bg};
        border: 1px solid {border};
        border-radius: 6px;
    }}
    FindingCard #cardHeader {{
        background: {header_bg};
        border-top-left-radius: 5px;
        border-top-right-radius: 5px;
    }}
    FindingCard #feedbackBody {{
        background: {feedback_bg};
        border-bottom-left-radius: 5px;
        border-bottom-right-radius: 5px;
    }}
    """

    # Signal emitted when the user finishes dragging (new position as QPoint)
    dragged = QtCore.Signal(QtCore.QPoint)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setObjectName("FindingCard")
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        self.setMinimumWidth(260)

        self._build_ui()
        self._feedback_visible = False
        self._feedback_frame.setVisible(False)

        # Drag state
        self._drag_active = False
        self._drag_start: Optional[QtCore.QPoint] = None

        # Re-entrancy guard for theme updates
        self._applying_theme = False

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # header row
        self._header = QtWidgets.QWidget()
        header = self._header
        header.setObjectName("cardHeader")
        h_lay = QtWidgets.QHBoxLayout(header)
        h_lay.setContentsMargins(8, 6, 8, 6)
        h_lay.setSpacing(6)

        self._icon_label = QtWidgets.QLabel()
        self._icon_label.setFixedSize(18, 18)
        h_lay.addWidget(self._icon_label)

        self._value_label = QtWidgets.QLabel()
        self._value_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        h_lay.addWidget(self._value_label)

        h_lay.addStretch(1)

        self._rule_label = QtWidgets.QLabel()
        self._rule_label.setStyleSheet("font-size: 10px;")
        self._rule_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        h_lay.addWidget(self._rule_label)

        self._toggle_btn = QtWidgets.QToolButton()
        self._toggle_btn.setFixedSize(18, 18)
        self._toggle_btn.setArrowType(QtCore.Qt.ArrowType.DownArrow)
        self._toggle_btn.setStyleSheet("QToolButton { border: none; background: transparent; }")
        self._toggle_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.clicked.connect(self._toggle_feedback)
        h_lay.addWidget(self._toggle_btn)

        root.addWidget(header)

        # separator (hidden until feedback is expanded)
        self._separator = QtWidgets.QFrame()
        self._separator.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        self._separator.setFixedHeight(1)
        self._separator.setVisible(False)
        root.addWidget(self._separator)

        # feedback body
        feedback_frame = QtWidgets.QWidget()
        feedback_frame.setObjectName("feedbackBody")
        fb_lay = QtWidgets.QVBoxLayout(feedback_frame)
        fb_lay.setContentsMargins(8, 6, 8, 8)
        fb_lay.setSpacing(0)

        self._feedback_label = QtWidgets.QLabel()
        self._feedback_label.setWordWrap(True)
        self._feedback_label.setStyleSheet("font-size: 11px;")
        fb_lay.addWidget(self._feedback_label)

        # Scroll area so long text doesn't get clipped
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        scroll.setWidget(self._feedback_label)
        scroll.setMaximumHeight(120)
        fb_lay.addWidget(scroll)

        self._feedback_frame = feedback_frame
        root.addWidget(feedback_frame)

    def set_finding(self, finding: CheckResult):
        """Populate the card from a CheckResult."""
        self._finding = finding

        severity = finding.severity
        sev_hex = _SEVERITY_BG.get(severity, "#444444")

        icon_path = _SEVERITY_ICON.get(severity, ":/icons/dfm_info.svg")
        pixmap = QtGui.QPixmap(icon_path).scaled(
            18,
            18,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        self._icon_label.setPixmap(pixmap)

        self._value_label.setText(finding.overview)

        rule_name = getattr(finding.rule_id.value, "label", "")
        self._rule_label.setText(rule_name)

        if finding.message:
            self._feedback_label.setText(finding.message)
            self._toggle_btn.setVisible(True)
        else:
            self._feedback_label.setText("")
            self._toggle_btn.setVisible(False)

        self._feedback_visible = False
        self._feedback_frame.setVisible(False)
        self._toggle_btn.setArrowType(QtCore.Qt.ArrowType.DownArrow)
        self._update_header_radius()

        self._apply_theme()

        # Lock card width to the header content (+ 2px for border)
        header_w = self._header.sizeHint().width() + 2
        self.setFixedWidth(max(260, header_w))

        self.adjustSize()

    def _apply_theme(self):
        """Apply colours from the current FreeCAD theme palette."""
        if not hasattr(self, "_finding") or self._finding is None:
            return

        self._applying_theme = True

        sev_hex = _SEVERITY_BG.get(self._finding.severity, "#444444")

        # Use the main window palette so we pick up FreeCAD stylesheet themes
        mw = Gui.getMainWindow()
        palette = mw.palette() if mw else QtWidgets.QApplication.palette()
        base = palette.color(QtGui.QPalette.ColorRole.Window)

        # FreeCAD dark themes set text colour via QSS but don't update the
        # QPalette WindowText role, so it stays black even on a dark bg.
        # Derive text colour from the background luminance instead.
        lum = 0.299 * base.redF() + 0.587 * base.greenF() + 0.114 * base.blueF()
        fg_hex = "#e8e8e8" if lum < 0.45 else "#1a1a1a"

        # Header: darker shade from palette; Feedback: lighter shade
        header_bg = base.darker(115).name()
        feedback_bg = base.lighter(115).name()
        card_bg = base.name()

        self.setStyleSheet(
            self._CARD_CSS.format(
                card_bg=card_bg,
                border=sev_hex,
                header_bg=header_bg,
                feedback_bg=feedback_bg,
            )
        )

        self._value_label.setStyleSheet(f"color: {fg_hex}; font-weight: bold; font-size: 12px;")
        self._rule_label.setStyleSheet(f"color: {fg_hex}; font-size: 10px;")
        self._feedback_label.setStyleSheet(f"color: {fg_hex}; font-size: 11px;")
        self._separator.setStyleSheet(f"color: {sev_hex};")
        self._toggle_btn.setStyleSheet(
            f"QToolButton {{ border: none; background: transparent; color: {fg_hex}; }}"
        )

        self._applying_theme = False

    def changeEvent(self, event):
        if event.type() == QtCore.QEvent.Type.PaletteChange:
            if not self._applying_theme:
                self._apply_theme()
        super().changeEvent(event)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_start = event.globalPosition().toPoint()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_active and self._drag_start is not None:
            delta = event.globalPosition().toPoint() - self._drag_start
            new_pos = self.pos() + delta
            self.move(new_pos)
            self._drag_start = event.globalPosition().toPoint()
            self.dragged.emit(new_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._drag_active:
            self._drag_active = False
            self._drag_start = None
            self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            self.dragged.emit(self.pos())
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _toggle_feedback(self):
        self._feedback_visible = not self._feedback_visible
        self._separator.setVisible(self._feedback_visible)
        self._feedback_frame.setVisible(self._feedback_visible)
        self._toggle_btn.setArrowType(
            QtCore.Qt.ArrowType.UpArrow if self._feedback_visible else QtCore.Qt.ArrowType.DownArrow
        )
        self._update_header_radius()
        self.adjustSize()

    def _update_header_radius(self):
        """Give the header bottom corners rounding when feedback is collapsed."""
        if self._feedback_visible:
            self._header.setStyleSheet("")
        else:
            self._header.setStyleSheet(
                "#cardHeader { border-bottom-left-radius: 5px; border-bottom-right-radius: 5px; }"
            )

    @staticmethod
    def _darken(hex_color: str, factor: float) -> str:
        h = hex_color.lstrip("#")
        r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
        r = max(0, int(r * (1 - factor)))
        g = max(0, int(g * (1 - factor)))
        b = max(0, int(b * (1 - factor)))
        return f"#{r:02x}{g:02x}{b:02x}"


class FindingOverlay:
    """
    Manages a single FindingCard + Coin3D leader line attached to a
    world-space anchor point visible in FreeCAD's 3D view.
    """

    OFFSET_PX = QtCore.QPoint(30, -40)  # card offset from projected anchor

    def __init__(self):
        self._card: Optional[FindingCard] = None
        self._anchor: Optional[App.Vector] = None
        self._view = None
        self._viewport_widget: Optional[QtWidgets.QWidget] = None

        # Coin3D leader line nodes
        self._line_root: Optional[coin.SoAnnotation] = None
        self._line_coords: Optional[coin.SoCoordinate3] = None
        self._line_material: Optional[coin.SoMaterial] = None

        # Camera tracking for repositioning
        self._cam_sensor: Optional[coin.SoNodeSensor] = None
        self._active_camera = None

        # Drag offset: when the user drags the card we store the pixel
        # offset from the projected anchor so that camera moves preserve
        # the user's chosen position.
        self._drag_offset: Optional[QtCore.QPoint] = None

    def show(self, anchor: App.Vector, finding: CheckResult, color_hex: str = "#E24B4A"):
        """
        Show the overlay card attached to *anchor* (world coords) for
        the given finding.
        """
        self.remove()

        active_doc = Gui.ActiveDocument
        if not active_doc or not hasattr(active_doc, "ActiveView"):
            return
        self._view = active_doc.ActiveView
        if not self._view:
            return

        self._anchor = anchor

        # Find the viewport widget to parent the card to
        self._viewport_widget = self._find_gl_widget(self._view)
        if not self._viewport_widget:
            App.Console.PrintWarning("DFM overlay: could not find viewport widget\n")
            return

        # Reset drag offset for new finding
        self._drag_offset = None

        # Create the card
        self._card = FindingCard(self._viewport_widget)
        self._card.set_finding(finding)
        self._card.dragged.connect(self._on_card_dragged)

        # Build the Coin3D leader line
        self._build_leader_line(color_hex)

        # Track the camera so we can reproject on zoom/pan/rotate
        camera = self._view.getCameraNode()
        if camera:
            self._cam_sensor = coin.SoNodeSensor(self._on_camera_changed, None)
            self._active_camera = camera
            self._cam_sensor.attach(camera)

        # Also track viewport resizes
        self._viewport_widget.installEventFilter(_ResizeFilter.instance(self))

        # Initial position
        self._reposition()
        self._card.show()

    def remove(self):
        """Remove the overlay card and leader line from the scene."""
        if self._card:
            self._card.hide()
            self._card.setParent(None)
            self._card.deleteLater()
            self._card = None

        if self._line_root and self._view:
            try:
                sg = self._view.getSceneGraph()
                if sg:
                    sg.removeChild(self._line_root)
            except Exception:
                pass
        self._line_root = None
        self._line_coords = None
        self._line_material = None

        if self._cam_sensor:
            self._cam_sensor.detach()
            self._cam_sensor = None
        self._active_camera = None

        if self._viewport_widget:
            filt = _ResizeFilter._instances.get(id(self))
            if filt:
                self._viewport_widget.removeEventFilter(filt)
            self._viewport_widget = None

        self._view = None
        self._anchor = None

    def _build_leader_line(self, color_hex: str):
        if not self._view or not hasattr(self._view, "getSceneGraph"):
            return

        self._line_root = coin.SoAnnotation()

        lm = coin.SoLightModel()
        lm.model.setValue(coin.SoLightModel.BASE_COLOR)
        self._line_root.addChild(lm)

        db = coin.SoDepthBuffer()
        db.test.setValue(False)
        self._line_root.addChild(db)

        self._line_material = coin.SoMaterial()
        h = color_hex.lstrip("#")
        r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
        self._line_material.diffuseColor.setValue(r, g, b)
        self._line_root.addChild(self._line_material)

        ds = coin.SoDrawStyle()
        ds.lineWidth.setValue(1.5)
        ds.style.setValue(coin.SoDrawStyle.LINES)
        self._line_root.addChild(ds)

        self._line_coords = coin.SoCoordinate3()
        self._line_root.addChild(self._line_coords)

        ls = coin.SoLineSet()
        ls.numVertices.setValue(2)
        self._line_root.addChild(ls)

        # Dot at anchor
        dot_sep = coin.SoSeparator()

        dot_mat = coin.SoMaterial()
        dot_mat.diffuseColor.setValue(r, g, b)
        dot_sep.addChild(dot_mat)

        dot_trans = coin.SoTranslation()
        if self._anchor:
            dot_trans.translation.setValue(self._anchor.x, self._anchor.y, self._anchor.z)
        dot_sep.addChild(dot_trans)

        dot_coords = coin.SoCoordinate3()
        dot_coords.point.setValue(0, 0, 0)
        dot_sep.addChild(dot_coords)

        dot_marker = coin.SoMarkerSet()
        dot_marker.markerIndex.setValue(coin.SoMarkerSet.CIRCLE_FILLED_9_9)
        dot_sep.addChild(dot_marker)

        self._line_root.addChild(dot_sep)

        self._view.getSceneGraph().addChild(self._line_root)

    def _reposition(self):
        if not self._card or not self._anchor or not self._view or not self._viewport_widget:
            return

        # Project world anchor to screen pixel coords
        screen_pt = self._project_to_screen(self._anchor)
        if screen_pt is None:
            self._card.hide()
            return

        # Use drag offset if the user has repositioned the card,
        # otherwise fall back to the default OFFSET_PX
        offset = self._drag_offset if self._drag_offset is not None else self.OFFSET_PX

        card_pos = screen_pt + offset
        card_w = self._card.width()
        card_h = self._card.height()
        vp_w = self._viewport_widget.width()
        vp_h = self._viewport_widget.height()

        # Clamp to viewport bounds
        if card_pos.x() + card_w > vp_w - 4:
            card_pos.setX(vp_w - card_w - 4)
        if card_pos.x() < 4:
            card_pos.setX(4)
        if card_pos.y() + card_h > vp_h - 4:
            card_pos.setY(vp_h - card_h - 4)
        if card_pos.y() < 4:
            card_pos.setY(4)

        self._card.move(card_pos)

        # Update the leader line endpoint
        card_edge = self._closest_edge_point(card_pos, card_w, card_h, screen_pt)
        card_edge_world = self._unproject_from_screen(card_edge)

        if self._line_coords and card_edge_world:
            self._line_coords.point.set1Value(0, self._anchor.x, self._anchor.y, self._anchor.z)
            self._line_coords.point.set1Value(
                1, card_edge_world.x, card_edge_world.y, card_edge_world.z
            )

        if not self._card.isVisible():
            self._card.show()

    def _on_card_dragged(self, new_pos: QtCore.QPoint):
        """Called while or after the user drags the card."""
        screen_pt = self._project_to_screen(self._anchor)
        if screen_pt is None:
            return

        # Store the offset from the projected anchor to the card position
        # so subsequent camera moves keep the card in the same relative spot
        self._drag_offset = new_pos - screen_pt

        # Update leader line to track the dragged card
        card_w = self._card.width()
        card_h = self._card.height()
        card_edge = self._closest_edge_point(new_pos, card_w, card_h, screen_pt)
        card_edge_world = self._unproject_from_screen(card_edge)

        if self._line_coords and card_edge_world:
            self._line_coords.point.set1Value(0, self._anchor.x, self._anchor.y, self._anchor.z)
            self._line_coords.point.set1Value(
                1, card_edge_world.x, card_edge_world.y, card_edge_world.z
            )

    def _project_to_screen(self, world_pt: App.Vector) -> Optional[QtCore.QPoint]:
        """Project a world-space point to viewport pixel coordinates."""
        try:
            view = self._view
            cam = view.getCameraNode()
            if cam is None:
                return None

            render_mgr = view.getViewer().getSoRenderManager()
            vol = render_mgr.getViewportRegion()

            vv = cam.getViewVolume(vol.getViewportAspectRatio())
            pt = coin.SbVec3f(world_pt.x, world_pt.y, world_pt.z)

            # pivy returns the projected point rather than modifying in-place
            projected = vv.projectToScreen(pt)
            sx, sy, _ = projected.getValue()

            # Use the parent widget size for pixel mapping (matches card positioning)
            if self._viewport_widget:
                vp_w = self._viewport_widget.width()
                vp_h = self._viewport_widget.height()
            else:
                vp_size = vol.getViewportSizePixels()
                vp_w, vp_h = int(vp_size[0]), int(vp_size[1])

            px_x = int(sx * vp_w)
            px_y = int((1.0 - sy) * vp_h)  # Qt y is top-down

            return QtCore.QPoint(px_x, px_y)
        except Exception as e:
            App.Console.PrintWarning(f"DFM overlay: projection failed: {e}\n")
            return None

    def _unproject_from_screen(self, screen_pt: QtCore.QPoint) -> Optional[App.Vector]:
        """
        Compute a world-space point corresponding to a screen pixel,
        on the same depth plane as self._anchor.

        Uses camera orientation and pixel scale rather than pivy's
        SbPlane.intersect, which has unreliable overload resolution
        in some pivy builds.
        """
        try:
            view = self._view
            cam = view.getCameraNode()
            if cam is None or self._anchor is None:
                return None

            anchor_screen = self._project_to_screen(self._anchor)
            if anchor_screen is None:
                return None

            # Pixel delta from the projected anchor to the target screen point
            dx_px = float(screen_pt.x() - anchor_screen.x())
            dy_px = float(screen_pt.y() - anchor_screen.y())

            if self._viewport_widget:
                vp_h = float(self._viewport_widget.height())
            else:
                render_mgr = view.getViewer().getSoRenderManager()
                vol = render_mgr.getViewportRegion()
                vp_size = vol.getViewportSizePixels()
                vp_h = float(vp_size[1])

            # Camera axes in world space
            orient = cam.orientation.getValue()
            cam_right = orient.multVec(coin.SbVec3f(1, 0, 0))
            cam_up = orient.multVec(coin.SbVec3f(0, 1, 0))
            cam_fwd = orient.multVec(coin.SbVec3f(0, 0, -1))

            # Depth of anchor along the view direction
            cx, cy, cz = cam.position.getValue().getValue()
            fwd_x, fwd_y, fwd_z = cam_fwd.getValue()
            to_anchor = App.Vector(
                self._anchor.x - cx,
                self._anchor.y - cy,
                self._anchor.z - cz,
            )
            depth = to_anchor.x * fwd_x + to_anchor.y * fwd_y + to_anchor.z * fwd_z

            # World-units-per-pixel at the anchor's depth
            if isinstance(cam, coin.SoPerspectiveCamera):
                fov = cam.heightAngle.getValue()
                world_h = 2.0 * abs(depth) * math.tan(fov / 2.0)
            else:
                world_h = cam.height.getValue()

            if vp_h < 1.0:
                return None
            px_scale = world_h / vp_h

            # Convert pixel offset to world offset
            rx, ry, rz = cam_right.getValue()
            ux, uy, uz = cam_up.getValue()

            world_dx = dx_px * px_scale
            world_dy = -dy_px * px_scale  # Qt y is top-down

            wx = self._anchor.x + world_dx * rx + world_dy * ux
            wy = self._anchor.y + world_dx * ry + world_dy * uy
            wz = self._anchor.z + world_dx * rz + world_dy * uz

            return App.Vector(wx, wy, wz)
        except Exception as e:
            App.Console.PrintWarning(f"DFM overlay: unproject failed: {e}\n")
        return None

    @staticmethod
    def _closest_edge_point(
        card_pos: QtCore.QPoint, w: int, h: int, target: QtCore.QPoint
    ) -> QtCore.QPoint:
        """Find the point on the card rectangle border closest to *target*."""
        cx = card_pos.x() + w / 2
        cy = card_pos.y() + h / 2
        tx, ty = float(target.x()), float(target.y())

        dx = tx - cx
        dy = ty - cy

        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return QtCore.QPoint(int(cx), card_pos.y())

        # Scale to find where the line exits the rectangle
        half_w, half_h = w / 2.0, h / 2.0
        if abs(dx) * half_h > abs(dy) * half_w:
            # Exit through left or right edge
            scale = half_w / abs(dx)
        else:
            # Exit through top or bottom edge
            scale = half_h / abs(dy)

        edge_x = cx + dx * scale
        edge_y = cy + dy * scale
        return QtCore.QPoint(int(edge_x), int(edge_y))

    def _on_camera_changed(self, userdata, sensor):
        self._reposition()

    @staticmethod
    def _find_gl_widget(view) -> Optional[QtWidgets.QWidget]:
        """Find the 3D viewport widget via the MDI sub-window."""
        try:
            mw = Gui.getMainWindow()
            mdi = mw.findChild(QtWidgets.QMdiArea)
            if mdi is None:
                return None
            sub = mdi.activeSubWindow() or mdi.currentSubWindow()
            if sub is None:
                return None
            return sub.widget()
        except Exception:
            return None


class _ResizeFilter(QtCore.QObject):
    """Re-projects the card whenever the viewport resizes."""

    _instances: dict[int, "_ResizeFilter"] = {}

    def __init__(self, overlay: FindingOverlay):
        super().__init__()
        self._overlay_ref = overlay

    @classmethod
    def instance(cls, overlay: FindingOverlay) -> "_ResizeFilter":
        key = id(overlay)
        if key not in cls._instances:
            cls._instances[key] = cls(overlay)
        return cls._instances[key]

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.Resize:
            self._overlay_ref._reposition()
        return False
