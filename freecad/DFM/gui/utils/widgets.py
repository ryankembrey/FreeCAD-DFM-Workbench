# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Reusable widgets and widget factories."""

from PySide6 import QtCore, QtGui, QtWidgets


class ToleranceSpinBox(QtWidgets.QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDecimals(7)
        self.setRange(1e-7, 1e-1)

    def textFromValue(self, value: float) -> str:
        return f"{value:.0e}".replace("e-0", "e-")

    def valueFromText(self, text: str) -> float:
        try:
            return float(text)
        except ValueError:
            return self.value()

    def stepBy(self, steps: int) -> None:
        val = self.value()
        if steps > 0:
            val *= 10
        elif steps < 0:
            val /= 10

        val = max(self.minimum(), min(self.maximum(), val))
        self.setValue(val)


# Two arrows, one down and one up: "reverse this direction". COLOR is
# replaced with the theme's text color.
FLIP_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none" stroke="COLOR"'
    b' stroke-width="5" stroke-linecap="round" stroke-linejoin="round">'
    b'<path d="M16 12V50M6 40l10 12 10-12"/><path d="M48 52V14M38 24l10-12 10 12"/></svg>'
)

_FLIP_BUTTON_STYLE = (
    "QToolButton { border: none; background: transparent; padding: 0px; margin: 0px; }"
    "QToolButton:hover { background: rgba(127, 127, 127, 40); border-radius: 3px; }"
)


def make_flip_button(
    on_click,
    tooltip: str = "Flip direction 180 degrees",
    size: tuple = (28, 28),
    enabled: bool = True,
) -> QtWidgets.QToolButton:
    """Flat icon-only button that flips a direction 180 degrees.

    ``on_click`` is connected to ``clicked``; wrap it in a lambda if it must not receive
    the ``checked`` argument.
    """
    from ..rule_cards import apply_svg_icon  # here, to avoid an import cycle

    btn = QtWidgets.QToolButton()
    btn.setToolTip(tooltip)
    btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
    btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
    # Monochrome, in the theme's text color.
    apply_svg_icon(btn, FLIP_SVG, 18)
    btn.setFixedSize(*size)
    btn.setStyleSheet(_FLIP_BUTTON_STYLE)
    btn.setEnabled(enabled)
    btn.clicked.connect(on_click)
    return btn
