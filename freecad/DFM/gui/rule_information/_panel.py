# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Popup that shows a rule's title, diagram and target / limit legend.

``RuleInfoPanel`` is a lightweight tool tip window intended to appear near
the widget that triggered it, for example while the pointer hovers a result.
It does not take focus or grab input; the caller closes it when it is no
longer needed. It can be invoked from any context that has a ``Rulebook``
member and an optional ``RuleLimit``.

Typical use::

    panel = RuleInfoPanel(rule, limit, parent=anchor)
    panel.show_near(anchor)
    ...
    panel.close()
"""

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ...core.processes.process import RuleLimit
from ...core.rules import Rulebook
from ._renderer import DEFAULT_WIDTH
from ._view import RuleDiagramView

_MARGIN_H = 16
_MARGIN_V = 14


class RuleInfoPanel(QtWidgets.QDialog):
    """Illustrated popup explaining a single DFM rule.

    Parameters
    ----------
    rule:
        The Rulebook member to illustrate.
    limit:
        Target / limit values shown in the legend (may be ``None``).
    parent:
        Parent widget, used for stylesheet and palette inheritance.
    """

    def __init__(
        self,
        rule: Rulebook,
        limit: Optional[RuleLimit] = None,
        parent: Optional[QtWidgets.QWidget] = None,
    ):
        super().__init__(parent)
        # A tool tip window neither grabs input nor takes focus, so it can be
        # shown on hover and closed by its owner when the pointer leaves.
        self.setWindowFlags(
            QtCore.Qt.WindowType.ToolTip
            | QtCore.Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(_MARGIN_H, _MARGIN_V, _MARGIN_H, _MARGIN_V)
        root.setSpacing(8)

        title = QtWidgets.QLabel(rule.label, self)
        title_font = title.font()
        title_font.setPointSizeF(title_font.pointSizeF() * 1.15)
        title_font.setBold(True)
        title.setFont(title_font)
        root.addWidget(title)

        self.view = RuleDiagramView(rule, limit, parent=self)
        root.addWidget(self.view)

        # Fixed width: diagram plus margins, so the brief wraps to match.
        self.setFixedWidth(DEFAULT_WIDTH + 2 * _MARGIN_H)

    # ------------------------------------------------------------------
    # Positioning
    # ------------------------------------------------------------------

    def show_near(self, anchor: QtWidgets.QWidget) -> None:
        """Position the panel near *anchor* and show it.

        The panel is placed to the right of the anchor when screen space
        allows; otherwise it falls back to the left or below.
        """
        self.ensurePolished()
        self.adjustSize()
        screen = anchor.screen() or QtGui.QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1920, 1080)

        anchor_global = anchor.mapToGlobal(QtCore.QPoint(0, 0))
        panel_w = self.sizeHint().width()
        panel_h = self.sizeHint().height()

        # Try right of anchor
        x = anchor_global.x() + anchor.width() + 6
        y = anchor_global.y()

        # Fall back to left
        if x + panel_w > avail.right():
            x = anchor_global.x() - panel_w - 6

        # Fall back to below
        if x < avail.left():
            x = anchor_global.x()
            y = anchor_global.y() + anchor.height() + 6

        # Clamp vertical
        if y + panel_h > avail.bottom():
            y = avail.bottom() - panel_h
        if y < avail.top():
            y = avail.top()

        self.move(x, y)
        self.show()

    # ------------------------------------------------------------------
    # Paint: rounded background that follows the palette
    # ------------------------------------------------------------------

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        pal = self.palette()
        painter.setPen(QtGui.QPen(pal.color(QtGui.QPalette.ColorRole.Mid), 1.0))
        painter.setBrush(pal.color(QtGui.QPalette.ColorRole.Window))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)
        painter.end()
