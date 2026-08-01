# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""FEM-style contour legend that floats over the 3D view.

No background panel: the color bar and haloed labels sit directly on the
viewport. The bar spans the measure's domain; the gradient is mapped to the
active [low, high] window, whose ends are draggable handles (the range control).
Right-click for colormap / bands / orientation / fit-to-data; double-click a
handle to type its value. Works vertical or horizontal. The widget is a child of
the view, movable by its body and resizable from the bottom-right corner.
"""

import math

from PySide6 import QtCore, QtGui, QtWidgets

from ...contour.colormap import value_to_color, COLORMAPS


_RESIZE_ZONE = 16
_HANDLE_GRAB = 9
_BAR_W = 18

_BAND_OPTIONS = [
    ("Smooth", 0.0),
    ("1 unit bands", 1.0),
    ("2 unit bands", 2.0),
    ("5 unit bands", 5.0),
    ("10 unit bands", 10.0),
]


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
    colormapChanged = QtCore.Signal(str)
    bandsChanged = QtCore.Signal(str)
    fitRequested = QtCore.Signal()

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
        self._horizontal = False

        self._drag_mode = None
        self._press_global = None
        self._start_pos = None
        self._start_size = None

        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.DefaultContextMenu)
        self._apply_min_size()
        self.resize(150, 280)
        if parent is not None:
            parent.installEventFilter(self)
        self._place_top_right()

    def orientation_horizontal(self):
        return self._horizontal

    def set_orientation(self, horizontal):
        horizontal = bool(horizontal)
        if horizontal != self._horizontal:
            self._horizontal = horizontal
            self._apply_min_size()
            self.update()

    # ---- API -------------------------------------------------------------

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

    # ---- geometry (orientation-aware) ------------------------------------

    def _apply_min_size(self):
        if self._horizontal:
            self.setMinimumSize(180, 84)
        else:
            self.setMinimumSize(90, 160)

    def _bar_rect(self):
        if self._horizontal:
            left, top, right_pad, bottom = 14, 34, 14, 26
            return QtCore.QRect(left, top, max(2, self.width() - left - right_pad), _BAR_W)
        top, bottom_pad = 30, 12
        return QtCore.QRect(10, top, _BAR_W, max(2, self.height() - top - bottom_pad))

    def _axis_len(self, bar):
        return bar.width() if self._horizontal else bar.height()

    def _value_to_pos(self, value, bar):
        span = self._dom_hi - self._dom_lo
        frac = (value - self._dom_lo) / span if span else 0.0
        frac = max(0.0, min(1.0, frac))
        if self._horizontal:
            return bar.left() + frac * bar.width()
        return bar.bottom() - frac * bar.height()

    def _pos_to_value(self, coord, bar):
        span = self._dom_hi - self._dom_lo
        if self._horizontal:
            frac = (coord - bar.left()) / bar.width() if bar.width() else 0.0
        else:
            frac = (bar.bottom() - coord) / bar.height() if bar.height() else 0.0
        frac = max(0.0, min(1.0, frac))
        return self._dom_lo + frac * span

    def _handle_hit(self, which, pos, bar):
        value = self._low if which == "low" else self._high
        c = self._value_to_pos(value, bar)
        if self._horizontal:
            return abs(pos.x() - c) <= _HANDLE_GRAB and (bar.top() - 14) <= pos.y() <= (
                bar.bottom() + 4
            )
        return abs(pos.y() - c) <= _HANDLE_GRAB and (bar.left() - 14) <= pos.x() <= (
            bar.right() + 4
        )

    def _which_handle(self, pos, bar):
        near_low = self._handle_hit("low", pos, bar)
        near_high = self._handle_hit("high", pos, bar)
        if not (near_low or near_high):
            return None
        if near_low and near_high:
            cl = self._value_to_pos(self._low, bar)
            ch = self._value_to_pos(self._high, bar)
            here = pos.x() if self._horizontal else pos.y()
            return "low" if abs(here - cl) <= abs(here - ch) else "high"
        return "low" if near_low else "high"

    # ---- placement -------------------------------------------------------

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

    # ---- interaction -----------------------------------------------------

    @staticmethod
    def _global(event):
        return event.globalPosition().toPoint()

    @staticmethod
    def _localpt(event):
        return event.position().toPoint()

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        self._press_global = self._global(event)
        self._start_pos = self.pos()
        self._start_size = self.size()
        pos = self._localpt(event)

        if pos.x() >= self.width() - _RESIZE_ZONE and pos.y() >= self.height() - _RESIZE_ZONE:
            self._drag_mode = "resize"
            return

        bar = self._bar_rect()
        which = self._which_handle(pos, bar)
        if which is not None:
            self._drag_mode = which
            return

        self._drag_mode = "move"

    def mouseMoveEvent(self, event):
        if self._drag_mode is None:
            return
        if self._drag_mode in ("low", "high"):
            bar = self._bar_rect()
            pt = self._localpt(event)
            coord = pt.x() if self._horizontal else pt.y()
            value = round(self._pos_to_value(coord, bar))
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
            new_w = max(self.minimumWidth(), self._start_size.width() + delta.x())
            new_h = max(self.minimumHeight(), self._start_size.height() + delta.y())
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

    def mouseDoubleClickEvent(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        bar = self._bar_rect()
        which = self._which_handle(self._localpt(event), bar)
        if which is None:
            return
        current = self._low if which == "low" else self._high
        label = "Lower bound:" if which == "low" else "Upper bound:"
        value, ok = QtWidgets.QInputDialog.getDouble(
            self,
            "Set color range",
            label,
            float(current),
            float(self._dom_lo),
            float(self._dom_hi),
            3,
        )
        if not ok:
            return
        gap = max((self._dom_hi - self._dom_lo) * 0.02, 1.0)
        if which == "low":
            self._low = min(value, self._high - gap)
        else:
            self._high = max(value, self._low + gap)
        self.update()
        self.rangeChanged.emit(self._low, self._high)

    def contextMenuEvent(self, event):
        menu = QtWidgets.QMenu(self)

        cmap_menu = menu.addMenu("Color Map")
        cmap_group = QtGui.QActionGroup(cmap_menu)
        cmap_group.setExclusive(True)
        for name in COLORMAPS.keys():
            act = cmap_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == self._colormap)
            cmap_group.addAction(act)
            act.triggered.connect(lambda _c=False, n=name: self.colormapChanged.emit(n))

        bands_menu = menu.addMenu("Bands")
        bands_group = QtGui.QActionGroup(bands_menu)
        bands_group.setExclusive(True)
        for name, step in _BAND_OPTIONS:
            act = bands_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(abs(step - self._band) < 1e-9)
            bands_group.addAction(act)
            act.triggered.connect(lambda _c=False, n=name: self.bandsChanged.emit(n))

        menu.addSeparator()
        orient = menu.addAction("Horizontal")
        orient.setCheckable(True)
        orient.setChecked(self._horizontal)
        orient.triggered.connect(self._toggle_orientation)

        menu.addAction("Fit To Data", self.fitRequested.emit)
        menu.exec(event.globalPos())

    def _toggle_orientation(self):
        self._horizontal = not self._horizontal
        self._apply_min_size()
        # Swap the footprint so the bar keeps a sensible aspect.
        self.resize(self.height(), self.width())
        self._clamp_into_parent()
        self.update()

    # ---- painting --------------------------------------------------------

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

        # Color bar, mapped to the active window (flat outside it).
        if self._horizontal:
            for i in range(bar.width()):
                value = self._pos_to_value(bar.left() + i, bar)
                r, g, b = value_to_color(value, self._low, self._high, self._colormap, self._band)
                p.fillRect(
                    bar.left() + i,
                    bar.top(),
                    1,
                    bar.height(),
                    QtGui.QColor(int(r * 255), int(g * 255), int(b * 255)),
                )
        else:
            for i in range(bar.height()):
                value = self._pos_to_value(bar.top() + i, bar)
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
            c = int(self._value_to_pos(value, bar))
            label = f"{value:.{dec}f}"
            if float(label) == 0.0:
                label = f"{0:.{dec}f}"
            p.setPen(QtGui.QColor(20, 20, 20, 220))
            if self._horizontal:
                p.drawLine(c, bar.bottom(), c, bar.bottom() + 4)
                w = fm.horizontalAdvance(label)
                self._halo_text(p, c - w / 2, bar.bottom() + 6 + fm.ascent(), label)
            else:
                p.drawLine(bar.right(), c, bar.right() + 4, c)
                self._halo_text(p, bar.right() + 8, c + fm.ascent() // 2 - 1, label)

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
            value = self._low if which == "low" else self._high
            c = int(self._value_to_pos(value, bar))
            p.setPen(QtGui.QColor(15, 15, 15, 230))
            if self._horizontal:
                p.drawLine(c, bar.top(), c, bar.bottom())
                tri = QtGui.QPolygon(
                    [
                        QtCore.QPoint(c, bar.top() - 2),
                        QtCore.QPoint(c - 5, bar.top() - 10),
                        QtCore.QPoint(c + 5, bar.top() - 10),
                    ]
                )
            else:
                p.drawLine(bar.left(), c, bar.right(), c)
                tri = QtGui.QPolygon(
                    [
                        QtCore.QPoint(bar.left() - 2, c),
                        QtCore.QPoint(bar.left() - 10, c - 5),
                        QtCore.QPoint(bar.left() - 10, c + 5),
                    ]
                )
            p.setBrush(QtGui.QColor(245, 245, 245))
            p.setPen(QtGui.QPen(QtGui.QColor(15, 15, 15), 1))
            p.drawPolygon(tri)

    def _paint_marker(self, p, bar):
        if self._marker is None:
            return
        c = int(self._value_to_pos(self._marker, bar))
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 2))
        if self._horizontal:
            p.drawLine(c, bar.top(), c, bar.bottom())
            tri = QtGui.QPolygon(
                [
                    QtCore.QPoint(c, bar.bottom() + 2),
                    QtCore.QPoint(c - 4, bar.bottom() + 8),
                    QtCore.QPoint(c + 4, bar.bottom() + 8),
                ]
            )
        else:
            p.drawLine(bar.left(), c, bar.right(), c)
            tri = QtGui.QPolygon(
                [
                    QtCore.QPoint(bar.right() + 2, c),
                    QtCore.QPoint(bar.right() + 8, c - 4),
                    QtCore.QPoint(bar.right() + 8, c + 4),
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
