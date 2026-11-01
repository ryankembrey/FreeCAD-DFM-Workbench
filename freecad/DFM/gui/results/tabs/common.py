# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Small building blocks shared by the History and Export tabs.

Colors are either the fixed status colors used across the results panel or
neutral greys with transparency, so every piece reads in light and dark
themes without depending on the theme's palette.
"""

import html
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

C_ERROR = "#E24B4A"
C_WARNING = "#D4900A"
C_PASS = "#639922"
C_ACCENT = "#3d8fd8"

# Neutral tints, readable on light and dark backgrounds.
TINT = "rgba(127, 127, 127, 28)"
LINE = "rgba(127, 127, 127, 110)"
SOFT_LINE = "rgba(127, 127, 127, 45)"


def section_header(text: str, note: str = "") -> QtWidgets.QWidget:
    """Bold heading followed by a rule, matching the panel's "Findings" heading."""
    w = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(w)
    row.setContentsMargins(0, 6, 0, 2)
    row.setSpacing(8)
    title = QtWidgets.QLabel(text)
    font = title.font()
    font.setBold(True)
    title.setFont(font)
    row.addWidget(title)
    if note:
        row.addWidget(muted_label(note, small=True))
    line = QtWidgets.QFrame()
    line.setFixedHeight(2)
    line.setStyleSheet(f"QFrame {{ background: {LINE}; border: none; }}")
    row.addWidget(line, stretch=1)
    return w


class MutedLabel(QtWidgets.QLabel):
    """Secondary text: the theme's text color at reduced strength.

    The color is read from the label's own (stylesheet-aware) palette when it
    is shown and whenever the style changes, and applied as rich text, so it
    follows light and dark themes without overriding the theme's stylesheet.
    """

    OPACITY = 0.65

    def __init__(self, text: str = "", parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._plain = ""
        self.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self._plain = text
        self._render()

    def text(self) -> str:
        return self._plain

    def showEvent(self, event) -> None:
        self._render()
        super().showEvent(event)

    def changeEvent(self, event) -> None:
        if event.type() in (QtCore.QEvent.Type.StyleChange, QtCore.QEvent.Type.PaletteChange):
            self._render()
        super().changeEvent(event)

    def _render(self) -> None:
        self.ensurePolished()
        c = self.palette().color(QtGui.QPalette.ColorRole.WindowText)
        color = f"rgba({c.red()}, {c.green()}, {c.blue()}, {self.OPACITY})"
        super().setText(f'<span style="color: {color}">{html.escape(self._plain)}</span>')


def muted_label(text: str = "", small: bool = False) -> QtWidgets.QLabel:
    """Secondary text in a faded version of the theme's text color."""
    lbl = MutedLabel(text)
    if small:
        font = lbl.font()
        font.setPointSizeF(font.pointSizeF() * 0.88)
        lbl.setFont(font)
    return lbl


def pill(text: str, color: str, parent: Optional[QtWidgets.QWidget] = None) -> QtWidgets.QLabel:
    """Colored status badge such as "Pass", "1 E" or "2 W"."""
    lbl = QtWidgets.QLabel(text, parent)
    lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    lbl.setMinimumWidth(38)
    lbl.setStyleSheet(
        f"QLabel {{ background: {color}; color: white; border-radius: 3px;"
        " padding: 0px 6px; font-size: 11px; }"
    )
    lbl.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
    return lbl


def status_pill(errors: int, warnings: int, parent=None) -> QtWidgets.QLabel:
    """Pill summarising one rule's result in a run."""
    if errors:
        return pill(f"{errors} E", C_ERROR, parent)
    if warnings:
        return pill(f"{warnings} W", C_WARNING, parent)
    return pill("Pass", C_PASS, parent)
