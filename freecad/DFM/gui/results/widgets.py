# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from PySide6 import QtCore, QtGui, QtWidgets


class DFMSparkline(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.error_data = []
        self.warning_data = []
        self.success_data = []
        self.run_labels = []
        self.setFixedHeight(130)

    def set_data(self, errors, warnings, successes, run_nums):
        self.error_data = errors
        self.warning_data = warnings
        self.success_data = successes
        self.run_labels = [f"Run {n}" for n in run_nums]
        self.update()

    def paintEvent(self, _event):
        if not self.error_data:
            return

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = self.contentsRect()

        p_top, p_bottom, p_left, p_right = 25, 25, 35, 15
        w = rect.width() - p_left - p_right
        h = rect.height() - p_top - p_bottom

        all_series = [self.error_data, self.warning_data, self.success_data]
        raw_max = max([max(s) if s else 0 for s in all_series] + [5])
        max_val = int(((raw_max // 5) + 1) * 5)

        num_points = len(self.error_data)
        x_step = w / (num_points - 1) if num_points > 1 else 0

        # Y-axis and grid
        font = painter.font()
        font.setPointSizeF(6.5)
        painter.setFont(font)

        intervals = 4
        for i in range(intervals + 1):
            val = int((max_val / intervals) * i)
            y = int(rect.height() - p_bottom - (val / max_val * h))

            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 15), 1))
            painter.drawLine(p_left, y, rect.width() - p_right, y)

            painter.setPen(QtGui.QColor("#666666"))
            painter.drawText(2, y + 4, str(val).rjust(3))

        # X-axis labels
        for i, label in enumerate(self.run_labels):
            x = int(p_left + (i * x_step))
            painter.setPen(QtGui.QColor("#666666"))
            if num_points < 6 or i % 2 == 0 or i == num_points - 1:
                painter.drawText(x - 15, int(rect.height() - 5), label)

        def draw_series(data, color_hex):
            if not data or max(data) == 0:
                return
            color = QtGui.QColor(color_hex)
            pts = [
                QtCore.QPointF(p_left + (i * x_step), rect.height() - p_bottom - (v / max_val * h))
                for i, v in enumerate(data)
            ]

            path = QtGui.QPainterPath()
            path.moveTo(pts[0].x(), rect.height() - p_bottom)
            for p in pts:
                path.lineTo(p)
            path.lineTo(pts[-1].x(), rect.height() - p_bottom)

            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            fill = QtGui.QColor(color)
            fill.setAlpha(35)
            painter.setBrush(fill)
            painter.drawPath(path)

            painter.setPen(QtGui.QPen(color, 1.5))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            line_path = QtGui.QPainterPath()
            line_path.moveTo(pts[0])
            for p in pts[1:]:
                line_path.lineTo(p)
            painter.drawPath(line_path)

            painter.setBrush(color)
            for p in pts:
                painter.drawEllipse(p, 2, 2)

        draw_series(self.success_data, "#639922")
        draw_series(self.warning_data, "#D4900A")
        draw_series(self.error_data, "#E24B4A")
