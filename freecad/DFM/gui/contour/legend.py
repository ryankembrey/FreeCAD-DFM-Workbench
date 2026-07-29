# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


import math

from PySide6 import QtCore, QtGui, QtWidgets

from ...contour.colormap import value_to_color


_RESIZE_ZONE = 16
_HANDLE_GRAB = 9
_MIN_W, _MIN_H = 90, 160
_BAR_W = 18


def _nice_step(raw):
    if raw <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _decimals_for(step):
    if step >= 1:
        return 0
    if step >= 0.1:
        return 1
    return 2


class ContourLegend(QtWidgets.QWidget):
    rangeChanged = QtCore.Signal(float, float)

    def __init__(self, parent):
        super().__init__(parent)
        self._title = "Value"
        self._unit = ""
        self._colormap = "Turbo"
        self._band = 0.0
        self._dom_lo = -1.0
        self._dom_hi = 1.0
        self._low = -1.0
        self._high = 1.0
        self._marker = None

        self._drag_mode = None
        self._press_global = None
        self._start_pos = None
        self._start_size = None

        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(150, 280)
        if parent is not None:
            parent.installEventFilter(self)
        self._place_top_right()

    def configure(
        self, title, unit, colormap, band, dom_lo, dom_hi, low, high, data_min=None, data_max=None
    ):
        self._title, self._unit = title, unit
        self._colormap, self._band = colormap, band
        self._dom_lo, self._dom_hi = dom_lo, dom_hi
        self._low, self._high = low, high
        self.update()

    def set_style(self, colormap, band):
        self._colormap, self._band = colormap, band
        self.update()

    def set_range(self, low, high):
        self._low, self._high = low, high
        self.update()

    def low(self):
        return self._low

    def high(self):
        return self._high

    def set_marker(self, value):
        if value != self._marker:
            self._marker = value
            self.update()

    def cleanup(self):
        parent = self.parent()
        if parent is not None:
            try:
                parent.removeEventFilter(self)
            except Exception:
                pass

    def _bar_rect(self):
        top = 30
        bottom_pad = 12
        return QtCore.QRect(10, top, _BAR_W, max(2, self.height() - top - bottom_pad))

    def _value_to_y(self, value, bar):
        span = self._dom_hi - self._dom_lo
        frac = (value - self._dom_lo) / span if span else 0.0
        frac = max(0.0, min(1.0, frac))
        return bar.bottom() - frac * bar.height()

    def _y_to_value(self, y, bar):
        span = self._dom_hi - self._dom_lo
        frac = (bar.bottom() - y) / bar.height() if bar.height() else 0.0
        frac = max(0.0, min(1.0, frac))
        return self._dom_lo + frac * span

    def _handle_hit(self, which, pos, bar):
        y = self._value_to_y(self._low if which == "low" else self._high, bar)
        return abs(pos.y() - y) <= _HANDLE_GRAB and (bar.left() - 14) <= pos.x() <= (
            bar.right() + 4
        )

    def _place_top_right(self):
        parent = self.parent()
        if parent is not None:
            self.move(max(8, parent.width() - self.width() - 16), 16)

    def _clamp_into_parent(self):
        parent = self.parent()
        if parent is None:
            return
        self.move(
            min(max(self.x(), 0), max(0, parent.width() - self.width())),
            min(max(self.y(), 0), max(0, parent.height() - self.height())),
        )

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() == QtCore.QEvent.Type.Resize:
            self._clamp_into_parent()
        return False

    @staticmethod
    def _global(event):
        return event.globalPosition().toPoint()

    @staticmethod
    def _localpt(event):
        return event.position().toPoint()

    def mousePressEvent(self, event):
        self._press_global = self._global(event)
        self._start_pos = self.pos()
        self._start_size = self.size()
        pos = self._localpt(event)

        if pos.x() >= self.width() - _RESIZE_ZONE and pos.y() >= self.height() - _RESIZE_ZONE:
            self._drag_mode = "resize"
            return

        bar = self._bar_rect()
        near_low = self._handle_hit("low", pos, bar)
        near_high = self._handle_hit("high", pos, bar)
        if near_low or near_high:
            if near_low and near_high:
                yl = self._value_to_y(self._low, bar)
                yh = self._value_to_y(self._high, bar)
                self._drag_mode = "low" if abs(pos.y() - yl) <= abs(pos.y() - yh) else "high"
            else:
                self._drag_mode = "low" if near_low else "high"
            return

        self._drag_mode = "move"

    def mouseMoveEvent(self, event):
        if self._drag_mode is None:
            return
        if self._drag_mode in ("low", "high"):
            bar = self._bar_rect()
            value = round(self._y_to_value(self._localpt(event).y(), bar))
            gap = max((self._dom_hi - self._dom_lo) * 0.02, 1.0)
            if self._drag_mode == "low":
                self._low = min(value, self._high - gap)
            else:
                self._high = max(value, self._low + gap)
            self.update()
            self.rangeChanged.emit(self._low, self._high)
            return

        delta = self._global(event) - self._press_global
        parent = self.parent()
        if self._drag_mode == "resize":
            new_w = max(_MIN_W, self._start_size.width() + delta.x())
            new_h = max(_MIN_H, self._start_size.height() + delta.y())
            if parent is not None:
                new_w = min(new_w, parent.width() - self.x())
                new_h = min(new_h, parent.height() - self.y())
            self.resize(new_w, new_h)
        elif self._drag_mode == "move":
            new_pos = self._start_pos + delta
            if parent is not None:
                new_pos.setX(min(max(new_pos.x(), 0), max(0, parent.width() - self.width())))
                new_pos.setY(min(max(new_pos.y(), 0), max(0, parent.height() - self.height())))
            self.move(new_pos)

    def mouseReleaseEvent(self, _event):
        self._drag_mode = None

    def _halo_text(self, p, x, baseline, text):
        p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
        path = QtGui.QPainterPath()
        path.addText(float(x), float(baseline), p.font(), text)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.setPen(
            QtGui.QPen(
                QtGui.QColor(0, 0, 0, 235),
                2.6,
                QtCore.Qt.PenStyle.SolidLine,
                QtCore.Qt.PenCapStyle.RoundCap,
                QtCore.Qt.PenJoinStyle.RoundJoin,
            )
        )
        p.drawPath(path)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(245, 245, 245))
        p.drawPath(path)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        bar = self._bar_rect()

        tick_font = QtGui.QFont(p.font())
        tick_font.setPointSize(11)
        title_font = QtGui.QFont(tick_font)
        title_font.setPointSize(12)
        title_font.setBold(True)

        p.setFont(title_font)
        title = f"{self._title} ({self._unit})" if self._unit else self._title
        self._halo_text(p, 8, 20, title)

        # color bar, mapped to the active window (flat outside it)
        for i in range(bar.height()):
            value = self._y_to_value(bar.top() + i, bar)
            r, g, b = value_to_color(value, self._low, self._high, self._colormap, self._band)
            p.fillRect(
                bar.left(),
                bar.top() + i,
                bar.width(),
                1,
                QtGui.QColor(int(r * 255), int(g * 255), int(b * 255)),
            )
        p.setPen(QtGui.QColor(20, 20, 20, 220))
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawRect(bar)

        p.setFont(tick_font)
        self._paint_ticks(p, bar)
        self._paint_marker(p, bar)
        self._paint_handles(p, bar)
        self._paint_resize_grip(p)

    def _paint_ticks(self, p, bar):
        fm = QtGui.QFontMetrics(p.font())
        span = self._dom_hi - self._dom_lo
        step = _nice_step(span / 8.0)
        dec = _decimals_for(step)

        def draw(value):
            y = int(self._value_to_y(value, bar))
            p.setPen(QtGui.QColor(20, 20, 20, 220))
            p.drawLine(bar.right(), y, bar.right() + 4, y)
            label = f"{value:.{dec}f}"
            if float(label) == 0.0:
                label = f"{0:.{dec}f}"
            self._halo_text(p, bar.right() + 8, y + fm.ascent() // 2 - 1, label)

        draw(self._dom_lo)
        draw(self._dom_hi)
        guard = step * 0.4
        v = math.ceil(self._dom_lo / step) * step
        while v <= self._dom_hi - 1e-9:
            if (v - self._dom_lo) > guard and (self._dom_hi - v) > guard:
                draw(v)
            v += step

    def _paint_handles(self, p, bar):
        for which in ("low", "high"):
            y = int(self._value_to_y(self._low if which == "low" else self._high, bar))
            p.setPen(QtGui.QColor(15, 15, 15, 230))
            p.drawLine(bar.left(), y, bar.right(), y)
            tri = QtGui.QPolygon(
                [
                    QtCore.QPoint(bar.left() - 2, y),
                    QtCore.QPoint(bar.left() - 10, y - 5),
                    QtCore.QPoint(bar.left() - 10, y + 5),
                ]
            )
            p.setBrush(QtGui.QColor(245, 245, 245))
            p.setPen(QtGui.QPen(QtGui.QColor(15, 15, 15), 1))
            p.drawPolygon(tri)

    def _paint_marker(self, p, bar):
        if self._marker is None:
            return
        y = int(self._value_to_y(self._marker, bar))
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 2))
        p.drawLine(bar.left(), y, bar.right(), y)
        tri = QtGui.QPolygon(
            [
                QtCore.QPoint(bar.right() + 2, y),
                QtCore.QPoint(bar.right() + 8, y - 4),
                QtCore.QPoint(bar.right() + 8, y + 4),
            ]
        )
        p.setBrush(QtGui.QColor(30, 30, 30))
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 1))
        p.drawPolygon(tri)

    def _paint_resize_grip(self, p):
        gx, gy = self.width() - 4, self.height() - 4
        p.setPen(QtGui.QPen(QtGui.QColor(150, 150, 150, 170), 1))
        for off in (3, 7, 11):
            p.drawLine(gx - off, gy, gx, gy - off)
