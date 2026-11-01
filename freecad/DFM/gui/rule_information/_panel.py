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
from ...core.rules import Criticality, Rulebook
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
    criticality:
        Shown as a small chip after the title when given.
    values_title:
        Heading above the values, such as "Values for PLA".
    parent:
        Parent widget, used for stylesheet and palette inheritance.
    """

    def __init__(
        self,
        rule: Rulebook,
        limit: Optional[RuleLimit] = None,
        criticality: Optional[Criticality] = None,
        parent: Optional[QtWidgets.QWidget] = None,
        values_title: str = "",
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
        title_row = QtWidgets.QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(title)
        if criticality is not None:
            title_row.addWidget(_criticality_chip(criticality, self))
        title_row.addStretch(1)
        root.addLayout(title_row)

        self.view = RuleDiagramView(rule, limit, values_title=values_title, parent=self)
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
        top_left = anchor.mapToGlobal(QtCore.QPoint(0, 0))
        self.show_at(QtCore.QRect(top_left, anchor.size()), anchor.screen())

    def show_at(self, rect: QtCore.QRect, screen: Optional[QtGui.QScreen] = None) -> None:
        """Show the panel beside *rect* (global coordinates), like ``show_near``.

        Useful when the anchor is part of a widget rather than a widget of
        its own, such as an icon drawn inside a tree row.
        """
        self.ensurePolished()
        self.adjustSize()
        screen = screen or QtGui.QGuiApplication.screenAt(rect.center()) \
            or QtGui.QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1920, 1080)

        panel_w = self.sizeHint().width()
        panel_h = self.sizeHint().height()

        # Try right of the rect
        x = rect.right() + 6
        y = rect.top()

        # Fall back to left
        if x + panel_w > avail.right():
            x = rect.left() - panel_w - 6

        # Fall back to below
        if x < avail.left():
            x = rect.left()
            y = rect.bottom() + 6

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


def _criticality_chip(criticality: Criticality, parent: QtWidgets.QWidget) -> QtWidgets.QLabel:
    """Small Low / Medium / High / Critical tag, matching the rule cards."""
    chip = QtWidgets.QLabel(criticality.label, parent)
    chip.setToolTip(f"Criticality: {criticality.label}")
    critical = criticality is Criticality.CRITICAL
    color = "color: #d9534f;" if critical else ""
    border = "#d9534f" if critical else "rgba(127, 127, 127, 140)"
    chip.setStyleSheet(
        f"QLabel {{ {color} border: 1px solid {border}; border-radius: 3px;"
        " padding: 0px 4px; font-size: 10px; background: transparent; }"
    )
    chip.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
    return chip
