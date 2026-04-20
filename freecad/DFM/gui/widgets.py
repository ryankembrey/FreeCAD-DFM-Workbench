# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from PySide6 import QtWidgets


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
