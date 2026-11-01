# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Compact error / warning trend chart for the History tab."""

from PySide6 import QtCore, QtGui, QtWidgets

from .common import C_ACCENT, C_ERROR, C_WARNING


class TrendChart(QtWidgets.QWidget):
    """Errors and warnings per run, with the two compared runs shaded.

    Axis labels and grid lines use the theme's text color (faded), so the
    chart reads in light and dark themes.
    """

    HEIGHT = 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self._runs: list[int] = []
        self._errors: list[int] = []
        self._warnings: list[int] = []
        self._compared: tuple = ()

    def set_data(self, runs: list[int], errors: list[int], warnings: list[int]) -> None:
        self._runs, self._errors, self._warnings = list(runs), list(errors), list(warnings)
        self.update()

    def set_compared(self, run_a, run_b) -> None:
        """Shade the span between the two compared runs (when shown)."""
        self._compared = (run_a, run_b)
        self.update()

    # ------------------------------------------------------------------

    def _text_color(self) -> QtGui.QColor:
        self.ensurePolished()
        return QtGui.QColor(self.palette().color(QtGui.QPalette.ColorRole.WindowText))

    def paintEvent(self, _event) -> None:
        if not self._runs:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        left, right, top, bottom = 22, 8, 8, 18
        w = self.width() - left - right
        h = self.height() - top - bottom
        n = len(self._runs)
        peak = max(self._errors + self._warnings + [1])
        top_value = max(5, ((peak + 4) // 5) * 5)

        def x(i: int) -> float:
            return left + (w * i / (n - 1) if n > 1 else w / 2)

        def y(v: float) -> float:
            return top + h * (1 - v / top_value)

        text = self._text_color()
        grid = QtGui.QColor(text)
        grid.setAlphaF(0.18)
        label = QtGui.QColor(text)
        label.setAlphaF(0.6)
        font = QtGui.QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * 0.75)
        p.setFont(font)

        # Grid and value labels.
        for step in range(4):
            value = top_value * step / 3
            yy = y(value)
            p.setPen(QtGui.QPen(grid, 1))
            p.drawLine(QtCore.QPointF(left, yy), QtCore.QPointF(left + w, yy))
            p.setPen(label)
            p.drawText(
                QtCore.QRectF(0, yy - 7, left - 5, 14),
                int(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter),
                f"{value:g}",
            )

        # Shade the compared runs.
        shown = [self._runs.index(r) for r in self._compared if r in self._runs]
        if shown:
            a, b = min(shown), max(shown)
            shade = QtGui.QColor(C_ACCENT)
            shade.setAlphaF(0.14)
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(shade)
            p.drawRoundedRect(QtCore.QRectF(x(a) - 8, top, x(b) - x(a) + 16, h), 3, 3)

        def series(values: list[int], color_hex: str, fill: bool) -> None:
            color = QtGui.QColor(color_hex)
            points = [QtCore.QPointF(x(i), y(v)) for i, v in enumerate(values)]
            if fill and n > 1:
                area = QtGui.QPainterPath(QtCore.QPointF(points[0].x(), y(0)))
                for pt in points:
                    area.lineTo(pt)
                area.lineTo(points[-1].x(), y(0))
                tint = QtGui.QColor(color)
                tint.setAlphaF(0.14)
                p.setPen(QtCore.Qt.PenStyle.NoPen)
                p.setBrush(tint)
                p.drawPath(area)
            if n > 1:
                line = QtGui.QPainterPath(points[0])
                for pt in points[1:]:
                    line.lineTo(pt)
                p.setPen(QtGui.QPen(color, 2))
                p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                p.drawPath(line)
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(color)
            for pt in points:
                p.drawEllipse(pt, 2.5, 2.5)

        series(self._errors, C_ERROR, fill=True)
        series(self._warnings, C_WARNING, fill=False)

        # Run numbers: every other run, plus the last.
        p.setPen(label)
        for i, run in enumerate(self._runs):
            if n <= 6 or i % 2 == 0 or i == n - 1:
                p.drawText(
                    QtCore.QRectF(x(i) - 16, self.height() - bottom + 3, 32, bottom - 3),
                    int(QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop),
                    str(run),
                )
        p.end()


def legend(*items: tuple[str, str]) -> QtWidgets.QWidget:
    """Row of colored line swatches with labels: legend(("Errors", "#E24B4A"), ...)."""
    w = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(12)
    for text, color in items:
        swatch = QtWidgets.QFrame()
        swatch.setFixedSize(10, 3)
        swatch.setStyleSheet(f"QFrame {{ background: {color}; border: none; border-radius: 1px; }}")
        row.addWidget(swatch)
        lbl = QtWidgets.QLabel(text)
        font = lbl.font()
        font.setPointSizeF(font.pointSizeF() * 0.88)
        lbl.setFont(font)
        row.addWidget(lbl)
        row.addSpacing(4)
    row.addStretch(1)
    return w
