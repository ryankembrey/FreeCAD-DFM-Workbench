# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Click a direction arrow in the 3D view to flip it.

Hovering an arrow (or its label) shows a faded copy pointing the other way,
a pointing-hand cursor and a "Click to flip" tip. Clicking calls the
arrow's flip callback.

The mouse is watched with a Qt event filter on the 3D view's drawing
widget, which sees each event before Coin does. A click on an arrow is
consumed there, so FreeCAD never selects the face or edge behind it.
Moves are only observed, so rotating and panning work as usual.

Usage::

    self._flipper = ArrowFlipper()
    self._flipper.add(handler.indicator, handler.flip)
    ...
    self._flipper.set_enabled(False)   # while picking in the 3D view
    ...
    self._flipper.detach()             # when the panel closes
"""

from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .view import get_active_view, get_view_widget

TIP_TEXT = "Click to flip"

_Ev = QtCore.QEvent.Type


def _drawing_widget(container: Optional[QtWidgets.QWidget]) -> Optional[QtWidgets.QWidget]:
    """The widget inside a 3D view that receives the mouse events."""
    if container is None:
        return None
    for area in container.findChildren(QtWidgets.QAbstractScrollArea):
        return area.viewport()
    try:
        from PySide6.QtOpenGLWidgets import QOpenGLWidget

        for gl in container.findChildren(QOpenGLWidget):
            return gl
    except ImportError:
        pass
    return container


class ArrowFlipper(QtCore.QObject):
    def __init__(self, parent: Optional[QtCore.QObject] = None):
        super().__init__(parent)
        self._items: list[tuple[object, Callable[[], None]]] = []
        self._widget: Optional[QtWidgets.QWidget] = None
        self._hover = None
        self._enabled = True
        self._cursor_pushed = False
        self._pressed = None
        # Called with the hovered indicator (or None) when that changes.
        self.on_hover: Callable[[Optional[object]], None] = lambda _indicator: None

    # ------------------------------------------------------------------

    def add(self, indicator, on_flip: Callable[[], None]) -> None:
        """Make *indicator* flippable; *on_flip* is called on click."""
        if not any(ind is indicator for ind, _ in self._items):
            self._items.append((indicator, on_flip))
        self._attach()

    def discard(self, indicator) -> None:
        """Stop flipping *indicator* (it was removed)."""
        if self._hover is indicator:
            self._set_hover(None)
        if self._pressed is indicator:
            self._pressed = None
        self._items = [(ind, cb) for ind, cb in self._items if ind is not indicator]

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            self._pressed = None
            self._set_hover(None)

    def detach(self) -> None:
        self._set_hover(None)
        self._pressed = None
        if self._widget is not None:
            try:
                self._widget.removeEventFilter(self)
            except RuntimeError:
                pass
        self._widget = None
        self._items = []

    # ------------------------------------------------------------------

    def _attach(self) -> None:
        widget = _drawing_widget(get_view_widget())
        if widget is None or widget is self._widget:
            return
        if self._widget is not None:
            try:
                self._widget.removeEventFilter(self)
            except RuntimeError:
                pass
        widget.installEventFilter(self)
        widget.setMouseTracking(True)
        self._widget = widget

    def _view_point(self, event) -> Optional[tuple]:
        """The mouse position in the 3D view's pixels, measured from the top left."""
        view = get_active_view()
        if view is None or self._widget is None:
            return None
        pos = event.position()
        try:
            width = self._widget.width()
            scale = view.getSize()[0] / width if width else 1.0
        except Exception:
            scale = 1.0
        return view, pos.x() * scale, pos.y() * scale

    def _hit(self, event) -> Optional[tuple]:
        point = self._view_point(event)
        if point is None:
            return None
        view, x, y = point
        for indicator, on_flip in self._items:
            try:
                if indicator.hit_test(view, x, y):
                    return indicator, on_flip
            except Exception:
                continue
        return None

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt naming)
        if obj is not self._widget or not self._enabled or not self._items:
            return False
        kind = event.type()
        try:
            if kind == _Ev.MouseMove:
                if event.buttons() == QtCore.Qt.MouseButton.NoButton:
                    hit = self._hit(event)
                    self._set_hover(hit[0] if hit else None)
                return False
            if kind == _Ev.Leave:
                self._set_hover(None)
                return False
            if event.type() in (_Ev.MouseButtonPress, _Ev.MouseButtonDblClick) \
                    and event.button() == QtCore.Qt.MouseButton.LeftButton:
                hit = self._hit(event)
                if hit is None:
                    return False
                # Ours: FreeCAD never sees it, so nothing gets selected.
                self._pressed = hit[0]
                return True
            if kind == _Ev.MouseButtonRelease and event.button() == QtCore.Qt.MouseButton.LeftButton:
                if self._pressed is None:
                    return False
                hit = self._hit(event)
                pressed, self._pressed = self._pressed, None
                if hit is not None and hit[0] is pressed:
                    QtCore.QTimer.singleShot(0, hit[1])
                return True
        except RuntimeError:  # the view was closed
            self._widget = None
        return False

    def _set_hover(self, indicator) -> None:
        if indicator is self._hover:
            return
        if self._hover is not None:
            try:
                self._hover.set_ghost(False)
            except Exception:
                pass
        self._hover = indicator
        try:
            self.on_hover(indicator)
        except Exception:
            pass
        if indicator is not None:
            indicator.set_ghost(True)
            if not self._cursor_pushed:
                QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                self._cursor_pushed = True
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos() + QtCore.QPoint(14, 14), TIP_TEXT)
        else:
            if self._cursor_pushed:
                QtWidgets.QApplication.restoreOverrideCursor()
                self._cursor_pushed = False
            QtWidgets.QToolTip.hideText()
