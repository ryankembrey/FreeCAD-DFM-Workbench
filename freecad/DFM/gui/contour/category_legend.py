# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The legend of a categorical contour, such as the undercut tool's release
directions. More than a key, it is how you work with the result:

* the eye on each row hides or shows that category (hidden ones go gray);
* clicking a row shows that category alone, clicking it again shows all;
* the arrows on undercut and side action rows step the camera through
  each region of that category, largest first;
* each row gives the category's share of the surface area, and the row
  under the cursor in the 3D view is highlighted.

Like the gradient legend it has no background: its text is drawn in the
legend text color (black or white from the viewport background, see
document.legend_text_rgb) with a faint outline. Drag it to move it, drag
an edge or corner to resize it (everything scales). It shares the gradient
legend's API, so the contour panel can hold either.
"""

from PySide6 import QtCore, QtGui, QtWidgets

from ...app.contour.colormap import HighlightSpec


# Layout in unscaled units; the widget's size sets the scale.
_PAD = 10
_TITLE_H = 26
_ROW_H = 24
_EYE_W = 20
_SWATCH_W = 22
_GAP = 8
_NAV_W = 76  # room for "‹ 12/34 ›"
_ARROW_W = 16

_EDGE = 6  # resize grab width along the edges
_CORNER = 14  # and at the corners
_MIN_SCALE = 0.6
_MAX_SCALE = 3.0


class CategoryLegend(QtWidgets.QWidget):
    # The gradient legend's signals, so the panel can connect either.
    rangeChanged = QtCore.Signal(float, float)
    colormapChanged = QtCore.Signal(str)
    bandsChanged = QtCore.Signal(str)
    fitRequested = QtCore.Signal()
    orientationChanged = QtCore.Signal(bool)

    # The codes now drawn gray (hidden, or not the soloed one).
    mutedChanged = QtCore.Signal(object)
    # Step the camera through a category's regions: (code, +1 or -1).
    regionRequested = QtCore.Signal(int, int)

    def __init__(self, parent):
        super().__init__(parent)
        self._title = "Categories"
        self._categories = []  # Category objects, in display order
        self._shares = {}  # code -> 0..1
        self._hidden = set()
        self._solo = None
        self._marker_code = None
        self._hover_row = None
        self._hover_part = None
        self._region_pos = {}  # code -> (index, count)
        self._text_rgb = (0.0, 0.0, 0.0)

        self._base_w = 240.0
        self._base_h = 120.0
        self._label_w = 60
        self._pct_w = 40
        self._has_nav = False
        self._scale = 1.0

        self._drag_mode = None  # "move" or "resize"
        self._edges = set()
        self._press_global = None
        self._start_pos = None
        self._start_size = None
        self._hovered = False

        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        if parent is not None:
            parent.installEventFilter(self)

    # -- The gradient legend's API (most of it means nothing here) ---------

    def configure(self, title, *_args, **_kwargs):
        self._title = title
        self._relayout()

    def set_style(self, *_args):
        pass

    def set_highlight(self, _highlight):
        pass

    def set_range(self, *_args):
        pass

    def set_no_value_label(self, _text):
        pass

    def orientation_horizontal(self):
        return False

    def set_orientation(self, _horizontal):
        pass

    def set_orientation_animated(self, _horizontal):
        pass

    def set_marker(self, value):
        code = None if value is None else int(round(value))
        if code != self._marker_code:
            self._marker_code = code
            self.update()

    def set_text_color(self, rgb):
        self._text_rgb = (float(rgb[0]), float(rgb[1]), float(rgb[2]))
        self.update()

    def cleanup(self):
        parent = self.parent()
        if parent is not None:
            try:
                parent.removeEventFilter(self)
            except Exception:
                pass

    # -- Content -----------------------------------------------------------

    def set_categories(self, categories, shares):
        """Rows to show (Category objects) and each code's share of the area."""
        self._categories = list(categories)
        self._shares = dict(shares or {})
        codes = {c.code for c in self._categories}
        self._hidden &= codes
        if self._solo not in codes:
            self._solo = None
        self._region_pos = {k: v for k, v in self._region_pos.items() if k in codes}
        self._relayout()

    def set_region_position(self, code, index, count):
        """Show "index+1 / count" on a row's region arrows."""
        self._region_pos[int(code)] = (int(index), int(count))
        self.update()

    def muted(self):
        codes = {c.code for c in self._categories}
        if self._solo is not None:
            return frozenset(codes - {self._solo})
        return frozenset(self._hidden & codes)

    def set_muted_state(self, hidden, solo):
        """Restore which categories are hidden or soloed (no signal)."""
        self._hidden = set(hidden or ())
        self._solo = solo
        self.update()

    def muted_state(self):
        return set(self._hidden), self._solo

    def highlight_spec(self):
        return HighlightSpec(active=False, muted=self.muted())

    # -- Layout (unscaled units) ---------------------------------------------

    def _fonts(self):
        base = QtGui.QFont(self.font())
        base.setPointSize(10)
        title = QtGui.QFont(base)
        title.setPointSize(12)
        bold = QtGui.QFont(base)
        bold.setBold(True)
        return base, title, bold

    def _relayout(self):
        """Work out the unscaled size from the content, keeping the current scale."""
        base, title, bold = self._fonts()
        fm, fmb, fmt = (QtGui.QFontMetrics(f) for f in (base, bold, title))
        self._label_w = max([fmb.horizontalAdvance(c.label) for c in self._categories] + [60])
        self._pct_w = fm.horizontalAdvance("100.0 %")
        self._has_nav = any(c.navigable for c in self._categories)
        row_w = _EYE_W + _GAP + _SWATCH_W + _GAP + self._label_w + _GAP * 2 + self._pct_w
        if self._has_nav:
            row_w += _GAP + _NAV_W
        self._base_w = float(max(row_w, fmt.horizontalAdvance(self._title) + 80, 200) + 2 * _PAD)
        self._base_h = float(_PAD + _TITLE_H + _ROW_H * len(self._categories) + _PAD)
        self.setMinimumSize(int(self._base_w * _MIN_SCALE), int(self._base_h * _MIN_SCALE))
        self.resize(int(round(self._base_w * self._scale)), int(round(self._base_h * self._scale)))
        self._clamp_into_parent()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        s = min(self.width() / self._base_w, self.height() / self._base_h)
        self._scale = max(_MIN_SCALE, min(_MAX_SCALE, s))
        self.update()

    def _to_base(self, pos):
        s = self._scale or 1.0
        return QtCore.QPointF(pos.x() / s, pos.y() / s)

    def _row_rect(self, i):
        return QtCore.QRectF(_PAD - 4, _PAD + _TITLE_H + i * _ROW_H, self._base_w - 2 * _PAD + 8, _ROW_H)

    def _eye_rect(self, i):
        r = self._row_rect(i)
        return QtCore.QRectF(r.left() + 4, r.top(), _EYE_W, r.height())

    def _nav_text(self, code):
        pos = self._region_pos.get(code)
        return None if pos is None else f"{pos[0] + 1}/{pos[1]}"

    def _nav_rects(self, i):
        """The previous and next arrows, just after the percentage: side by side
        until a region has been visited, then with the "2/5" counter between."""
        r = self._row_rect(i)
        left = r.right() - 4 - _NAV_W + 4
        text = self._nav_text(self._categories[i].code) if i < len(self._categories) else None
        gap = 2.0
        if text:
            base, _title, _bold = self._fonts()
            gap = QtGui.QFontMetricsF(base).horizontalAdvance(text) + 10
        prev = QtCore.QRectF(left, r.top() + 2, _ARROW_W, r.height() - 4)
        nxt = QtCore.QRectF(prev.right() + gap, r.top() + 2, _ARROW_W, r.height() - 4)
        return prev, nxt

    def _reset_rect(self):
        return QtCore.QRectF(self._base_w - _PAD - 70, _PAD - 2, 72, _TITLE_H - 4)

    def _hit(self, pos):
        """(row index or None, part) at a widget point: part is eye, prev, next,
        row, reset or None (empty space, which moves the legend)."""
        b = self._to_base(pos)
        if self.muted() and self._reset_rect().contains(b):
            return None, "reset"
        for i, cat in enumerate(self._categories):
            if not self._row_rect(i).contains(b):
                continue
            if self._eye_rect(i).contains(b):
                return i, "eye"
            if cat.navigable:
                prev, nxt = self._nav_rects(i)
                if prev.contains(b):
                    return i, "prev"
                if nxt.contains(b):
                    return i, "next"
            return i, "row"
        return None, None

    def _resize_edges(self, pos):
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        edges = set()
        near_x = _CORNER if (y <= _CORNER or y >= h - _CORNER) else _EDGE
        near_y = _CORNER if (x <= _CORNER or x >= w - _CORNER) else _EDGE
        if x <= near_x:
            edges.add("left")
        elif x >= w - near_x:
            edges.add("right")
        if y <= near_y:
            edges.add("top")
        elif y >= h - near_y:
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

    # -- Mouse ---------------------------------------------------------------

    def enterEvent(self, _event):
        self._hovered = True
        self.update()

    def leaveEvent(self, _event):
        self._hovered = False
        self._hover_row = None
        self._hover_part = None
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self.update()

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        self._press_global = event.globalPosition().toPoint()
        self._start_pos = self.pos()
        self._start_size = self.size()
        edges = self._resize_edges(pos)
        if edges:
            self._drag_mode = "resize"
            self._edges = edges
            return
        row, part = self._hit(pos)
        if part is None:
            self._drag_mode = "move"
            return
        if part == "reset":
            self._show_all()
            return
        cat = self._categories[row]
        if part == "eye":
            if self._solo is not None:
                # Leave solo for the plain hide/show view, keeping what was seen.
                self._hidden = {c.code for c in self._categories if c.code != self._solo}
                self._solo = None
            if cat.code in self._hidden:
                self._hidden.discard(cat.code)
            else:
                self._hidden.add(cat.code)
            self._emit_muted()
        elif part == "row":
            self._solo = None if self._solo == cat.code else cat.code
            self._emit_muted()
        elif part in ("prev", "next"):
            self.regionRequested.emit(cat.code, -1 if part == "prev" else 1)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._drag_mode is not None:
            delta = event.globalPosition().toPoint() - self._press_global
            if self._drag_mode == "move":
                self._move_by(delta)
            else:
                self._resize_by(delta)
            return

        edges = self._resize_edges(pos)
        row, part = (None, None) if edges else self._hit(pos)
        if (row, part) != (self._hover_row, self._hover_part):
            self._hover_row, self._hover_part = row, part
            self.update()
        if edges:
            self.setCursor(self._edge_cursor(edges))
            self._set_tip("Drag to resize the legend")
            return
        if part is None:
            self.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)
            tip = "Drag to move the legend. Right-click for more."
            if self._has_nav:
                tip = ("Click a row to show that category alone; use ‹ › to visit "
                       "its regions.\n" + tip)
            else:
                tip = "Click a row to show that category alone.\n" + tip
            self._set_tip(tip)
            return
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        if part == "reset":
            tip = "Show every category again"
        elif part == "eye":
            tip = "Hide or show this category (hidden ones are gray)"
        elif part in ("prev", "next"):
            tip = ("Turn the camera to the previous or next region of this category, "
                   "largest first")
        else:
            cat = self._categories[row]
            alone = "show every category" if self._solo == cat.code else "show it alone"
            tip = f"{cat.tooltip}\n\nClick to {alone}." if cat.tooltip else f"Click to {alone}."
        self._set_tip(tip)

    def _set_tip(self, tip):
        if tip != self.toolTip():
            self.setToolTip(tip)

    def mouseReleaseEvent(self, _event):
        self._drag_mode = None
        self._edges = set()

    def _move_by(self, delta):
        new_pos = self._start_pos + delta
        parent = self.parent()
        if parent is not None:
            new_pos.setX(min(max(new_pos.x(), 0), max(0, parent.width() - self.width())))
            new_pos.setY(min(max(new_pos.y(), 0), max(0, parent.height() - self.height())))
        self.move(new_pos)

    def _resize_by(self, delta):
        parent = self.parent()
        x0, y0 = self._start_pos.x(), self._start_pos.y()
        w0, h0 = self._start_size.width(), self._start_size.height()
        right0, bottom0 = x0 + w0, y0 + h0
        min_w, min_h = self.minimumWidth(), self.minimumHeight()
        left, top, right, bottom = x0, y0, right0, bottom0
        if "right" in self._edges:
            right = max(x0 + w0 + delta.x(), x0 + min_w)
            if parent is not None:
                right = min(right, parent.width())
        if "bottom" in self._edges:
            bottom = max(y0 + h0 + delta.y(), y0 + min_h)
            if parent is not None:
                bottom = min(bottom, parent.height())
        if "left" in self._edges:
            left = min(max(x0 + delta.x(), 0), right0 - min_w)
        if "top" in self._edges:
            top = min(max(y0 + delta.y(), 0), bottom0 - min_h)
        self.setGeometry(int(left), int(top), int(right - left), int(bottom - top))

    def contextMenuEvent(self, event):
        menu = QtWidgets.QMenu(self)
        menu.addAction("Show All", self._show_all)
        for cat in self._categories:
            if cat.code == 3:  # undercut
                menu.addAction("Show Undercuts Only", lambda c=cat.code: self._solo_code(c))
        menu.addSeparator()
        menu.addAction("Reset Size", self._reset_size)
        menu.exec(event.globalPos())

    def _reset_size(self):
        self._scale = 1.0
        self.resize(int(round(self._base_w)), int(round(self._base_h)))
        self._clamp_into_parent()

    def _show_all(self):
        self._hidden.clear()
        self._solo = None
        self._emit_muted()

    def _solo_code(self, code):
        self._solo = code
        self._emit_muted()

    def _emit_muted(self):
        self.update()
        self.mutedChanged.emit(self.muted())

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() == QtCore.QEvent.Type.Resize:
            self._clamp_into_parent()
        return False

    def _clamp_into_parent(self):
        parent = self.parent()
        if parent is None:
            return
        self.move(
            min(max(self.x(), 0), max(0, parent.width() - self.width())),
            min(max(self.y(), 0), max(0, parent.height() - self.height())),
        )

    # -- Painting --------------------------------------------------------------

    def _text_color(self, alpha=255):
        r, g, b = self._text_rgb
        return QtGui.QColor(int(r * 255), int(g * 255), int(b * 255), alpha)

    def _halo_text(self, p, rect, align, text, alpha=255):
        """Text in the legend color with a faint outline, as on the gradient legend."""
        fm = QtGui.QFontMetricsF(p.font())
        w = fm.horizontalAdvance(text)
        if align & QtCore.Qt.AlignmentFlag.AlignRight:
            x = rect.right() - w
        elif align & QtCore.Qt.AlignmentFlag.AlignHCenter:
            x = rect.center().x() - w / 2
        else:
            x = rect.left()
        baseline = rect.center().y() + (fm.ascent() - fm.descent()) / 2
        path = QtGui.QPainterPath()
        path.addText(QtCore.QPointF(x, baseline), p.font(), text)

        fill = self._text_color(alpha)
        lum = 0.2126 * fill.redF() + 0.7152 * fill.greenF() + 0.0722 * fill.blueF()
        outline = QtGui.QColor(255, 255, 255, 60) if lum < 0.5 else QtGui.QColor(0, 0, 0, 60)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.setPen(QtGui.QPen(outline, 1.2, QtCore.Qt.PenStyle.SolidLine,
                            QtCore.Qt.PenCapStyle.RoundCap, QtCore.Qt.PenJoinStyle.RoundJoin))
        p.drawPath(path)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawPath(path)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
        if self._hovered:
            self._paint_hover_chrome(p)

        p.save()
        p.scale(self._scale, self._scale)
        base, title_font, bold = self._fonts()

        p.setFont(title_font)
        self._halo_text(p, QtCore.QRectF(_PAD, _PAD - 2, self._base_w - 2 * _PAD, _TITLE_H),
                        QtCore.Qt.AlignmentFlag.AlignLeft, self._title)
        if self.muted():
            hot = self._hover_part == "reset"
            p.setFont(bold if hot else base)
            self._halo_text(p, self._reset_rect(), QtCore.Qt.AlignmentFlag.AlignRight, "Show all",
                            255 if hot else 190)

        muted = self.muted()
        for i, cat in enumerate(self._categories):
            self._paint_row(p, i, cat, cat.code in muted, base, bold)
        p.restore()

        if self._hovered:
            self._paint_resize_grip(p)

    def _paint_row(self, p, i, cat, is_muted, base, bold):
        r = self._row_rect(i)
        marked = cat.code == self._marker_code
        soloed = cat.code == self._solo
        if marked or soloed or self._hover_row == i:
            tint = QtGui.QColor(*cat_rgb255(cat.rgb), 80 if (marked or soloed) else 40)
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(tint)
            p.drawRoundedRect(r, 3.0, 3.0)

        alpha = 120 if is_muted else 255
        eye = self._eye_rect(i)
        self._paint_eye(p, eye, not (cat.code in self._hidden and self._solo is None),
                        self._hover_row == i and self._hover_part == "eye")

        x = eye.right() + _GAP
        sw = QtCore.QRectF(x, r.center().y() - 6, _SWATCH_W, 12)
        color = QtGui.QColor(158, 158, 158) if is_muted else QtGui.QColor(*cat_rgb255(cat.rgb))
        p.setBrush(color)
        p.setPen(QtGui.QPen(QtGui.QColor(20, 20, 20, 220), 1.0))
        p.drawRect(sw)

        x = sw.right() + _GAP
        p.setFont(bold if (marked or soloed) else base)
        self._halo_text(p, QtCore.QRectF(x, r.top(), self._label_w + _GAP, r.height()),
                        QtCore.Qt.AlignmentFlag.AlignLeft, cat.label, alpha)

        x += self._label_w + _GAP * 2
        share = self._shares.get(cat.code, 0.0) * 100.0
        p.setFont(base)
        self._halo_text(p, QtCore.QRectF(x, r.top(), self._pct_w, r.height()),
                        QtCore.Qt.AlignmentFlag.AlignRight, f"{share:.1f} %", int(alpha * 0.9))

        if cat.navigable:
            prev, nxt = self._nav_rects(i)
            has_any = share > 0.0
            arrow_font = QtGui.QFont(bold)
            arrow_font.setPointSize(13)
            for rect, glyph, part in ((prev, "‹", "prev"), (nxt, "›", "next")):
                hot = self._hover_row == i and self._hover_part == part and has_any
                if hot:
                    p.setPen(QtCore.Qt.PenStyle.NoPen)
                    p.setBrush(QtGui.QColor(127, 127, 127, 80))
                    p.drawRoundedRect(rect, 3.0, 3.0)
                p.setFont(arrow_font)
                self._halo_text(p, rect, QtCore.Qt.AlignmentFlag.AlignHCenter, glyph,
                                255 if has_any else 80)
            text = self._nav_text(cat.code)
            if text and has_any:
                p.setFont(base)
                mid = QtCore.QRectF(prev.right(), r.top(), nxt.left() - prev.right(), r.height())
                self._halo_text(p, mid, QtCore.Qt.AlignmentFlag.AlignHCenter, text, 220)

    def _paint_eye(self, p, rect, open_, hot):
        c = rect.center()
        col = self._text_color(255 if hot else 200)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.setPen(QtGui.QPen(col, 1.4))
        path = QtGui.QPainterPath()
        w, h = 7.0, 4.5
        path.moveTo(c.x() - w, c.y())
        path.quadTo(c.x(), c.y() - 2 * h, c.x() + w, c.y())
        path.quadTo(c.x(), c.y() + 2 * h, c.x() - w, c.y())
        p.drawPath(path)
        if open_:
            p.setBrush(col)
            p.drawEllipse(QtCore.QPointF(c.x(), c.y()), 2.2, 2.2)
        else:
            p.drawLine(QtCore.QPointF(c.x() - w, c.y() + h + 1), QtCore.QPointF(c.x() + w, c.y() - h - 1))

    def _paint_hover_chrome(self, p):
        rect = QtCore.QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0)
        p.setBrush(QtGui.QColor(127, 127, 127, 28))
        p.setPen(QtGui.QPen(QtGui.QColor(200, 200, 200, 130), 1.2))
        p.drawRoundedRect(rect, 6.0, 6.0)

    def _paint_resize_grip(self, p):
        gx, gy = self.width() - 4, self.height() - 4
        p.setPen(QtGui.QPen(QtGui.QColor(230, 230, 230, 220), 1.4))
        for off in (3, 7, 11):
            p.drawLine(gx - off, gy, gx, gy - off)


def cat_rgb255(rgb):
    return (int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255))
