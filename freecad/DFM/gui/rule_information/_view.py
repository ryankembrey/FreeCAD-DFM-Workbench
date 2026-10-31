# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Embeddable widget showing a rule's brief, diagram and target / limit legend.

``RuleDiagramView`` is the building block for every place that explains a
rule: the rule editing dialog embeds it directly, and ``RuleInfoPanel`` wraps
it in a popup.
"""

import html
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ...core.processes.process import RuleLimit
from ...core.rules import Rulebook, SHAPE_FIELD_ATTRS
from ._diagrams import DIAGRAMS
from ._renderer import DEFAULT_WIDTH, render_diagram

_LEGEND_ACCENT = "#5b9bd5"
_BRIEF_ALPHA = 0.65


class RuleDiagramView(QtWidgets.QWidget):
    """Brief description, bad / good diagram and target / limit legend.

    Parameters
    ----------
    rule:
        The Rulebook member to illustrate.
    limit:
        Values to show in the legend. ``None`` shows a dash for each value.
        Binary rules have no legend.
    show_brief:
        Whether to show the one-line description above the diagram.
    show_legend:
        Whether to show the target / limit legend below the diagram. Turn it
        off when the values are shown elsewhere, for example as inputs.
    parent:
        Parent widget.

    The diagram is rendered when the widget is shown and again whenever the
    style or palette changes, so its colours always match the theme the
    widget is drawn in, wherever it is embedded.
    """

    def __init__(
        self,
        rule: Rulebook,
        limit: Optional[RuleLimit] = None,
        show_brief: bool = True,
        show_legend: bool = True,
        parent: Optional[QtWidgets.QWidget] = None,
    ):
        super().__init__(parent)
        self._rule = rule
        self._limit = limit
        self._brief: Optional[QtWidgets.QLabel] = None
        self._image: Optional[QtWidgets.QLabel] = None
        self._value_labels: dict[str, QtWidgets.QLabel] = {}
        self._rendering = False

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        diag = DIAGRAMS.get(rule)
        if diag is not None and show_brief:
            self._brief = QtWidgets.QLabel(diag.brief, self)
            self._brief.setWordWrap(True)
            root.addWidget(self._brief)

        if diag is not None:
            self._image = QtWidgets.QLabel(self)
            self._image.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self._image.setMinimumSize(DEFAULT_WIDTH, int(DEFAULT_WIDTH * 165 / 340))
            root.addWidget(self._image)

        if show_legend and not rule.is_binary:
            self._build_legend(root)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_limit(self, limit: Optional[RuleLimit]) -> None:
        """Show the values from *limit* in the legend."""
        self._limit = limit
        for attr, lbl in self._value_labels.items():
            lbl.setText(self._value_text(attr))

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_legend(self, parent_layout: QtWidgets.QVBoxLayout) -> None:
        attrs = SHAPE_FIELD_ATTRS.get(self._rule.shape, ())
        labels = self._rule.field_labels
        if not attrs or not labels:
            return

        sep = QtWidgets.QFrame(self)
        sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        sep.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        parent_layout.addWidget(sep)

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)

        for row, (attr, label_text) in enumerate(zip(attrs, labels)):
            lbl = QtWidgets.QLabel(label_text, self)
            font = lbl.font()
            font.setBold(True)
            lbl.setFont(font)
            grid.addWidget(lbl, row, 0)

            # Rich text colour, because a theme stylesheet's ``color`` would
            # override a palette colour on the label.
            val = QtWidgets.QLabel(self)
            val.setTextFormat(QtCore.Qt.TextFormat.RichText)
            val.setText(self._value_text(attr))
            grid.addWidget(val, row, 1)
            self._value_labels[attr] = val

        # Keep both columns at their natural width so values sit beside
        # their labels; the empty third column takes the spare width.
        grid.setColumnStretch(2, 1)
        parent_layout.addLayout(grid)

    def _value_text(self, attr: str) -> str:
        value = str(getattr(self._limit, attr, "") or "") if self._limit else ""
        text = f"{value} {self._rule.unit or ''}".strip() if value else "—"
        return f'<span style="color:{_LEGEND_ACCENT}">{html.escape(text)}</span>'

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        self._apply_theme()
        super().showEvent(event)

    def changeEvent(self, event: QtCore.QEvent) -> None:
        if event.type() in (
            QtCore.QEvent.Type.StyleChange,
            QtCore.QEvent.Type.PaletteChange,
        ) and self.isVisible():
            self._apply_theme()
        super().changeEvent(event)

    def _apply_theme(self) -> None:
        """Render the diagram and tint the brief for the current theme."""
        if self._rendering:
            return
        self._rendering = True
        try:
            pal = self._drawn_palette()
            if self._brief is not None:
                colour = QtGui.QColor(pal.color(QtGui.QPalette.ColorRole.WindowText))
                colour.setAlphaF(_BRIEF_ALPHA)
                brief_pal = self._brief.palette()
                brief_pal.setColor(QtGui.QPalette.ColorRole.WindowText, colour)
                self._brief.setPalette(brief_pal)
            if self._image is not None:
                pixmap = render_diagram(self._rule, pal, DEFAULT_WIDTH)
                if pixmap is not None:
                    self._image.setPixmap(pixmap)
        finally:
            self._rendering = False

    def _drawn_palette(self) -> QtGui.QPalette:
        """Return a palette holding the colours this widget is drawn with.

        FreeCAD themes are stylesheets, and a stylesheet only reaches a
        widget's palette once the widget is polished. Polishing first makes
        the background and text colours match what is actually drawn. The
        text colour is read from a label, since stylesheets usually set the
        text colour on labels rather than on plain widgets.
        """
        role = QtGui.QPalette.ColorRole
        self.ensurePolished()

        # The background is drawn by the top-level window (dialog or popup).
        window = self.window()
        window.ensurePolished()
        pal = QtGui.QPalette(self.palette())
        pal.setColor(role.Window, window.palette().color(role.Window))

        # Our own labels carry custom colours, so read the text colour from
        # a fresh, unstyled label instead.
        probe = QtWidgets.QLabel(self)
        probe.ensurePolished()
        pal.setColor(role.WindowText, probe.palette().color(role.WindowText))
        probe.deleteLater()
        return pal
