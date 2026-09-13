# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


import math

from PySide6 import QtCore, QtGui, QtWidgets

from ...app.contour.colormap import value_to_color, COLORMAPS


_RESIZE_ZONE = 16
_HANDLE_GRAB = 9
_BAR_W = 18

_H_PAD_ALONG = 28  # horizontal
_V_PAD_ALONG = 42  # vertical

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
    orientationChanged = QtCore.Signal(bool)  # emits horizontal=True/False

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
        self._text_rgb = (1.0, 1.0, 1.0)

        self._drag_mode = None
        self._press_global = None
        self._start_pos = None
        self._start_size = None
        self._hovered = False
        self._resize_edges_active = set()

        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.DefaultContextMenu)
        self._apply_min_size()
        self.resize(150, 280)
        if parent is not None:
            parent.installEventFilter(self)
        self._place_top_right()

    def enterEvent(self, _event):
        self._hovered = True
        self.update()

    def leaveEvent(self, _event):
        self._hovered = False
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self.update()

    def orientation_horizontal(self):
        return self._horizontal

    def set_orientation(self, horizontal):
        self.set_orientation_animated(horizontal)

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

    def _title_text(self):
        return f"{self._title} ({self._unit})" if self._unit else self._title

    def _title_min_width(self):
        font = QtGui.QFont(self.font())
        font.setPointSize(12)
        w = QtGui.QFontMetrics(font).horizontalAdvance(self._title_text())
        return int(8 + w + 8)  # left x-offset + text + right margin

    def _apply_min_size(self):
        if self._horizontal:
            self.setMinimumSize(180, 84)
        else:
            # Never narrower than the title needs, so it can't be cut off.
            self.setMinimumSize(max(90, self._title_min_width()), 160)

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

    def _resize_edges(self, pos):
        edges = set()
        if pos.x() <= _RESIZE_ZONE:
            edges.add("left")
        elif pos.x() >= self.width() - _RESIZE_ZONE:
            edges.add("right")
        if pos.y() <= _RESIZE_ZONE:
            edges.add("top")
        elif pos.y() >= self.height() - _RESIZE_ZONE:
            edges.add("bottom")
        return edges

    @staticmethod
    def _edge_cursor(edges):
        if edges in ({"left", "top"}, {"right", "bottom"}):
            return QtCore.Qt.CursorShape.SizeFDiagCursor
        if edges in ({"right", "top"}, {"left", "bottom"}):
            return QtCore.Qt.CursorShape.SizeBDiagCursor
        if "left" in edges or "right" in edges:
            return QtCore.Qt.CursorShape.SizeHorCursor
        return QtCore.Qt.CursorShape.SizeVerCursor

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        self._press_global = self._global(event)
        self._start_pos = self.pos()
        self._start_size = self.size()
        pos = self._localpt(event)

        bar = self._bar_rect()
        which = self._which_handle(pos, bar)
        if which is not None:
            self._drag_mode = which
            return

        edges = self._resize_edges(pos)
        if edges:
            self._drag_mode = "resize"
            self._resize_edges_active = edges
            return

        self._drag_mode = "move"

    def _update_hover_cursor(self, pos):
        bar = self._bar_rect()
        if self._which_handle(pos, bar) is not None:
            self.setCursor(
                QtCore.Qt.CursorShape.SplitHCursor
                if self._horizontal
                else QtCore.Qt.CursorShape.SplitVCursor
            )
            self.setToolTip("Drag handles to adjust range, or double-click to set exact values.")
            return

        self.setToolTip("")
        edges = self._resize_edges(pos)
        if edges:
            self.setCursor(self._edge_cursor(edges))
            return
        self.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)

    def mouseMoveEvent(self, event):
        if self._drag_mode is None:
            self._update_hover_cursor(self._localpt(event))
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
            self._resize_by_edges(delta, parent)
        elif self._drag_mode == "move":
            new_pos = self._start_pos + delta
            if parent is not None:
                new_pos.setX(min(max(new_pos.x(), 0), max(0, parent.width() - self.width())))
                new_pos.setY(min(max(new_pos.y(), 0), max(0, parent.height() - self.height())))
            self.move(new_pos)

    def mouseReleaseEvent(self, _event):
        self._drag_mode = None
        self._resize_edges_active = set()

    def _resize_by_edges(self, delta, parent):
        edges = getattr(self, "_resize_edges_active", set())
        if not edges:
            return
        x0, y0 = self._start_pos.x(), self._start_pos.y()
        w0, h0 = self._start_size.width(), self._start_size.height()
        right0, bottom0 = x0 + w0, y0 + h0
        min_w, min_h = self.minimumWidth(), self.minimumHeight()

        left, top, right, bottom = x0, y0, right0, bottom0

        if "right" in edges:
            right = x0 + w0 + delta.x()
            if parent is not None:
                right = min(right, parent.width())
            right = max(right, x0 + min_w)
        if "bottom" in edges:
            bottom = y0 + h0 + delta.y()
            if parent is not None:
                bottom = min(bottom, parent.height())
            bottom = max(bottom, y0 + min_h)
        if "left" in edges:
            left = x0 + delta.x()
            left = max(left, 0)
            left = min(left, right0 - min_w)
        if "top" in edges:
            top = y0 + delta.y()
            top = max(top, 0)
            top = min(top, bottom0 - min_h)

        self.setGeometry(int(left), int(top), int(right - left), int(bottom - top))

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
        self.set_orientation_animated(not self._horizontal)
        self.orientationChanged.emit(self._horizontal)

    def set_orientation_animated(self, horizontal):
        horizontal = bool(horizontal)
        if horizontal == self._horizontal:
            return
        bar = self._bar_rect()
        bar_len = bar.width() if self._horizontal else bar.height()

        self._horizontal = horizontal
        self._apply_min_size()
        if horizontal:
            new_w = max(self.minimumWidth(), bar_len + _H_PAD_ALONG)
            new_h = self.minimumHeight()
        else:
            new_h = max(self.minimumHeight(), bar_len + _V_PAD_ALONG)
            new_w = self.minimumWidth()
        self.resize(new_w, new_h)
        self._clamp_into_parent()
        self.update()

    def set_text_color(self, rgb):
        self._text_rgb = (float(rgb[0]), float(rgb[1]), float(rgb[2]))
        self.update()

    def _text_qcolor(self):
        r, g, b = self._text_rgb
        return QtGui.QColor(int(r * 255), int(g * 255), int(b * 255))

    def _halo_text(self, p, x, baseline, text):
        p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
        path = QtGui.QPainterPath()
        path.addText(float(x), float(baseline), p.font(), text)

        fill = self._text_qcolor()
        lum = (0.2126 * fill.redF()) + (0.7152 * fill.greenF()) + (0.0722 * fill.blueF())
        outline = QtGui.QColor(255, 255, 255, 60) if lum < 0.5 else QtGui.QColor(0, 0, 0, 60)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.setPen(
            QtGui.QPen(
                outline,
                1.2,
                QtCore.Qt.PenStyle.SolidLine,
                QtCore.Qt.PenCapStyle.RoundCap,
                QtCore.Qt.PenJoinStyle.RoundJoin,
            )
        )
        p.drawPath(path)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawPath(path)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        bar = self._bar_rect()

        if self._hovered:
            self._paint_hover_chrome(p)

        tick_font = QtGui.QFont(p.font())
        tick_font.setPointSize(11)
        title_font = QtGui.QFont(tick_font)
        title_font.setPointSize(12)

        p.setFont(title_font)
        title = f"{self._title} ({self._unit})" if self._unit else self._title
        self._halo_text(p, 8, 20, title)

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

        self._paint_segment_dividers(p, bar)

        p.setPen(QtGui.QPen(QtGui.QColor(30, 30, 30, 235), 1.4))
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawRect(bar)

        p.setFont(tick_font)
        self._paint_ticks(p, bar)
        self._paint_marker(p, bar)
        self._paint_handles(p, bar)
        if self._hovered:
            self._paint_resize_grip(p)

    def _paint_segment_dividers(self, p, bar):
        span = self._dom_hi - self._dom_lo
        if span <= 0:
            return
        step = _nice_step(span / 8.0)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 90), 1.0))
        v = math.ceil(self._dom_lo / step) * step
        guard = step * 0.25
        while v <= self._dom_hi - 1e-9:
            if (v - self._dom_lo) > guard and (self._dom_hi - v) > guard:
                c = int(self._value_to_pos(v, bar))
                if self._horizontal:
                    p.drawLine(c, bar.top() + 1, c, bar.bottom() - 1)
                else:
                    p.drawLine(bar.left() + 1, c, bar.right() - 1, c)
            v += step

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
        # Brighter on hover so it clearly reads as a grabbable corner.
        p.setPen(QtGui.QPen(QtGui.QColor(230, 230, 230, 220), 1.4))
        for off in (3, 7, 11):
            p.drawLine(gx - off, gy, gx, gy - off)

    def _paint_hover_chrome(self, p):
        rect = QtCore.QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0)
        p.setBrush(QtGui.QColor(127, 127, 127, 28))
        p.setPen(QtGui.QPen(QtGui.QColor(200, 200, 200, 130), 1.2))
        p.drawRoundedRect(rect, 6.0, 6.0)

        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(220, 220, 220, 200))
        ox, oy, gap, rdot = 6, 6, 4, 1.4
        for dy in (0, 1):
            for dx in (0, 1):
                p.drawEllipse(QtCore.QPointF(ox + dx * gap, oy + dy * gap), rdot, rdot)
