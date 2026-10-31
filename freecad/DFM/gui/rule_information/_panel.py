# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Popup panel that shows a rule's illustration, brief description, and
target / limit legend.

``RuleInfoPanel`` is a lightweight tool tip window intended to appear near
the button that triggered it, typically while the pointer hovers it. It does
not take focus or grab input; the owner closes it when the pointer leaves.
It can be invoked from any context that has a ``Rulebook`` member and an
optional ``RuleLimit``.
"""

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ...core.processes.process import RuleLimit
from ...core.rules import Rulebook, RuleShape, SHAPE_FIELD_ATTRS
from ._diagrams import DIAGRAMS
from ._renderer import DEFAULT_WIDTH, render_diagram

_MARGIN_H = 16
_MARGIN_V = 14


class RuleInfoPanel(QtWidgets.QDialog):
    """Illustrated popup explaining a single DFM rule.

    Parameters
    ----------
    rule:
        The Rulebook member to illustrate.
    material_limit:
        Current RuleLimit (may be ``None`` for rules not active on the
        selected material).
    parent:
        Parent widget — used for palette inheritance.
    """

    def __init__(
        self,
        rule: Rulebook,
        material_limit: Optional[RuleLimit] = None,
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

        self._rule = rule
        self._limit = material_limit

        self._build_ui()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(_MARGIN_H, _MARGIN_V, _MARGIN_H, _MARGIN_V)
        root.setSpacing(8)

        # --- Title (bold) ---
        title = QtWidgets.QLabel(self._rule.label, self)
        title_font = title.font()
        title_font.setPointSizeF(title_font.pointSizeF() * 1.15)
        title_font.setBold(True)
        title.setFont(title_font)
        root.addWidget(title)

        pal = self._themed_palette(title)

        # --- Brief description (muted) ---
        diag = DIAGRAMS.get(self._rule)
        if diag is not None:
            brief = QtWidgets.QLabel(diag.brief)
            brief.setWordWrap(True)
            brief_color = pal.color(QtGui.QPalette.ColorRole.WindowText)
            brief_color.setAlphaF(0.65)
            brief_pal = brief.palette()
            brief_pal.setColor(QtGui.QPalette.ColorRole.WindowText, brief_color)
            brief.setPalette(brief_pal)
            root.addWidget(brief)

        # --- SVG illustration (avoid / prefer pair) ---
        pixmap = render_diagram(self._rule, pal, DEFAULT_WIDTH)
        if pixmap is not None:
            img = QtWidgets.QLabel()
            img.setPixmap(pixmap)
            img.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            root.addWidget(img)

        # --- Target / limit legend ---
        if not self._rule.is_binary:
            self._build_legend(root, pal)

        # --- Fixed width: diagram plus margins, so the brief wraps to match ---
        self.setFixedWidth(DEFAULT_WIDTH + 2 * _MARGIN_H)

    def _themed_palette(self, text_widget: QtWidgets.QWidget) -> QtGui.QPalette:
        """Return a palette holding the colours the panel is drawn with.

        FreeCAD themes are stylesheets. A stylesheet only reaches a widget's
        palette once the widget is polished, so reading ``self.palette()``
        straight after construction returns the default (light) palette even
        in a dark theme. Polishing first makes the background and text
        colours match what is actually drawn.
        """
        self.ensurePolished()
        text_widget.ensurePolished()

        role = QtGui.QPalette.ColorRole
        pal = QtGui.QPalette(self.palette())
        pal.setColor(role.WindowText, text_widget.palette().color(role.WindowText))
        return pal

    def _build_legend(
        self,
        parent_layout: QtWidgets.QVBoxLayout,
        pal: QtGui.QPalette,
    ) -> None:
        """Add a small legend showing field labels with current values."""
        attrs = SHAPE_FIELD_ATTRS.get(self._rule.shape, ())
        labels = self._rule.field_labels

        if not attrs or not labels:
            return

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        sep.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        parent_layout.addWidget(sep)

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)

        accent = QtGui.QColor("#5b9bd5")

        for row, (attr, label_text) in enumerate(zip(attrs, labels)):
            # Label (e.g. "Aim for", "At most")
            lbl = QtWidgets.QLabel(label_text)
            lbl_font = lbl.font()
            lbl_font.setBold(True)
            lbl.setFont(lbl_font)
            grid.addWidget(lbl, row, 0)

            # Value + unit
            value_str = self._field_value(attr)
            unit = self._rule.unit or ""
            val_lbl = QtWidgets.QLabel(f"{value_str} {unit}".strip() if value_str else "—")
            val_pal = val_lbl.palette()
            val_pal.setColor(QtGui.QPalette.ColorRole.WindowText, accent)
            val_lbl.setPalette(val_pal)
            grid.addWidget(val_lbl, row, 1)

        # Keep both columns at their natural width so values sit beside
        # their labels; the empty third column takes the spare width.
        grid.setColumnStretch(2, 1)
        parent_layout.addLayout(grid)

    def _field_value(self, attr: str) -> str:
        """Read a field value from the current RuleLimit, if available."""
        if self._limit is None:
            return ""
        return str(getattr(self._limit, attr, ""))

    # ------------------------------------------------------------------
    # Positioning
    # ------------------------------------------------------------------

    def show_near(self, anchor: QtWidgets.QWidget) -> None:
        """Position the panel near *anchor* and show it.

        The panel is placed to the right of the anchor when screen space
        allows; otherwise it falls back to the left or below.
        """
        self.adjustSize()
        screen = self.screen()
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()

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
    # Paint — rounded background that follows palette
    # ------------------------------------------------------------------

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        pal = self.palette()
        bg = pal.color(QtGui.QPalette.ColorRole.Window)
        border = pal.color(QtGui.QPalette.ColorRole.Mid)

        painter.setPen(QtGui.QPen(border, 1.0))
        painter.setBrush(bg)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)
        painter.end()
