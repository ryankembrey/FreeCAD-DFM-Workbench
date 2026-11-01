# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Camera buttons on the selected row of the findings tree.

Three small buttons at the right end of the selected finding or rule:
zoom to it, face it, and both. They sit in a small widget laid over the
row; the tree's delegate leaves room for them, so they never cover the
count badges or the measured value.
"""

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..rule_cards import render_svg_icon

# A magnifier with a plus: zoom in on it.
ZOOM_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    b'<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5M11 8v6M8 11h6"/></svg>'
)
# A target: turn to face it.
FACE_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    b'<path d="M12 3v3M12 18v3M3 12h3M18 12h3"/><circle cx="12" cy="12" r="4"/></svg>'
)
# A target in a frame: face it and fill the view with it.
ZOOM_FACE_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    b'<path d="M3 8V3h5M21 8V3h-5M3 16v5h5M21 16v5h-5"/><circle cx="12" cy="12" r="3.5"/></svg>'
)

# The moves, in button order: (key, label, shortcut, icon).
MOVES = (
    ("zoom", "Zoom to", "Z", ZOOM_SVG),
    ("face", "Face it", "F", FACE_SVG),
    ("zoom_face", "Zoom and face", "Shift+F", ZOOM_FACE_SVG),
)

_BUTTON_CSS = (
    "QToolButton { border: none; border-radius: 3px; padding: 0px; background: transparent; }"
    "QToolButton:hover { background: rgba(255, 255, 255, 56); }"
    "QToolButton:pressed { background: rgba(255, 255, 255, 90); }"
)

BUTTON_SIZE = QtCore.QSize(22, 20)
SPACING = 2
RIGHT_MARGIN = 4


class RowCameraButtons(QtWidgets.QWidget):
    """The camera buttons for one row; emits the move's key when clicked."""

    triggered = QtCore.Signal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACING)
        self.buttons: dict[str, QtWidgets.QToolButton] = {}
        self._svg: dict[str, bytes] = {}
        self._color_key = None
        for key, label, shortcut, svg in MOVES:
            btn = QtWidgets.QToolButton()
            btn.setFixedSize(BUTTON_SIZE)
            btn.setToolTip(f"{label} ({shortcut})")
            btn.setAccessibleName(label)
            btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)  # keep focus in the tree
            btn.setStyleSheet(_BUTTON_CSS)
            btn.clicked.connect(lambda _c=False, k=key: self.triggered.emit(k))
            row.addWidget(btn)
            self.buttons[key] = btn
            self._svg[key] = svg
        self.setFixedSize(self.width_needed(), BUTTON_SIZE.height())
        self.hide()

    @staticmethod
    def width_needed() -> int:
        n = len(MOVES)
        return n * BUTTON_SIZE.width() + (n - 1) * SPACING

    def set_icon_color(self, color: QtGui.QColor) -> None:
        """Draw the icons in *color*: the selected row's text color."""
        ratio = self.devicePixelRatioF() or 1.0
        key = (QtGui.QColor(color).rgba(), ratio)
        if key == self._color_key:
            return
        self._color_key = key
        for name, btn in self.buttons.items():
            btn.setIconSize(QtCore.QSize(16, 16))
            btn.setIcon(render_svg_icon(self._svg[name], QtGui.QColor(color), 16, ratio))

    def place_on(self, rect: QtCore.QRect, viewport_width: int) -> None:
        """Sit at the right end of the row *rect* (viewport coordinates)."""
        right = min(rect.right(), viewport_width - 1) - RIGHT_MARGIN
        x = right - self.width() + 1
        y = rect.top() + (rect.height() - self.height()) // 2
        self.move(x, y)
        self.show()
        self.raise_()
