# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The Zebra Stripes task panel.

::

    Model      Body (or Body, 3 faces)                [pick]
    Stripes    [Follow the view | Fixed to the part]
               Direction  [Vertical | Horizontal]      (or a direction row)
               Stripes    ----o-----  20
               Width      ---o------  50 %
               Colors     [black] [white] [swap]
    Display    Quality [Fine v]   [x] Show edges
    > How to read the stripes

Everything applies at once; only Quality meshes the part again. The stripes
show only while the panel is open: Close clears them. The last settings are
kept for next time.
"""

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ..utils import resources  # noqa: F401
from ..utils.body_select import auto_select_body
from ..utils.direction_row import PICK_SVG, DirectionRow, ViewSwitch, axis_name, icon_button
from ..utils.picking import CrossCursorOverride, first_selected_subelement
from ..utils.pull_direction import compute_direction
from ..utils.qt_filters import EscapeFilter
from ..utils.view import get_active_view

from ...app.contour import zebra as Z
from ...app.prefs import dfm_params
from .zebra_scene import ZebraOverlay

ICON = ":/icons/dfm_zebra.svg"
_TITLE = "Zebra Stripes"
_COUNT_RANGE = (2, 100)
_WIDTH_RANGE = (10, 90)  # percent of a stripe pair in the first color
_DEFAULTS = {"count": 20, "width": 50, "first": (0, 0, 0), "second": (255, 255, 255)}

_SWAP_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    b'<path d="M7 4L3 8l4 4M3 8h14M17 20l4-4-4-4M21 16H7"/></svg>'
)

# The reading guide: (picture, heading, line).
_GUIDE = (
    ("g0", "Stripes jump at the edge",
     "The faces only touch (G0): there is a crease you will see and feel on the part."),
    ("g1", "Stripes line up but bend sharply",
     "Tangent (G1), typical of a constant-radius fillet. Fine on most parts; it shows on gloss."),
    ("g2", "Stripes run smoothly across",
     "Curvature-continuous (G2): the join won't show, even on a polished surface."),
)


def _pack(rgb) -> int:
    r, g, b = rgb
    return (int(r) << 16) | (int(g) << 8) | int(b)


def _unpack(value: int):
    return ((value >> 16) & 255, (value >> 8) & 255, value & 255)


class ContinuityPicture(QtWidgets.QWidget):
    """A small drawing of stripes crossing an edge at a G0, G1 or G2 join."""

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(84, 50)

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt naming)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QtGui.QColor("#ffffff"))
        mid = w / 2.0
        gap = 10.0
        pen = QtGui.QPen(QtGui.QColor("#16181d"), gap / 2.0)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        for i in range(-1, 6):
            y0 = 4 + i * gap
            path = QtGui.QPainterPath()
            steps = 24
            for s in range(steps + 1):
                x = w * s / steps
                u = (x - mid) / mid  # -1 .. 1, 0 at the edge
                if self.kind == "g0":
                    y = y0 + (gap / 2.0 if u > 0 else 0.0)
                elif self.kind == "g1":
                    y = y0 + (14.0 * u if u > 0 else 0.0)
                else:
                    # Bends away gradually from zero slope: no corner at the edge.
                    y = y0 + 14.0 * max(u, 0.0) ** 2
                path.moveTo(x, y) if s == 0 else path.lineTo(x, y)
            p.drawPath(path)
        edge = QtGui.QPen(QtGui.QColor("#d0453a"), 1.5, QtCore.Qt.PenStyle.DashLine)
        p.setPen(edge)
        p.drawLine(QtCore.QPointF(mid, 0), QtCore.QPointF(mid, h))
        p.setPen(QtGui.QPen(QtGui.QColor(127, 127, 127, 150), 1))
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawRect(0, 0, w - 1, h - 1)
        p.end()


class ZebraTaskPanel:
    def __init__(self):
        self.target_object = None
        self.faces: Optional[list] = None  # 1-based face numbers, or None for the whole body
        self.overlay: Optional[ZebraOverlay] = None
        self.direction = App.Vector(0, 0, 1)
        self.direction_ref = "+Z"
        self.direction_flipped = False
        self.picking_mode: Optional[str] = None
        self._cursor = CrossCursorOverride()
        prefs = dfm_params()
        self._colors = [
            _unpack(prefs.GetUnsigned("ZebraColor1", _pack(_DEFAULTS["first"]))),
            _unpack(prefs.GetUnsigned("ZebraColor2", _pack(_DEFAULTS["second"]))),
        ]

        self._build_form()
        Gui.Selection.addObserver(self)
        self._escape_filter = EscapeFilter(self._on_escape)
        self.form.installEventFilter(self._escape_filter)

        faces_obj, faces = self._selected_faces()
        if faces_obj is not None:
            Gui.Selection.clearSelection()
            self._apply_object(faces_obj, faces)
        else:
            auto_select_body(self._apply_object)

    # -- the form ---------------------------------------------------------

    def _build_form(self) -> None:
        prefs = dfm_params()
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(_TITLE)
        self.form.setWindowIcon(QtGui.QIcon(ICON))
        root = QtWidgets.QVBoxLayout(self.form)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)

        model_box = QtWidgets.QGroupBox("Model")
        row = QtWidgets.QHBoxLayout(model_box)
        row.setContentsMargins(8, 8, 8, 8)
        row.setSpacing(4)
        self.le_object = QtWidgets.QLineEdit()
        self.le_object.setReadOnly(True)
        self.le_object.setPlaceholderText("No model selected")
        self.pb_object = icon_button(
            PICK_SVG, "Pick the model in the 3D view. To stripe only some faces, select them "
            "before opening the tool.", checkable=True)
        self.pb_object.clicked.connect(self._on_pick_object)
        row.addWidget(self.le_object, 1)
        row.addWidget(self.pb_object)
        root.addWidget(model_box)

        box = QtWidgets.QGroupBox("Stripes")
        g = QtWidgets.QGridLayout(box)
        g.setContentsMargins(8, 8, 8, 8)
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(6)
        g.setColumnStretch(1, 1)

        self.sw_mode = ViewSwitch("Follow the view", "Fixed to the part")
        self.sw_mode.buttons[0].setToolTip(
            "The stripes slide over the part as you turn it, like a reflection. "
            "Turn the part slowly: a stripe that wobbles or breaks shows a defect.")
        self.sw_mode.buttons[1].setToolTip(
            "The stripes stay put as you turn the part: lines of equal angle to a direction "
            "(isophotes). Easier to compare between screenshots.")
        self.sw_mode.setChecked(prefs.GetString("ZebraMode", "view") == "fixed")
        self.sw_mode.toggled.connect(self._on_mode)
        g.addWidget(self.sw_mode, 0, 0, 1, 3)

        self.lbl_view_dir = QtWidgets.QLabel("Direction")
        g.addWidget(self.lbl_view_dir, 1, 0)
        self.sw_horizontal = ViewSwitch("Vertical", "Horizontal")
        self.sw_horizontal.setChecked(prefs.GetBool("ZebraHorizontal", False))
        self.sw_horizontal.setToolTip("Which way the stripes run on the screen")
        self.sw_horizontal.toggled.connect(self._on_mode)
        g.addWidget(self.sw_horizontal, 1, 1, 1, 2)

        self.direction_row = DirectionRow("Direction")
        self.direction_row.pick_btn.setToolTip(
            "Pick a face or edge. Planar faces give their normal; cylindrical, conical and "
            "toroidal faces and circular edges give their axis; other edges give their tangent.")
        self.direction_row.axis_clicked.connect(self._on_axis)
        self.direction_row.pick_clicked.connect(self._on_pick_direction)
        self.direction_row.flip_clicked.connect(self._on_flip)
        g.addWidget(self.direction_row, 2, 0, 1, 3)

        g.addWidget(QtWidgets.QLabel("Stripes"), 3, 0)
        self.sl_count = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.sl_count.setRange(*_COUNT_RANGE)
        self.sb_count = QtWidgets.QSpinBox()
        self.sb_count.setRange(*_COUNT_RANGE)
        self.sb_count.setToolTip("How many stripes: more show smaller changes in the surface")
        count = min(max(prefs.GetInt("ZebraCount", _DEFAULTS["count"]), _COUNT_RANGE[0]), _COUNT_RANGE[1])
        self.sl_count.setValue(count)
        self.sb_count.setValue(count)
        self.sl_count.valueChanged.connect(self.sb_count.setValue)
        self.sb_count.valueChanged.connect(self.sl_count.setValue)
        self.sb_count.valueChanged.connect(self._on_style)
        g.addWidget(self.sl_count, 3, 1)
        g.addWidget(self.sb_count, 3, 2)

        g.addWidget(QtWidgets.QLabel("Width"), 4, 0)
        self.sl_width = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.sl_width.setRange(*_WIDTH_RANGE)
        self.sl_width.setToolTip("How much of each stripe pair is the first color")
        width = min(max(prefs.GetInt("ZebraWidth", _DEFAULTS["width"]), _WIDTH_RANGE[0]), _WIDTH_RANGE[1])
        self.sl_width.setValue(width)
        self.lbl_width = QtWidgets.QLabel(f"{width} %")
        self.lbl_width.setMinimumWidth(40)
        self.sl_width.valueChanged.connect(self._on_style)
        g.addWidget(self.sl_width, 4, 1)
        g.addWidget(self.lbl_width, 4, 2)

        g.addWidget(QtWidgets.QLabel("Colors"), 5, 0)
        colors = QtWidgets.QHBoxLayout()
        colors.setSpacing(4)
        self.pb_colors = []
        for i in range(2):
            btn = QtWidgets.QPushButton()
            btn.setFixedSize(44, 24)
            btn.setToolTip("Stripe color" if i == 0 else "Background color")
            btn.clicked.connect(lambda _c=False, i=i: self._on_pick_color(i))
            colors.addWidget(btn)
            self.pb_colors.append(btn)
        self.pb_swap = icon_button(_SWAP_SVG, "Swap the two colors")
        self.pb_swap.clicked.connect(self._on_swap)
        colors.addWidget(self.pb_swap)
        colors.addStretch(1)
        g.addLayout(colors, 5, 1, 1, 2)
        root.addWidget(box)

        disp = QtWidgets.QGroupBox("Display")
        dg = QtWidgets.QGridLayout(disp)
        dg.setContentsMargins(8, 8, 8, 8)
        dg.setHorizontalSpacing(6)
        dg.setColumnStretch(1, 1)
        dg.addWidget(QtWidgets.QLabel("Quality"), 0, 0)
        self.cb_quality = QtWidgets.QComboBox()
        self.cb_quality.addItems(list(Z.QUALITY))
        self.cb_quality.setToolTip(
            "How finely the part is meshed for the stripes. Finer gives smoother stripes on "
            "curved faces, but takes longer on big parts.")
        quality = prefs.GetString("ZebraQuality", Z.DEFAULT_QUALITY)
        self.cb_quality.setCurrentText(quality if quality in Z.QUALITY else Z.DEFAULT_QUALITY)
        self.cb_quality.currentIndexChanged.connect(self._on_quality)
        dg.addWidget(self.cb_quality, 0, 1)
        self.cb_edges = QtWidgets.QCheckBox("Show edges")
        self.cb_edges.setToolTip("Draw the faces' edges over the stripes, so you can see where faces meet")
        self.cb_edges.setChecked(prefs.GetBool("ZebraEdges", True))
        self.cb_edges.toggled.connect(self._on_edges)
        dg.addWidget(self.cb_edges, 0, 2)
        root.addWidget(disp)

        root.addWidget(self._build_guide(prefs.GetBool("ZebraGuideOpen", True)))
        root.addStretch(1)
        self._refresh_colors()
        self._refresh_mode_rows()

    def _build_guide(self, open_: bool) -> QtWidgets.QWidget:
        holder = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.tb_guide = QtWidgets.QToolButton()
        self.tb_guide.setText("How to read the stripes")
        self.tb_guide.setCheckable(True)
        self.tb_guide.setChecked(open_)
        self.tb_guide.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.tb_guide.setAutoRaise(True)
        lay.addWidget(self.tb_guide)
        self.guide = QtWidgets.QWidget()
        gl = QtWidgets.QGridLayout(self.guide)
        gl.setContentsMargins(4, 0, 4, 0)
        gl.setHorizontalSpacing(10)
        gl.setVerticalSpacing(8)
        gl.setColumnStretch(1, 1)
        for row, (kind, heading, line) in enumerate(_GUIDE):
            gl.addWidget(ContinuityPicture(kind), row, 0, QtCore.Qt.AlignmentFlag.AlignTop)
            text = QtWidgets.QLabel(f"<b>{heading}</b><br>{line}")
            text.setWordWrap(True)
            text.setTextFormat(QtCore.Qt.TextFormat.RichText)
            gl.addWidget(text, row, 1)
        hint = QtWidgets.QLabel("The dashed line is the edge between two faces. Turn the part to "
                                "move the stripes over the edge you are checking.")
        hint.setWordWrap(True)
        hint.setEnabled(False)
        gl.addWidget(hint, len(_GUIDE), 0, 1, 2)
        lay.addWidget(self.guide)
        self.tb_guide.toggled.connect(self._on_guide)
        self._on_guide(open_)
        return holder

    def _on_guide(self, open_: bool) -> None:
        self.guide.setVisible(open_)
        self.tb_guide.setArrowType(QtCore.Qt.ArrowType.DownArrow if open_ else QtCore.Qt.ArrowType.RightArrow)
        dfm_params().SetBool("ZebraGuideOpen", bool(open_))

    # -- values -------------------------------------------------------------

    @property
    def mode(self) -> str:
        return "fixed" if self.sw_mode.isChecked() else "view"

    def _refresh_mode_rows(self) -> None:
        fixed = self.mode == "fixed"
        self.lbl_view_dir.setVisible(not fixed)
        self.sw_horizontal.setVisible(not fixed)
        self.direction_row.setVisible(fixed)
        d = self.direction
        self.direction_row.set_state(
            (d.x, d.y, d.z), self.direction_ref, self.direction_flipped,
            picking=self.picking_mode == "direction")

    def _refresh_colors(self) -> None:
        for btn, (r, g, b) in zip(self.pb_colors, self._colors):
            btn.setStyleSheet(
                f"QPushButton {{ background: rgb({r}, {g}, {b}); border: 1px solid rgba(127, 127, 127, 160);"
                " border-radius: 3px; }")

    def _save_prefs(self) -> None:
        prefs = dfm_params()
        prefs.SetString("ZebraMode", self.mode)
        prefs.SetBool("ZebraHorizontal", self.sw_horizontal.isChecked())
        prefs.SetInt("ZebraCount", self.sb_count.value())
        prefs.SetInt("ZebraWidth", self.sl_width.value())
        prefs.SetUnsigned("ZebraColor1", _pack(self._colors[0]))
        prefs.SetUnsigned("ZebraColor2", _pack(self._colors[1]))
        prefs.SetString("ZebraQuality", self.cb_quality.currentText())
        prefs.SetBool("ZebraEdges", self.cb_edges.isChecked())

    # -- the overlay ------------------------------------------------------------

    def _apply_object(self, obj, faces=None) -> bool:
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull() or not shape.Faces:
            App.Console.PrintWarning("DFM zebra: that object has no faces to show stripes on.\n")
            return False
        self.target_object = obj
        self.faces = sorted(set(faces)) if faces else None
        if self.faces:
            n = len(self.faces)
            self.le_object.setText(f"{obj.Label}, {n} face{'s' if n != 1 else ''}")
            self.le_object.setToolTip(", ".join(f"Face{i}" for i in self.faces))
        else:
            self.le_object.setText(obj.Label)
            self.le_object.setToolTip("")
        self._rebuild()
        return True

    def _rebuild(self) -> None:
        """Mesh the target again and show its stripes."""
        self._remove_overlay()
        if self.target_object is None:
            return
        from ...core.utils.conversion import freecad_to_ocp

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            mesh = Z.tessellate(freecad_to_ocp(self.target_object.Shape), self.cb_quality.currentText(), self.faces)
        except Exception as exc:
            App.Console.PrintError(f"DFM zebra: could not mesh {self.target_object.Label}. {exc}\n")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        view = get_active_view()
        if view is None:
            return
        overlay = ZebraOverlay(mesh)
        overlay.set_style(count=self.sb_count.value(), width_share=self.sl_width.value() / 100.0,
                          colors=self._colors)
        overlay.set_edges_visible(self.cb_edges.isChecked())
        overlay.mode = self.mode
        overlay.horizontal = self.sw_horizontal.isChecked()
        overlay.direction = (self.direction.x, self.direction.y, self.direction.z)
        overlay.attach(view)
        self.overlay = overlay

    def _remove_overlay(self) -> None:
        if self.overlay is not None:
            self.overlay.remove()
            self.overlay = None

    def _on_mode(self, _checked=False) -> None:
        self._refresh_mode_rows()
        if self.overlay is not None:
            self.overlay.set_mode(self.mode, horizontal=self.sw_horizontal.isChecked(),
                                  direction=(self.direction.x, self.direction.y, self.direction.z))
        self._save_prefs()

    def _on_style(self, _value=None) -> None:
        self.lbl_width.setText(f"{self.sl_width.value()} %")
        if self.overlay is not None:
            self.overlay.set_style(count=self.sb_count.value(), width_share=self.sl_width.value() / 100.0)
        self._save_prefs()

    def _on_pick_color(self, index: int) -> None:
        r, g, b = self._colors[index]
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor(r, g, b), self.form,
                                                "Stripe color" if index == 0 else "Background color")
        if not color.isValid():
            return
        self._colors[index] = (color.red(), color.green(), color.blue())
        self._apply_colors()

    def _on_swap(self) -> None:
        self._colors.reverse()
        self._apply_colors()

    def _apply_colors(self) -> None:
        self._refresh_colors()
        if self.overlay is not None:
            self.overlay.set_style(colors=self._colors)
        self._save_prefs()

    def _on_quality(self, _index=0) -> None:
        self._save_prefs()
        self._rebuild()

    def _on_edges(self, on: bool) -> None:
        if self.overlay is not None:
            self.overlay.set_edges_visible(on)
        self._save_prefs()

    # -- the fixed direction ----------------------------------------------------------

    def _set_direction(self, vec, ref: str, flipped: bool = False) -> None:
        self.direction = App.Vector(*vec)
        self.direction_ref = ref
        self.direction_flipped = flipped
        self._refresh_mode_rows()
        if self.overlay is not None:
            self.overlay.set_mode(self.mode, direction=tuple(vec))

    def _on_axis(self, vec) -> None:
        self._end_pick()
        self._set_direction(vec, axis_name(vec).replace("−", "-"))

    def _on_flip(self) -> None:
        d = self.direction
        self._set_direction((-d.x, -d.y, -d.z), self.direction_ref, not self.direction_flipped)

    def _on_pick_direction(self) -> None:
        if self.picking_mode == "direction":
            self._end_pick()
            return
        if self._apply_direction_from_selection():
            Gui.Selection.clearSelection()
            return
        self._start_pick("direction")

    def _apply_direction_from_selection(self) -> bool:
        picked = first_selected_subelement()
        if picked is None:
            return False
        sub, name = picked
        result = compute_direction(sub)
        if result is None:
            App.Console.PrintWarning("DFM zebra: pick a face or an edge.\n")
            return False
        _pnt, d = result
        self._set_direction((d.x, d.y, d.z), name)
        return True

    # -- picking in the 3D view ---------------------------------------------------------

    def _on_pick_object(self) -> None:
        if self.picking_mode == "object":
            self._end_pick()
            return
        sel = Gui.Selection.getSelection()
        if sel:
            Gui.Selection.clearSelection()
            if self._apply_object(sel[0]):
                self.pb_object.setChecked(False)
                return
        self._start_pick("object")

    def _start_pick(self, mode: str) -> None:
        self._end_pick()
        self.picking_mode = mode
        if mode == "object":
            self.pb_object.setChecked(True)
            self.le_object.setText("")
            self.le_object.setPlaceholderText("Click a model in the 3D view")
        self._refresh_mode_rows()
        self._cursor.push()

    def _end_pick(self) -> None:
        if self.picking_mode is None:
            return
        self.picking_mode = None
        self.pb_object.setChecked(False)
        self.le_object.setPlaceholderText("No model selected")
        if self.target_object is not None and not self.le_object.text():
            self._apply_label()
        self._refresh_mode_rows()
        self._cursor.pop()

    def _apply_label(self) -> None:
        obj = self.target_object
        if self.faces:
            n = len(self.faces)
            self.le_object.setText(f"{obj.Label}, {n} face{'s' if n != 1 else ''}")
        else:
            self.le_object.setText(obj.Label)

    def _process_pick(self) -> None:
        if self.picking_mode == "object":
            sel = Gui.Selection.getSelection()
            ok = bool(sel) and self._apply_object(sel[0])
        elif self.picking_mode == "direction":
            ok = self._apply_direction_from_selection()
        else:
            return
        if ok:
            self._end_pick()
            Gui.Selection.clearSelection()

    @staticmethod
    def _selected_faces():
        """(object, [face numbers]) when faces of one object are selected, else (None, None)."""
        try:
            sel = Gui.Selection.getSelectionEx()
        except Exception:
            return None, None
        if not sel:
            return None, None
        names = [n for n in getattr(sel[0], "SubElementNames", ()) if n]
        if not names or not all(n.startswith("Face") and n[4:].isdigit() for n in names):
            return None, None
        return sel[0].Object, [int(n[4:]) for n in names]

    # Selection observer.
    def addSelection(self, *_args) -> None:  # noqa: N802 (FreeCAD naming)
        if self.picking_mode:
            QtCore.QTimer.singleShot(30, self._process_pick)

    def removeSelection(self, *_args) -> None:  # noqa: N802
        pass

    def setSelection(self, *_args) -> None:  # noqa: N802
        pass

    def clearSelection(self, *_args) -> None:  # noqa: N802
        pass

    def _on_escape(self) -> None:
        if self.picking_mode:
            self._end_pick()

    # -- the task dialog ---------------------------------------------------------

    def getStandardButtons(self):  # noqa: N802
        return QtWidgets.QDialogButtonBox.StandardButton.Close

    def _teardown(self) -> None:
        self._end_pick()
        self._remove_overlay()
        try:
            Gui.Selection.removeObserver(self)
        except Exception:
            pass

    def reject(self):
        self._teardown()
        Gui.Control.closeDialog()
        return True

    def accept(self):
        return self.reject()
