# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Base task panel shared by every contour tool.

A tool is just a ContourMeasure plus a title and icon; this panel reads the
measure's metadata to build the UI (adding a pull-direction picker only when the
measure needs one) and drives meshing, measuring, the coin overlay, the
interactive legend, and the hover readout."""

from typing import Optional
import time

from PySide6 import QtCore, QtGui, QtWidgets

from pivy import coin

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore
import Part  # type: ignore

from ..visuals import DirectionIndicator

try:
    from .. import DFM_rc  # noqa: F401
except Exception:
    pass

from ...contour.meshing import generate_uniform_mesh
from ...contour.colormap import COLORMAPS
from ...contour.measures import BoolOption, ChoiceOption

from .scene import ContourNode, register, clear_all
from .legend import ContourLegend
from .resolution import ResolutionField
from . import document  # noqa: F401


PICK_BUTTON_STYLE = """
QPushButton:checked { border: 2px solid palette(highlight); font-weight: bold; }
"""

_BAND_STEPS = {
    "Smooth": 0.0,
    "1 unit bands": 1.0,
    "2 unit bands": 2.0,
    "5 unit bands": 5.0,
    "10 unit bands": 10.0,
}


class _EscapeFilter(QtCore.QObject):
    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.KeyPress:
            if event.key() == QtCore.Qt.Key.Key_Escape and self._callback():
                return True
        return False


class ContourTaskPanel:
    def __init__(self, measure, title, icon=":/icons/dfm_analysis.svg", analysis_obj=None):
        self.measure = measure
        self._title = title
        self._icon = icon
        self._analysis_obj = analysis_obj
        self._saved = False
        self._auto_range = False

        self.target_object = None
        self.target_shape = None

        self.pull_dir = App.Vector(0, 0, 1)
        self.pull_ref = "+Z (default)"
        self.pull_flipped = False
        self.pull_anchor = None
        self.indicator = (
            DirectionIndicator((1.0, 0.15, 0.15), "Pull") if measure.needs_pull_direction else None
        )

        self.picking_mode: Optional[str] = None
        self.cursor_overridden = False

        self._has_contour = False
        self._dirty = False
        self._last = None
        self._mesh = None
        self._node = None
        self._legend = None
        self._option_widgets = {}
        self._hover_label = None
        self._hover_cb = None
        self._hover_view = None
        self._last_hover_t = 0.0
        self._hover_interval = 0.03
        self._last_face_key = None

        self._build_form()
        Gui.Selection.addObserver(self)
        self._escape_filter = _EscapeFilter(self._on_escape)
        self.form.installEventFilter(self._escape_filter)
        if self._analysis_obj is not None:
            self._load_from_object(self._analysis_obj)
        else:
            self._auto_select()
        self._update_generate_state()

    def _grid(self, box):
        g = QtWidgets.QGridLayout(box)
        g.setContentsMargins(8, 8, 8, 8)
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(6)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)
        return g

    @staticmethod
    def _compact_combo(combo):
        """Stop a combo's longest item from dictating column width, so the two
        grid columns can share space evenly."""
        combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setMinimumContentsLength(6)
        combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed
        )

    def _build_form(self):
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(self._title)
        self.form.setWindowIcon(QtGui.QIcon(self._icon))
        root = QtWidgets.QVBoxLayout(self.form)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)

        # Object
        obj_box = QtWidgets.QGroupBox("Object")
        og = self._grid(obj_box)
        self.pb_object = QtWidgets.QPushButton("Select Object")
        self.pb_object.setCheckable(True)
        self.pb_object.setMinimumHeight(28)
        self.pb_object.setStyleSheet(PICK_BUTTON_STYLE)
        self.pb_object.setToolTip("Pick the object, or pre-select it before opening the tool.")
        self.pb_object.clicked.connect(self._on_pick_object)
        self.le_object = QtWidgets.QLineEdit()
        self.le_object.setReadOnly(True)
        self.le_object.setMinimumHeight(28)
        self.le_object.setPlaceholderText("No object selected")
        og.addWidget(self.pb_object, 0, 0)
        og.addWidget(self.le_object, 0, 1)
        root.addWidget(obj_box)

        # Pull direction (only if the measure needs it)
        if self.measure.needs_pull_direction:
            pull_box = QtWidgets.QGroupBox("Pull Direction")
            pg = self._grid(pull_box)
            self.pb_pull = QtWidgets.QPushButton("Select Pull Direction")
            self.pb_pull.setCheckable(True)
            self.pb_pull.setMinimumHeight(28)
            self.pb_pull.setStyleSheet(PICK_BUTTON_STYLE)
            self.pb_pull.setToolTip("Set from a planar face normal or an edge. Defaults to +Z.")
            self.pb_pull.clicked.connect(self._on_pick_pull)
            field = QtWidgets.QHBoxLayout()
            field.setContentsMargins(0, 0, 0, 0)
            field.setSpacing(4)
            self.le_pull = QtWidgets.QLineEdit()
            self.le_pull.setReadOnly(True)
            self.le_pull.setMinimumHeight(28)
            self.le_pull.setText(self.pull_ref)
            self.flip_btn = QtWidgets.QToolButton()
            self.flip_btn.setIcon(QtGui.QIcon(":/icons/flip_direction.svg"))
            self.flip_btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
            self.flip_btn.setIconSize(QtCore.QSize(18, 18))
            self.flip_btn.setFixedSize(28, 28)
            self.flip_btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            self.flip_btn.setStyleSheet(
                "QToolButton { border: none; background: transparent; padding: 0px; }"
                "QToolButton:hover { background: rgba(127,127,127,40); border-radius: 3px; }"
            )
            self.flip_btn.setToolTip("Flip the pull direction 180 degrees.")
            self.flip_btn.clicked.connect(self._on_flip_pull)
            field.addWidget(self.le_pull, 1)
            field.addWidget(self.flip_btn, 0)
            fw = QtWidgets.QWidget()
            fw.setLayout(field)
            pg.addWidget(self.pb_pull, 0, 0)
            pg.addWidget(fw, 0, 1)
            root.addWidget(pull_box)
        else:
            self.pb_pull = None

        # Options (measure-specific, e.g. thickness method): above Contour.
        if self.measure.options:
            opt_box = QtWidgets.QGroupBox("Options")
            og2 = self._grid(opt_box)
            orow = 0
            for opt in self.measure.options:
                if isinstance(opt, ChoiceOption):
                    og2.addWidget(QtWidgets.QLabel(opt.label), orow, 0)
                    combo = QtWidgets.QComboBox()
                    combo.addItems(opt.choices)
                    if opt.default in opt.choices:
                        combo.setCurrentText(opt.default)
                    if opt.tooltip:
                        combo.setToolTip(opt.tooltip)
                    self._compact_combo(combo)
                    combo.currentIndexChanged.connect(self._on_option_changed)
                    og2.addWidget(combo, orow, 1)
                    self._option_widgets[opt.id] = combo
                else:
                    cb = QtWidgets.QCheckBox(opt.label)
                    cb.setChecked(opt.default)
                    if opt.tooltip:
                        cb.setToolTip(opt.tooltip)
                    cb.toggled.connect(self._on_option_changed)
                    og2.addWidget(cb, orow, 0, 1, 2)
                    self._option_widgets[opt.id] = cb
                orow += 1
            root.addWidget(opt_box)

        # Contour
        cont_box = QtWidgets.QGroupBox("Contour")
        cg = self._grid(cont_box)
        cg.addWidget(QtWidgets.QLabel("Resolution"), 0, 0)
        self.resolution = ResolutionField()
        self.resolution.changed.connect(self._on_resolution_changed)
        cg.addWidget(self.resolution, 0, 1)

        cg.addWidget(QtWidgets.QLabel("Color Map"), 1, 0)
        self.cb_colormap = QtWidgets.QComboBox()
        self.cb_colormap.addItems(list(COLORMAPS.keys()))
        self.cb_colormap.setCurrentText(self.measure.default_colormap)
        self._compact_combo(self.cb_colormap)
        self.cb_colormap.currentIndexChanged.connect(self._on_style_changed)
        cg.addWidget(self.cb_colormap, 1, 1)

        cg.addWidget(QtWidgets.QLabel("Range"), 2, 0)
        default_opts = {o.id: o.default for o in self.measure.options}
        lo0, hi0 = self.measure.initial_range(default_opts) or (0.0, 1.0)
        bmin, bmax = self.measure.value_limits(default_opts) or (-1.0e6, 1.0e6)
        range_row = QtWidgets.QHBoxLayout()
        range_row.setContentsMargins(0, 0, 0, 0)
        range_row.setSpacing(4)
        self.sb_range_lo = QtWidgets.QDoubleSpinBox()
        self.sb_range_hi = QtWidgets.QDoubleSpinBox()
        for sb, val in ((self.sb_range_lo, lo0), (self.sb_range_hi, hi0)):
            sb.setRange(bmin, bmax)
            sb.setDecimals(self.measure.range_decimals)
            sb.setSingleStep(self.measure.range_step)
            if self.measure.unit:
                sb.setSuffix(f" {self.measure.unit}")
            sb.setValue(val)
            sb.setToolTip("Set the color range. Also adjustable by dragging the legend ends.")
            sb.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed
            )
            sb.valueChanged.connect(self._on_range_spin)
        range_row.addWidget(self.sb_range_lo, 1)
        range_row.addWidget(QtWidgets.QLabel("to"), 0)
        range_row.addWidget(self.sb_range_hi, 1)
        range_w = QtWidgets.QWidget()
        range_w.setLayout(range_row)
        cg.addWidget(range_w, 2, 1)

        cg.addWidget(QtWidgets.QLabel("Bands"), 3, 0)
        self.cb_bands = QtWidgets.QComboBox()
        self.cb_bands.addItems(list(_BAND_STEPS.keys()))
        self.cb_bands.setToolTip("Quantize colors into fixed steps of the measured value.")
        self._compact_combo(self.cb_bands)
        self.cb_bands.currentIndexChanged.connect(self._on_style_changed)
        cg.addWidget(self.cb_bands, 3, 1)
        root.addWidget(cont_box)

        row = QtWidgets.QHBoxLayout()
        self.pb_generate = QtWidgets.QPushButton("Generate Contour")
        self.pb_generate.setMinimumHeight(30)
        self.pb_generate.clicked.connect(self._on_generate)
        self.pb_clear = QtWidgets.QPushButton("Clear")
        self.pb_clear.setMinimumHeight(30)
        self.pb_clear.setEnabled(False)
        self.pb_clear.clicked.connect(self._on_clear)
        row.addWidget(self.pb_generate, 1)
        row.addWidget(self.pb_clear, 1)
        root.addLayout(row)

        self.progress = QtWidgets.QProgressBar()
        self.progress.hide()
        root.addWidget(self.progress)

        root.addStretch(1)

    def _auto_select(self):
        sel = Gui.Selection.getSelection()
        if sel:
            self._apply_object(sel[0])
            Gui.Selection.clearSelection()

    def addSelection(self, *args):
        if self.picking_mode:
            QtCore.QTimer.singleShot(30, self._process_pick)

    def _process_pick(self):
        if self.picking_mode == "object":
            ok = self._apply_object_from_selection()
        elif self.picking_mode == "pull":
            ok = self._apply_pull_from_selection()
        else:
            return
        if ok:
            self.picking_mode = None
            self._reset_pick_ui()
            Gui.Selection.clearSelection()
            self._update_generate_state()

    def _enter_pick(self, mode, button, hint):
        self._reset_pick_ui()
        self.picking_mode = mode
        button.setChecked(True)
        button.setText(hint)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.cursor_overridden = True

    def _reset_pick_ui(self):
        self.pb_object.setChecked(False)
        self.pb_object.setText("Select Object")
        if self.pb_pull is not None:
            self.pb_pull.setChecked(False)
            self.pb_pull.setText("Select Pull Direction")
        if self.cursor_overridden:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.cursor_overridden = False

    def _on_escape(self):
        if self.picking_mode:
            self.picking_mode = None
            self._reset_pick_ui()
            return True
        return False

    def _on_pick_object(self):
        if self.picking_mode == "object":
            self.picking_mode = None
            self._reset_pick_ui()
            return
        if self._apply_object_from_selection():
            self._reset_pick_ui()
            Gui.Selection.clearSelection()
            self._update_generate_state()
            return
        self._enter_pick("object", self.pb_object, "Click an object")

    def _apply_object_from_selection(self):
        sel = Gui.Selection.getSelection()
        Gui.Selection.clearSelection()
        return self._apply_object(sel[0]) if sel else False

    def _apply_object(self, obj):
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            App.Console.PrintWarning("DFM contour: that object has no valid shape.\n")
            return False
        self.target_object = obj
        self.target_shape = shape
        self.le_object.setText(obj.Label)
        self.resolution.set_shape(shape)
        if self.indicator is not None and self.pull_anchor is None:
            self._refresh_arrow()
        self._mark_dirty()
        return True

    def _on_pick_pull(self):
        if self.picking_mode == "pull":
            self.picking_mode = None
            self._reset_pick_ui()
            return
        if self._apply_pull_from_selection():
            self._reset_pick_ui()
            Gui.Selection.clearSelection()
            return
        self._enter_pick("pull", self.pb_pull, "Click a face or edge")

    def _apply_pull_from_selection(self):
        try:
            sel = Gui.Selection.getSelectionEx()
            if not sel or not sel[0].SubObjects:
                return False
            sub = sel[0].SubObjects[0]
            name = sel[0].SubElementNames[0] if sel[0].SubElementNames else "Selected"
            if isinstance(sub, Part.Face):
                u0, u1, v0, v1 = sub.ParameterRange
                pnt = sub.valueAt((u0 + u1) * 0.5, (v0 + v1) * 0.5)
                direction = sub.normalAt((u0 + u1) * 0.5, (v0 + v1) * 0.5).normalize()
            elif isinstance(sub, Part.Edge):
                p0, p1 = sub.ParameterRange
                pm = (p0 + p1) * 0.5
                pnt = sub.valueAt(pm)
                tangent = sub.tangentAt(pm)
                if tangent.Length == 0:
                    return False
                direction = tangent.normalize()
            else:
                App.Console.PrintWarning("DFM contour: pick a face or an edge.\n")
                return False
            self.pull_dir = App.Vector(direction.x, direction.y, direction.z)
            self.pull_anchor = App.Vector(pnt.x, pnt.y, pnt.z)
            self.pull_ref = name
            self.pull_flipped = False
            self.le_pull.setText(name)
            self._refresh_arrow()
            self._mark_dirty()
            return True
        except Exception as exc:
            App.Console.PrintError(f"DFM contour: could not read pull direction. {exc}\n")
            return False

    def _on_flip_pull(self):
        self.pull_dir = App.Vector(-self.pull_dir.x, -self.pull_dir.y, -self.pull_dir.z)
        self.pull_flipped = not self.pull_flipped
        suffix = " (flipped)" if self.pull_flipped else ""
        self.le_pull.setText(f"{self.pull_ref}{suffix}")
        self._refresh_arrow()
        self._mark_dirty()

    def _refresh_arrow(self):
        if self.indicator is None:
            return
        anchor = self.pull_anchor
        if anchor is None and self.target_shape is not None:
            anchor = self.target_shape.BoundBox.Center
        if anchor is None:
            return
        self.indicator.show(anchor, App.Vector(self.pull_dir.x, self.pull_dir.y, self.pull_dir.z))

    def _band(self):
        return _BAND_STEPS.get(self.cb_bands.currentText(), 0.0)

    def _options(self):
        out = {}
        for oid, w in self._option_widgets.items():
            if isinstance(w, QtWidgets.QComboBox):
                out[oid] = w.currentText()
            else:
                out[oid] = w.isChecked()
        return out

    def _on_option_changed(self, _checked=False):
        opts = self._options()
        lim = self.measure.value_limits(opts) or (-1.0e6, 1.0e6)
        for sb in (self.sb_range_lo, self.sb_range_hi):
            sb.blockSignals(True)
            sb.setRange(lim[0], lim[1])
            sb.blockSignals(False)
        rng = self.measure.initial_range(opts)
        if rng is not None:
            self._set_range_spins(rng[0], rng[1])
        # Auto-ranged measures (rng is None) keep their current spin values;
        # a regenerate refits them to the data.
        self._mark_dirty()

    def _mark_dirty(self):
        if self._has_contour:
            self._dirty = True
            self._update_generate_state()

    def _on_resolution_changed(self):
        self._mark_dirty()
        self._update_generate_state()

    def _update_generate_state(self):
        valid = self.target_object is not None and self.resolution.is_safe()
        if not self._has_contour:
            self.pb_generate.setText("Generate Contour")
            self.pb_generate.setEnabled(valid)
            if self.target_object is None:
                tip = "Select an object first."
            elif not self.resolution.is_safe():
                tip = "Resolution is too fine; choose a coarser one."
            else:
                tip = "Run the contour."
        else:
            self.pb_generate.setText("Update Contour")
            self.pb_generate.setEnabled(valid and self._dirty)
            if self.target_object is None:
                tip = "Select an object first."
            elif not self.resolution.is_safe():
                tip = "Resolution is too fine; choose a coarser one."
            elif self._dirty:
                tip = "Apply the changed settings."
            else:
                tip = "Contour is up to date."
        self.pb_generate.setToolTip(tip)
        self.pb_clear.setEnabled(self._has_contour)

    def _on_generate(self):
        if self.target_object is None or self.target_shape is None:
            return
        if not self.resolution.is_safe():
            App.Console.PrintError("DFM contour: resolution too fine.\n")
            return

        self.picking_mode = None
        self._reset_pick_ui()
        size = self.resolution.element_size()

        self.pb_generate.setEnabled(False)
        self.progress.show()
        self.progress.setRange(0, 0)
        self.progress.setFormat("Meshing... %p%")
        QtWidgets.QApplication.processEvents()
        try:
            self._mesh = generate_uniform_mesh(self.target_shape, size)
        except Exception as exc:
            App.Console.PrintError(f"DFM contour: {exc}\n")
            self.progress.hide()
            self._update_generate_state()
            return
        self._measure_and_render()
        self._update_generate_state()

    def _measure_and_render(self):
        """Measure the cached mesh with current options and (re)draw. Used by
        Generate and by option toggles, so switching an option never remeshes."""
        if self._mesh is None:
            return
        pull = App.Vector(self.pull_dir).normalize() if self.measure.needs_pull_direction else None
        self.progress.show()
        self.progress.setRange(0, len(self._mesh.triangles))
        self.progress.setFormat("Measuring... %p%")
        try:

            def progress_cb(done, total):
                self.progress.setValue(done)
                QtWidgets.QApplication.processEvents(
                    QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                )

            values, normals = self.measure.measure(
                self.target_shape,
                self._mesh,
                pull=pull,
                options=self._options(),
                progress_cb=progress_cb,
            )
            dmin = min(values) if values else 0.0
            dmax = max(values) if values else 1.0
            self._auto_range = self.measure.initial_range(self._options()) is None
            self._last = (self._mesh, values, normals, dmin, dmax)
            self._render()
        except Exception as exc:
            App.Console.PrintError(f"DFM contour: {exc}\n")
            import traceback

            App.Console.PrintError(traceback.format_exc())
        finally:
            self.progress.hide()

    def _current_range(self):
        lo = self.sb_range_lo.value()
        hi = self.sb_range_hi.value()
        return (lo, hi) if lo <= hi else (hi, lo)

    def _set_range_spins(self, low, high):
        for sb, val in ((self.sb_range_lo, low), (self.sb_range_hi, high)):
            sb.blockSignals(True)
            sb.setValue(val)
            sb.blockSignals(False)

    def _on_range_spin(self):
        low, high = self._current_range()
        if self._node is not None:
            self._node.recolor(low, high, self.cb_colormap.currentText(), self._band())
        if self._legend is not None:
            self._legend.set_range(low, high)

    def _on_range_changed(self, low, high):
        self._set_range_spins(low, high)
        if self._node is not None:
            self._node.recolor(low, high, self.cb_colormap.currentText(), self._band())

    def _on_style_changed(self):
        if self._node is None:
            return
        low, high = self._current_range()
        colormap = self.cb_colormap.currentText()
        band = self._band()
        self._node.recolor(low, high, colormap, band)
        if self._legend is not None:
            self._legend.set_style(colormap, band)

    def _render(self):
        if self._last is None:
            return
        # Replace any existing overlay (this also serves re-renders on option change).
        self._remove_hover()
        clear_all()
        self._destroy_legend()
        self._node = None

        mesh, values, normals, dmin, dmax = self._last
        dom_lo, dom_hi = self.measure.value_limits(self._options()) or (dmin, dmax)
        if self._auto_range:
            low, high = dmin, dmax
            self._auto_range = False
        else:
            low, high = self._current_range()
        low = max(dom_lo, min(low, dom_hi))
        high = max(dom_lo, min(high, dom_hi))
        if low > high:
            low, high = high, low
        self._set_range_spins(low, high)
        colormap = self.cb_colormap.currentText()
        band = self._band()

        node = ContourNode(self.target_object)
        node.build(mesh.vertices, mesh.triangles, values, normals, low, high, colormap, band)
        node.attach()
        register(node)
        self._node = node

        view_widget = self._view_widget()
        if view_widget is not None:
            self._legend = ContourLegend(view_widget)
            self._legend.rangeChanged.connect(self._on_range_changed)
            self._legend.configure(
                self.measure.label,
                self.measure.unit,
                colormap,
                band,
                dom_lo,
                dom_hi,
                low,
                high,
                dmin,
                dmax,
            )
            self._legend.show()
            self._legend.raise_()
            self._position_legend()
        else:
            App.Console.PrintWarning("DFM contour: no 3D view widget found for the legend.\n")

        self._install_hover()
        self._has_contour = True
        self._dirty = False
        self._update_generate_state()

    def _position_legend(self):
        """Place the legend just to the right of the model by projecting the
        object's bounding box to screen. Falls back to the default corner spot."""
        if self._legend is None or self.target_shape is None:
            return
        try:
            view = Gui.ActiveDocument.ActiveView
            bb = self.target_shape.BoundBox
            corners = [
                App.Vector(x, y, z)
                for x in (bb.XMin, bb.XMax)
                for y in (bb.YMin, bb.YMax)
                for z in (bb.ZMin, bb.ZMax)
            ]
            xs, ys = [], []
            for c in corners:
                sx, sy = view.getPointOnScreen(c)
                xs.append(sx)
                ys.append(sy)
            parent = self._legend.parentWidget()
            if parent is None:
                return
            right_x = max(xs) + 24
            center_y = parent.height() - (min(ys) + max(ys)) / 2.0
            wx = int(min(max(right_x, 0), max(0, parent.width() - self._legend.width())))
            wy = int(
                min(
                    max(center_y - self._legend.height() / 2, 0),
                    max(0, parent.height() - self._legend.height()),
                )
            )
            self._legend.move(wx, wy)
        except Exception:
            pass  # keep the default placement

    def _on_clear(self):
        self._remove_hover()
        clear_all()
        self._destroy_legend()
        self._node = None
        self._last = None
        self._mesh = None
        self._has_contour = False
        self._dirty = False
        self._update_generate_state()

    def _gather_params(self):
        resolution, element_size = self.resolution.state()
        low, high = self._current_range()
        pull = None
        if self.measure.needs_pull_direction:
            pull = (self.pull_dir.x, self.pull_dir.y, self.pull_dir.z)
        return {
            "source": self.target_object,
            "measure": self.measure.id,
            "pull_direction": pull,
            "pull_reference": self.pull_ref,
            "resolution": resolution,
            "element_size": element_size,
            "colormap": self.cb_colormap.currentText(),
            "range_low": low,
            "range_high": high,
            "bands": self.cb_bands.currentText(),
            "options": self._options(),
        }

    def _on_save(self):
        if self._last is None:
            return
        mesh, values, normals, dmin, dmax = self._last
        field = {
            "vertices": list(mesh.vertices),
            "triangles": [tuple(t) for t in mesh.triangles],
            "values": list(values),
            "normals": list(normals),
            "dmin": dmin,
            "dmax": dmax,
        }
        try:
            from .document import create_or_update_analysis

            self._analysis_obj = create_or_update_analysis(
                self._analysis_obj, self._gather_params(), field
            )
            self._saved = True
        except Exception as exc:
            App.Console.PrintError(f"DFM analysis: could not save. {exc}\n")
            return
        # The saved object now owns a persistent overlay; drop our preview and close.
        self._teardown()

    def _load_from_object(self, obj):
        try:
            src = getattr(obj, "Source", None)
            if src is not None:
                self._apply_object(src)
            self.resolution.set_state(
                getattr(obj, "Resolution", ""), getattr(obj, "ElementSize", 0.0)
            )
            if obj.ColorMap:
                self.cb_colormap.setCurrentText(obj.ColorMap)
            if obj.Bands:
                self.cb_bands.setCurrentText(obj.Bands)
            for oid, w in self._option_widgets.items():
                if oid in (obj.Options or {}):
                    val = obj.Options[oid]
                    if isinstance(w, QtWidgets.QComboBox):
                        w.setCurrentText(str(val))
                    else:
                        w.setChecked(bool(val))
            if self.measure.needs_pull_direction:
                self.pull_dir = App.Vector(obj.PullDirection)
                self.pull_ref = obj.PullReference or self.pull_ref
                self.le_pull.setText(self.pull_ref)
                self._refresh_arrow()
            self._set_range_spins(obj.RangeLow, obj.RangeHigh)

            field = getattr(obj, "FieldData", None)
            if field:
                import types

                stub = types.SimpleNamespace(
                    vertices=field["vertices"], triangles=field["triangles"]
                )
                self._auto_range = False
                self._last = (
                    stub,
                    field["values"],
                    field["normals"],
                    field.get("dmin", 0.0),
                    field.get("dmax", 1.0),
                )
                self._mesh = None  # a re-mesh is needed to Update; static until then
                self._render()
        except Exception as exc:
            App.Console.PrintError(f"DFM analysis: could not load. {exc}\n")

    def _view_widget(self):
        try:
            mw = Gui.getMainWindow()
            mdi = mw.findChild(QtWidgets.QMdiArea)
            if mdi is None:
                return None
            sub = mdi.activeSubWindow() or mdi.currentSubWindow()
            return sub.widget() if sub is not None else None
        except Exception:
            return None

    def _destroy_legend(self):
        if self._legend is not None:
            try:
                self._legend.cleanup()
                self._legend.setParent(None)
                self._legend.deleteLater()
            except Exception:
                pass
            self._legend = None

    def _install_hover(self):
        if self._hover_cb is not None:
            return
        gui_doc = Gui.ActiveDocument
        if gui_doc is None or gui_doc.ActiveView is None:
            return
        view = gui_doc.ActiveView
        try:
            self._last_hover_t = 0.0
            self._last_face_key = None
            self._hover_cb = view.addEventCallbackPivy(
                coin.SoLocation2Event.getClassTypeId(), self._on_hover
            )
            self._hover_view = view
        except Exception as exc:
            App.Console.PrintWarning(f"DFM contour: hover unavailable. {exc}\n")

    def _remove_hover(self):
        if self._hover_cb is not None and self._hover_view is not None:
            try:
                self._hover_view.removeEventCallbackPivy(
                    coin.SoLocation2Event.getClassTypeId(), self._hover_cb
                )
            except Exception:
                pass
        self._hover_cb = None
        self._hover_view = None
        self._last_face_key = None
        self._hide_hover_label()
        if self._legend is not None:
            self._legend.set_marker(None)

    def _hover_label_widget(self):
        if self._hover_label is not None:
            return self._hover_label
        view_widget = self._view_widget()
        if view_widget is None:
            return None
        label = QtWidgets.QLabel(view_widget)
        label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.setStyleSheet(
            "QLabel { background: rgba(20,20,22,180); color: #fff;"
            " border: 1px solid rgba(255,255,255,70); border-radius: 4px;"
            " padding: 2px 6px; font-weight: bold; }"
        )
        self._hover_label = label
        return self._hover_label

    def _place_hover_label(self, label):
        parent = label.parentWidget()
        if parent is None:
            return
        pos = parent.mapFromGlobal(QtGui.QCursor.pos()) + QtCore.QPoint(14, 14)
        pos.setX(min(max(pos.x(), 0), max(0, parent.width() - label.width())))
        pos.setY(min(max(pos.y(), 0), max(0, parent.height() - label.height())))
        label.move(pos)

    def _hide_hover_label(self):
        if self._hover_label is not None:
            self._hover_label.hide()

    def _on_hover(self, event_cb):
        node = self._node
        if node is None:
            return
        now = time.monotonic()
        if now - self._last_hover_t < self._hover_interval:
            return
        self._last_hover_t = now
        try:
            result = node.pick_value(event_cb.getPickedPoint())
        except Exception:
            result = None

        if result is None:
            self._hide_hover_label()
            if self._legend is not None:
                self._legend.set_marker(None)
            if self._last_face_key is not None:
                Gui.getMainWindow().statusBar().clearMessage()
                self._last_face_key = None
            return

        key, value, _point = result
        label = self._hover_label_widget()
        if key != self._last_face_key:
            self._last_face_key = key
            if label is not None:
                label.setText(self.measure.format_value(value))
                label.adjustSize()
            Gui.getMainWindow().statusBar().showMessage(
                f"{self.measure.label}: {self.measure.format_value(value)}"
            )
        if self._legend is not None:
            self._legend.set_marker(value)
        if label is not None:
            self._place_hover_label(label)
            label.show()
            label.raise_()

    def getStandardButtons(self):
        return (
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Close
        )

    def _teardown(self):
        self._reset_pick_ui()
        self._remove_hover()
        if self._hover_label is not None:
            self._hover_label.deleteLater()
            self._hover_label = None
        self._destroy_legend()
        clear_all()  # drop the live preview overlay
        if self.indicator is not None:
            self.indicator.remove()
        try:
            Gui.Selection.removeObserver(self)
        except Exception:
            pass
        Gui.Control.closeDialog()

    def reject(self):
        self._teardown()

    def accept(self):
        self._on_save()
        if not self._saved:
            self._teardown()
