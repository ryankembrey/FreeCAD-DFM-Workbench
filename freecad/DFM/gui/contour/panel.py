# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


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


from ...app.contour.meshing import generate_uniform_mesh
from ...app.contour.colormap import COLORMAPS
from ...app.contour.measures import BoolOption, ChoiceOption

from .scene import ContourNode, register, clear_all
from .legend import ContourLegend
from .resolution import ResolutionField
from . import document  # noqa: F401

PICK_BUTTON_STYLE = """
QPushButton:checked { border: 2px solid palette(highlight); font-weight: bold; }
"""


_LEGEND_STATE = {"pos": None, "size": None, "horizontal": False}

_BAND_STEPS = {
    "Smooth": 0.0,
    "1 unit bands": 1.0,
    "2 unit bands": 2.0,
    "5 unit bands": 5.0,
    "10 unit bands": 10.0,
}


def _probe_ray_pick(view, event):
    try:
        pos = event.getPosition()
        size = view.getSize()
        if not size or size[0] <= 0 or size[1] <= 0:
            return None
        render_manager = view.getViewer().getSoRenderManager()
        viewport_region = render_manager.getViewportRegion()
        scene = render_manager.getSceneGraph()

        action = coin.SoRayPickAction(viewport_region)
        action.setPoint(coin.SbVec2s(int(pos[0]), int(pos[1])))
        action.setPickAll(True)
        action.apply(scene)

        picked_list = action.getPickedPointList()
        try:
            count = picked_list.getLength()
        except Exception:
            count = len(picked_list)
        for i in range(count):
            path = picked_list[i].getPath()
            if path is None:
                continue
            for j in range(path.getLength()):
                node = path.getNode(j)
                try:
                    type_name = node.getTypeId().getName().getString()
                except Exception:
                    continue
                if type_name == "SoFCSelection":
                    try:
                        doc_name = node.documentName.getValue().getString()
                        obj_name = node.objectName.getValue().getString()
                    except Exception:
                        continue
                    if doc_name and obj_name:
                        return (doc_name, obj_name)
    except Exception:
        return None
    return None


class _EscapeFilter(QtCore.QObject):
    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.KeyPress:
            if event.key() == QtCore.Qt.Key.Key_Escape and self._callback():
                return True
        return False


class _SpinBoxEnterFilter(QtCore.QObject):
    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.KeyPress:
            if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                if hasattr(obj, "interpretText"):
                    obj.interpretText()
                obj.clearFocus()
                return True
        return False


class ContourTaskPanel:
    def __init__(self, measure, title, icon=":/icons/dfm_analysis.svg", analysis_obj=None):
        self.measure = measure
        self._title = title
        self._icon = icon
        self._analysis_obj = analysis_obj
        self._analysis_persisted = analysis_obj is not None
        self._saved = False
        self._auto_range = False
        self._range_initialized = False

        self.target_object = None
        self.target_shape = None

        self.pull_dir = App.Vector(0, 0, 1)
        self.pull_ref = "+Z (default)"
        self.pull_flipped = False
        self.pull_anchor = None
        self.indicator = (
            DirectionIndicator((1.0, 0.15, 0.15), "Pull Direction")
            if measure.needs_pull_direction
            else None
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
        self._probe_seq = 0
        self._click_cb = None
        self._pending_new_probe = None
        self._consumed_button_downs = set()
        self._hovered_probe = None
        self._selected_probes = set()
        self._suppress_prop_sync = False
        self._legend_obj = None
        self._direction_obj = None
        self._suppress_legend_sync = False
        self._applied_text_rgb = None

        self._build_form()
        Gui.Selection.addObserver(self)
        self._escape_filter = _EscapeFilter(self._on_escape)
        self.form.installEventFilter(self._escape_filter)
        if self._analysis_obj is not None:
            try:
                self._analysis_obj.Proxy._live_panel = self
            except Exception:
                pass
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

        cont_box = QtWidgets.QGroupBox("Contour")
        cg = self._grid(cont_box)
        cg.addWidget(QtWidgets.QLabel("Resolution"), 0, 0)
        self.resolution = ResolutionField()
        self.resolution.changed.connect(self._on_resolution_changed)
        cg.addWidget(self.resolution, 0, 1)

        cg.addWidget(QtWidgets.QLabel("Color map"), 1, 0)
        self.cb_colormap = QtWidgets.QComboBox()
        self.cb_colormap.addItems(list(COLORMAPS.keys()))
        self.cb_colormap.setCurrentText(self.measure.default_colormap)
        self._compact_combo(self.cb_colormap)
        self.cb_colormap.currentIndexChanged.connect(self._on_style_changed)
        cg.addWidget(self.cb_colormap, 1, 1)

        self.lbl_range = QtWidgets.QLabel("Range")
        cg.addWidget(self.lbl_range, 2, 0)
        default_opts = {o.id: o.default for o in self.measure.options}
        lo0, hi0 = self.measure.initial_range(default_opts) or (0.0, 1.0)
        bmin, bmax = self.measure.value_limits(default_opts) or (-1.0e6, 1.0e6)
        range_row = QtWidgets.QHBoxLayout()
        range_row.setContentsMargins(0, 0, 0, 0)
        range_row.setSpacing(4)
        self.sb_range_lo = QtWidgets.QDoubleSpinBox()
        self.sb_range_hi = QtWidgets.QDoubleSpinBox()
        self._spinbox_enter_filter = _SpinBoxEnterFilter(self.form)
        range_decimals = max(int(self.measure.range_decimals), 2)
        range_step = min(self.measure.range_step, 0.1)
        for sb, val in ((self.sb_range_lo, lo0), (self.sb_range_hi, hi0)):
            sb.setRange(bmin, bmax)
            sb.setDecimals(range_decimals)
            sb.setSingleStep(range_step)
            if self.measure.unit:
                sb.setSuffix(f" {self.measure.unit}")
            sb.setValue(val)
            sb.setToolTip("Set the color range. Also adjustable by dragging the legend ends.")
            sb.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed
            )
            sb.installEventFilter(self._spinbox_enter_filter)
            sb.valueChanged.connect(self._on_range_spin)
        range_row.addWidget(self.sb_range_lo, 1)
        range_row.addWidget(QtWidgets.QLabel("to"), 0)
        range_row.addWidget(self.sb_range_hi, 1)
        range_w = QtWidgets.QWidget()
        range_w.setLayout(range_row)
        cg.addWidget(range_w, 2, 1)

        cg.addWidget(QtWidgets.QLabel("Bands"), 4, 0)
        self.cb_bands = QtWidgets.QComboBox()
        self.cb_bands.addItems(list(_BAND_STEPS.keys()))
        self.cb_bands.setToolTip("Quantize colors into fixed steps of the measured value.")
        self._compact_combo(self.cb_bands)
        self.cb_bands.currentIndexChanged.connect(self._on_style_changed)
        cg.addWidget(self.cb_bands, 4, 1)

        self.cb_smooth = QtWidgets.QCheckBox("Smooth shading")
        self.cb_smooth.setToolTip(
            "Blend colors across triangles so the mesh facets disappear. "
            "Off shows one flat color per triangle."
        )
        self.cb_smooth.toggled.connect(self._on_smooth_changed)
        cg.addWidget(self.cb_smooth, 5, 0, 1, 2)

        self.cb_highlight = QtWidgets.QCheckBox("Highlight range")
        self.cb_highlight.setToolTip(
            "Show a pass/fail view: values within the Range above are flagged "
            "red, everything else muted gray. The Range spin boxes and the "
            "legend handles set the flagged band."
        )
        self.cb_highlight.toggled.connect(self._on_highlight_toggled)
        cg.addWidget(self.cb_highlight, 3, 0, 1, 2)
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
        self._observe_selection_add(args)

    def removeSelection(self, *args):
        self._observe_selection_remove(args)

    def clearSelection(self, *args):
        self._observe_selection_clear()

    def setSelection(self, *args):
        self._observe_selection_resync()

    def _selection_key_from_args(self, args):
        if len(args) >= 2 and args[0] and args[1]:
            return (str(args[0]), str(args[1]))
        return None

    def _observe_selection_add(self, args):
        key = self._selection_key_from_args(args)
        if key is None:
            return
        if key == self._pending_new_probe:
            self._pending_new_probe = None
            self._deselect_probe(key)
            return
        if key not in self._selected_probes:
            self._selected_probes.add(key)
            self._apply_probe_state(key)

    def _observe_selection_remove(self, args):
        key = self._selection_key_from_args(args)
        if key is None:
            return
        if key in self._selected_probes:
            self._selected_probes.discard(key)
            self._apply_probe_state(key)

    def _observe_selection_clear(self):
        cleared = list(self._selected_probes)
        self._selected_probes.clear()
        for key in cleared:
            self._apply_probe_state(key)

    def _observe_selection_resync(self):
        try:
            live = set()
            for sel in Gui.Selection.getSelectionEx():
                doc_obj = sel.Object
                if doc_obj is not None:
                    live.add((doc_obj.Document.Name, doc_obj.Name))
        except Exception:
            return
        changed = self._selected_probes.symmetric_difference(live)
        self._selected_probes = live
        for key in changed:
            self._apply_probe_state(key)

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
        if self._selected_probes:
            try:
                Gui.Selection.clearSelection()
            except Exception:
                pass
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

    def _highlight_spec(self):
        from ...app.contour.colormap import HighlightSpec

        if not getattr(self, "cb_highlight", None) or not self.cb_highlight.isChecked():
            return HighlightSpec(active=False)
        lo, hi = self._current_range()
        return HighlightSpec(active=True, lo=lo, hi=hi)

    def _on_highlight_toggled(self, _checked=False):
        spec = self._highlight_spec()
        if self._node is not None:
            low, high = self._current_range()
            self._node.recolor(low, high, self.cb_colormap.currentText(), self._band(), spec)
        if self._legend is not None:
            self._legend.set_highlight(spec)
        self._update_range_label()

    def _update_range_label(self):
        on = bool(getattr(self, "cb_highlight", None) and self.cb_highlight.isChecked())
        if hasattr(self, "lbl_range"):
            self.lbl_range.setText("Flagged band" if on else "Range")

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
        self._reset_text_color_override()
        self._update_generate_state()

    def _measure_and_render(self):
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
            self._last = (self._mesh, values, normals, dmin, dmax)
            self._render()
            self._ensure_analysis_object()
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
        spec = self._highlight_spec()
        if self._node is not None:
            self._node.recolor(low, high, self.cb_colormap.currentText(), self._band(), spec)
        if self._legend is not None:
            self._legend.set_range(low, high)
            self._legend.set_highlight(spec)

    def _on_range_changed(self, low, high):
        self._set_range_spins(low, high)
        spec = self._highlight_spec()
        if self._node is not None:
            self._node.recolor(low, high, self.cb_colormap.currentText(), self._band(), spec)
        if self._legend is not None:
            self._legend.set_highlight(spec)

    def _on_style_changed(self):
        if self._node is None:
            return
        low, high = self._current_range()
        colormap = self.cb_colormap.currentText()
        band = self._band()
        self._node.recolor(low, high, colormap, band)
        if self._legend is not None:
            self._legend.set_style(colormap, band)

    def _on_smooth_changed(self, _checked=False):
        if self._last is not None:
            self._render()

    def apply_property_change(self, prop):
        if self._suppress_prop_sync or self._analysis_obj is None:
            return
        obj = self._analysis_obj
        self._suppress_prop_sync = True
        try:
            if prop == "ColorMap":
                val = getattr(obj, "ColorMap", "")
                if val and val != self.cb_colormap.currentText():
                    self.cb_colormap.setCurrentText(val)
            elif prop == "Bands":
                val = getattr(obj, "Bands", "")
                if val and val != self.cb_bands.currentText():
                    self.cb_bands.setCurrentText(val)
            elif prop == "Smooth":
                val = bool(getattr(obj, "Smooth", False))
                if val != self.cb_smooth.isChecked():
                    self.cb_smooth.setChecked(val)
            elif prop in ("RangeLow", "RangeHigh"):
                low = float(getattr(obj, "RangeLow", 0.0))
                high = float(getattr(obj, "RangeHigh", 0.0))
                if (low, high) != self._current_range():
                    self._set_range_spins(low, high)
                    self._on_range_spin()
            elif prop == "HighlightActive":
                val = bool(getattr(obj, "HighlightActive", False))
                if val != self.cb_highlight.isChecked():
                    self.cb_highlight.setChecked(val)
        except Exception as exc:
            App.Console.PrintWarning(f"DFM contour: property sync failed. {exc}\n")
        finally:
            self._suppress_prop_sync = False

    def _render(self):
        if self._last is None:
            return
        self._remove_hover()
        clear_all()
        self._destroy_legend()
        self._node = None

        mesh, values, normals, dmin, dmax = self._last
        dom_lo, dom_hi = self.measure.value_limits(self._options()) or (dmin, dmax)
        if not self._range_initialized:
            rng = self.measure.initial_range(self._options())
            low, high = (dmin, dmax) if rng is None else rng
            self._range_initialized = True
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
        smooth = self.cb_smooth.isChecked()
        span = dom_hi - dom_lo
        value_gap = 0.5 * span if (smooth and span > 0) else None
        node.build(
            mesh.vertices,
            mesh.triangles,
            values,
            normals,
            low,
            high,
            colormap,
            band,
            smooth,
            value_gap,
            self._highlight_spec(),
        )
        node.attach()
        register(node)
        self._node = node

        view_widget = self._view_widget()
        if view_widget is not None:
            self._legend = ContourLegend(view_widget)
            self._legend.rangeChanged.connect(self._on_range_changed)
            self._legend.colormapChanged.connect(self._on_legend_colormap)
            self._legend.bandsChanged.connect(self._on_legend_bands)
            self._legend.fitRequested.connect(self._fit_to_data)
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
            self._legend.set_highlight(self._highlight_spec())
            self._update_range_label()
            self._legend.show()
            self._legend.raise_()
            self._restore_legend_geometry()
            try:
                self._legend.orientationChanged.connect(
                    lambda _h=False: self._sync_legend_object_orientation()
                )
            except Exception:
                pass
            self._ensure_legend_object()
        else:
            App.Console.PrintWarning("DFM contour: no 3D view widget found for the legend.\n")

        self._ensure_direction_object()

        self._install_hover()
        self._set_probes_visible(True)
        self._has_contour = True
        self._dirty = False
        self._update_generate_state()

    def _on_legend_colormap(self, name):
        if name and name != self.cb_colormap.currentText():
            self.cb_colormap.setCurrentText(name)

    def _on_legend_bands(self, name):
        if name and name != self.cb_bands.currentText():
            self.cb_bands.setCurrentText(name)

    def _fit_to_data(self):
        if self._last is None:
            return
        _, _, _, dmin, dmax = self._last
        dom_lo, dom_hi = self.measure.value_limits(self._options()) or (dmin, dmax)
        low = max(dom_lo, min(dmin, dom_hi))
        high = max(dom_lo, min(dmax, dom_hi))
        if low > high:
            low, high = high, low
        self._set_range_spins(low, high)
        spec = self._highlight_spec()
        if self._node is not None:
            self._node.recolor(low, high, self.cb_colormap.currentText(), self._band(), spec)
        if self._legend is not None:
            self._legend.set_range(low, high)
            self._legend.set_highlight(spec)

    def _restore_legend_geometry(self):
        if self._legend is None:
            return
        size = _LEGEND_STATE.get("size")
        pos = _LEGEND_STATE.get("pos")
        if size is None or pos is None:
            self._position_legend()
            return
        try:
            self._legend.set_orientation(_LEGEND_STATE.get("horizontal", False))
            self._legend.resize(size)
            self._legend.move(pos)
            self._legend._clamp_into_parent()
        except Exception:
            self._position_legend()

    def _position_legend(self):
        if self._legend is None:
            return
        parent = self._legend.parentWidget()
        if parent is None:
            return
        try:
            self._legend.set_orientation(True)
            pw = parent.width()
            w = min(max(320, pw // 3), max(200, pw - 40))
            h = 96
            self._legend.resize(w, h)
            x = (pw - w) // 2
            right_limit = int(pw * 0.62)
            if x + w > right_limit:
                x = max(8, right_limit - w)
            self._legend.move(x, 12)
        except Exception:
            pass

    def _on_clear(self):
        self._remove_hover()
        clear_all()
        self._destroy_legend()
        self._node = None
        self._last = None
        self._mesh = None
        self._has_contour = False
        self._dirty = False
        self._range_initialized = False
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
            "smooth": self.cb_smooth.isChecked(),
            "highlight_active": self.cb_highlight.isChecked(),
            "options": self._options(),
        }

    def _build_field(self):
        if self._last is None:
            return None
        mesh, values, normals, dmin, dmax = self._last
        return {
            "vertices": list(mesh.vertices),
            "triangles": [tuple(t) for t in mesh.triangles],
            "values": list(values),
            "normals": list(normals),
            "dmin": dmin,
            "dmax": dmax,
        }

    def _ensure_analysis_object(self):
        field = self._build_field()
        if field is None:
            return None
        if self._analysis_obj is None:
            from .document import create_or_update_analysis

            try:
                self._analysis_obj = create_or_update_analysis(None, self._gather_params(), field)
                try:
                    self._analysis_obj.Proxy._live_panel = self
                except Exception:
                    pass
            except Exception as exc:
                App.Console.PrintError(f"DFM contour: could not create analysis object. {exc}\n")
                return None
        else:
            try:
                self._analysis_obj.Proxy.store(self._analysis_obj, self._gather_params(), field)
                self._analysis_obj.touch()
                App.ActiveDocument.recompute()
            except Exception as exc:
                App.Console.PrintError(f"DFM contour: could not update analysis object. {exc}\n")
        return self._analysis_obj

    def _on_save(self):
        analysis = self._ensure_analysis_object()
        if analysis is None:
            return
        self._saved = True
        self._analysis_persisted = True
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
            self.cb_smooth.setChecked(bool(getattr(obj, "Smooth", False)))
            self.cb_highlight.blockSignals(True)
            self.cb_highlight.setChecked(bool(getattr(obj, "HighlightActive", False)))
            self.cb_highlight.blockSignals(False)
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
                self._range_initialized = True
                self._last = (
                    stub,
                    field["values"],
                    field["normals"],
                    field.get("dmin", 0.0),
                    field.get("dmax", 1.0),
                )
                self._mesh = None
                self._render()

            from .document import _probe_children

            existing = _probe_children(obj)
            self._probe_seq = len(existing)
        except Exception as exc:
            App.Console.PrintError(f"DFM analysis: could not load. {exc}\n")

    def _view_widget(self) -> Optional[QtWidgets.QWidget]:
        try:
            mw = Gui.getMainWindow()
            mdi = mw.findChild(QtWidgets.QMdiArea)
            if mdi is None:
                return None
            sub = mdi.activeSubWindow() or mdi.currentSubWindow()
            return sub.widget() if sub is not None else None
        except Exception:
            return None

    def _ensure_legend_object(self):
        analysis = self._ensure_analysis_object()
        if analysis is None or self._legend is None:
            return
        from .document import ensure_legend_object

        horizontal = self._legend.orientation_horizontal()
        try:
            self._legend_obj = ensure_legend_object(analysis, horizontal)
        except Exception as exc:
            App.Console.PrintWarning(f"DFM contour: could not create legend item. {exc}\n")
            return
        obj = self._legend_obj
        if obj is None:
            return
        self._suppress_legend_sync = True
        try:
            self._legend.set_orientation(getattr(obj, "Orientation", "Horizontal") != "Vertical")
            if App.GuiUp and obj.ViewObject is not None:
                self._legend.setVisible(bool(obj.ViewObject.Visibility))
        except Exception:
            pass
        finally:
            self._suppress_legend_sync = False
        self._apply_legend_text_color()

    def _reset_text_color_override(self):
        obj = self._legend_obj
        if obj is None:
            self._apply_legend_text_color()
            return
        try:
            from .document import auto_legend_text_rgb

            proxy = obj.Proxy
            proxy._setting_auto_flag = True
            try:
                obj.AutoTextColor = True
                obj.TextColor = auto_legend_text_rgb()
            finally:
                proxy._setting_auto_flag = False
        except Exception:
            pass
        self._apply_legend_text_color()

    def apply_legend_text_color(self):
        self._apply_legend_text_color()

    def _apply_legend_text_color(self):
        if self._legend is None or self._analysis_obj is None:
            return
        from .document import legend_text_rgb

        try:
            rgb = legend_text_rgb(self._analysis_obj)
        except Exception:
            return
        if rgb == getattr(self, "_applied_text_rgb", None):
            return
        self._applied_text_rgb = rgb
        try:
            self._legend.set_text_color(rgb)
        except Exception:
            pass

    def _destroy_legend(self):
        if self._legend is not None:
            try:
                _LEGEND_STATE["pos"] = self._legend.pos()
                _LEGEND_STATE["size"] = self._legend.size()
                _LEGEND_STATE["horizontal"] = self._legend.orientation_horizontal()
            except Exception:
                pass
            try:
                self._legend.cleanup()
                self._legend.setParent(None)
                self._legend.deleteLater()
            except Exception:
                pass
            self._legend = None
        self._applied_text_rgb = None

    def apply_legend_orientation(self, vertical):
        if self._suppress_legend_sync or self._legend is None:
            return
        self._suppress_legend_sync = True
        try:
            self._legend.set_orientation(not vertical)
        except Exception:
            pass
        finally:
            self._suppress_legend_sync = False

    def apply_legend_visible(self, visible):
        if self._legend is None:
            return
        try:
            self._legend.setVisible(bool(visible))
        except Exception:
            pass

    def on_legend_deleted(self):
        self._legend_obj = None
        self._destroy_legend()

    def _ensure_direction_object(self):
        if self.indicator is None:
            return
        analysis = self._ensure_analysis_object()
        if analysis is None:
            return
        from .document import ensure_direction_object

        try:
            self._direction_obj = ensure_direction_object(analysis)
        except Exception as exc:
            App.Console.PrintWarning(f"DFM contour: could not create direction item. {exc}\n")
            return
        obj = self._direction_obj
        if obj is None:
            return
        if App.GuiUp and obj.ViewObject is not None:
            try:
                self.indicator.set_visible(bool(obj.ViewObject.Visibility))
            except Exception:
                pass

    def apply_indicator_visible(self, visible):
        if self.indicator is None:
            return
        try:
            self.indicator.set_visible(bool(visible))
        except Exception:
            pass

    def on_indicator_deleted(self):
        self._direction_obj = None
        if self.indicator is not None:
            try:
                self.indicator.remove()
            except Exception:
                pass

    def _sync_legend_object_orientation(self):
        if self._suppress_legend_sync or self._legend_obj is None or self._legend is None:
            return
        self._suppress_legend_sync = True
        try:
            want = "Horizontal" if self._legend.orientation_horizontal() else "Vertical"
            if getattr(self._legend_obj, "Orientation", None) != want:
                self._legend_obj.Orientation = want
        except Exception:
            pass
        finally:
            self._suppress_legend_sync = False

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
            self._consumed_button_downs.clear()
            self._hover_cb = view.addEventCallbackPivy(
                coin.SoLocation2Event.getClassTypeId(), self._on_hover
            )
            self._click_cb = view.addEventCallbackPivy(
                coin.SoMouseButtonEvent.getClassTypeId(), self._on_click
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
        if getattr(self, "_click_cb", None) is not None and self._hover_view is not None:
            try:
                self._hover_view.removeEventCallbackPivy(
                    coin.SoMouseButtonEvent.getClassTypeId(), self._click_cb
                )
            except Exception:
                pass
            self._click_cb = None
        self._reset_hover_cursor()
        self._hover_cb = None
        self._hover_view = None
        self._last_face_key = None
        self._set_hovered_probe(None)
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

    def _gl_widget(self):
        vw = self._view_widget()
        if vw is None:
            return None
        try:
            for w in vw.findChildren(QtWidgets.QWidget):
                if w is self._legend:
                    continue
                cn = w.metaObject().className().lower()
                if any(k in cn for k in ("quarter", "glarea", "viewer", "soqt", "opengl")):
                    return w
        except Exception:
            pass
        return vw

    def _set_hover_cursor(self, on_contour):
        if getattr(self, "_cursor_cross", None) == on_contour:
            return
        self._cursor_cross = on_contour
        w = self._gl_widget()
        if w is None:
            return
        self._cursor_widget = w
        try:
            if on_contour:
                w.setCursor(QtCore.Qt.CursorShape.CrossCursor)
            else:
                w.unsetCursor()
        except Exception:
            pass

    def _reset_hover_cursor(self):
        w = getattr(self, "_cursor_widget", None)
        if w is not None:
            try:
                w.unsetCursor()
            except Exception:
                pass
        self._cursor_widget = None
        self._cursor_cross = None

    def _apply_probe_state(self, key):
        from .document import (
            ProbeState,
            probe_highlight_proxy,
        )

        if key in self._selected_probes:
            state = ProbeState.SELECTED
        elif key == self._hovered_probe:
            state = ProbeState.HOVER
        else:
            state = ProbeState.NONE
        proxy = probe_highlight_proxy(key[0], key[1])
        if proxy is not None:
            proxy.set_highlight_state(state)

    def _set_hovered_probe(self, key):
        if key == self._hovered_probe:
            return
        previous = self._hovered_probe
        self._hovered_probe = key
        if previous is not None:
            self._apply_probe_state(previous)
        if key is not None:
            self._apply_probe_state(key)

    def _probe_at_cursor(self, event_cb):
        view = self._hover_view
        if view is None:
            return None
        return _probe_ray_pick(view, event_cb.getEvent())

    def _on_hover(self, event_cb):
        now = time.monotonic()
        if now - getattr(self, "_last_text_check_t", 0.0) > 0.5:
            self._last_text_check_t = now
            self._apply_legend_text_color()
        if now - self._last_hover_t < self._hover_interval:
            return
        self._last_hover_t = now

        probe_hit = self._probe_at_cursor(event_cb)
        self._set_hovered_probe(probe_hit)
        if probe_hit is not None:
            self._set_hover_cursor(False)
            self._hide_hover_label()
            if self._legend is not None:
                self._legend.set_marker(None)
            if self._last_face_key is not None:
                Gui.getMainWindow().statusBar().clearMessage()
                self._last_face_key = None
            return

        node = self._node
        if node is None:
            self._set_hover_cursor(False)
            self._hide_hover_label()
            return
        try:
            result = node.pick_value(event_cb.getPickedPoint())
        except Exception:
            result = None

        if result is None:
            self._set_hover_cursor(False)
            self._hide_hover_label()
            if self._legend is not None:
                self._legend.set_marker(None)
            if self._last_face_key is not None:
                Gui.getMainWindow().statusBar().clearMessage()
                self._last_face_key = None
            return

        self._set_hover_cursor(True)
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

    def _on_click(self, event_cb):
        event = event_cb.getEvent()
        button = event.getButton()
        state = event.getState()

        if state == coin.SoButtonEvent.UP:
            consumed = button in self._consumed_button_downs
            if consumed:
                self._consumed_button_downs.discard(button)
                event_cb.setHandled()
            if (
                consumed
                and button == coin.SoMouseButtonEvent.BUTTON2
                and self._pending_ctx_hit is not None
            ):
                hit = self._pending_ctx_hit
                self._pending_ctx_hit = None
            if button != coin.SoMouseButtonEvent.BUTTON1:
                return

        if button == coin.SoMouseButtonEvent.BUTTON2:
            if state != coin.SoButtonEvent.DOWN:
                return
            if self.picking_mode:
                return
            hit = self._probe_at_cursor(event_cb)
            if hit is not None:
                self._pending_ctx_hit = hit
                self._consumed_button_downs.add(button)
                event_cb.setHandled()
            return

        if button != coin.SoMouseButtonEvent.BUTTON1:
            return

        if state == coin.SoButtonEvent.UP:
            return

        if state != coin.SoButtonEvent.DOWN:
            return
        if self.picking_mode:
            return
        hit = self._probe_at_cursor(event_cb)
        if hit is not None:
            doc_name, obj_name = hit
            try:
                ctrl = bool(event.wasCtrlDown())
            except Exception:
                ctrl = False
            try:
                if ctrl:
                    if (doc_name, obj_name) in self._selected_probes:
                        self._deselect_probe((doc_name, obj_name))
                    else:
                        Gui.Selection.addSelection(doc_name, obj_name)
                else:
                    Gui.Selection.clearSelection()
                    Gui.Selection.addSelection(doc_name, obj_name)
            except Exception:
                pass
            self._consumed_button_downs.add(button)
            event_cb.setHandled()
            return
        node = self._node
        if node is None:
            return
        picked = event_cb.getPickedPoint()
        if picked is None:
            return
        try:
            result = node.pick_value(picked)
        except Exception:
            result = None
        if result is not None:
            key, value, point = result
            self._consumed_button_downs.add(button)
            event_cb.setHandled()
            self._add_probe(point, value)

    def _deselect_probe(self, key):
        doc_name, obj_name = key
        try:
            obj = App.getDocument(doc_name).getObject(obj_name)
            if obj is not None:
                Gui.Selection.removeSelection(obj)
        except Exception:
            pass

    def _add_probe(self, point, value):
        analysis = self._ensure_analysis_object()
        if analysis is None:
            return None
        from .document import (
            ContourProbeFeature,
            ContourProbeViewProvider,
            default_display_text,
            default_probe_label,
        )

        doc = analysis.Document
        self._probe_seq += 1
        index = self._probe_seq
        unit = self.measure.unit
        formatted = self.measure.format_value(value)

        obj = doc.addObject("App::FeaturePython", f"Probe{index:03d}")
        ContourProbeFeature(obj)
        obj.Parent = analysis
        obj.Position = App.Vector(point[0], point[1], point[2])
        obj.Value = float(value)
        obj.Unit = unit
        obj.FormattedValue = formatted
        obj.DisplayText = default_display_text(value, unit)
        obj.Label = default_probe_label(index, value, unit)
        if App.GuiUp and obj.ViewObject is not None:
            ContourProbeViewProvider(obj.ViewObject)
            obj.ViewObject.Visibility = True
            key = (doc.Name, obj.Name)
            self._pending_new_probe = key
            QtCore.QTimer.singleShot(500, lambda k=key: self._clear_pending_if(k))
        doc.recompute()
        return obj

    def _clear_pending_if(self, key):
        if self._pending_new_probe == key:
            self._pending_new_probe = None

    def _set_probes_visible(self, visible):
        if self._analysis_obj is None:
            return
        from .document import _probe_children

        for child in _probe_children(self._analysis_obj):
            try:
                child.ViewObject.Visibility = visible
            except Exception:
                pass

    def getStandardButtons(self):
        return (
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Close
        )

    def _teardown(self):
        self._reset_pick_ui()
        self._deselect_all_probes()
        if self._analysis_obj is not None:
            try:
                if getattr(self._analysis_obj.Proxy, "_live_panel", None) is self:
                    self._analysis_obj.Proxy._live_panel = None
            except Exception:
                pass
        self._set_probes_visible(False)
        self._remove_hover()
        if self._hover_label is not None:
            self._hover_label.deleteLater()
            self._hover_label = None
        self._destroy_legend()
        clear_all()
        if self.indicator is not None:
            self.indicator.remove()
        try:
            Gui.Selection.removeObserver(self)
        except Exception:
            pass
        Gui.Control.closeDialog()

    def _deselect_all_probes(self):
        from .document import ProbeState, _probe_children, probe_highlight_proxy

        try:
            Gui.Selection.clearSelection()
        except Exception:
            pass
        if self._analysis_obj is not None:
            for child in _probe_children(self._analysis_obj):
                try:
                    proxy = probe_highlight_proxy(child.Document.Name, child.Name)
                    if proxy is not None:
                        proxy.set_highlight_state(ProbeState.NONE)
                except Exception:
                    pass
        self._selected_probes.clear()
        self._hovered_probe = None

    def reject(self):
        delete_unsaved = self._analysis_obj is not None and not self._analysis_persisted
        obj = self._analysis_obj
        self._teardown()
        if delete_unsaved and obj is not None:
            self._delete_unsaved_analysis(obj)

    def _delete_unsaved_analysis(self, analysis_obj):
        from .document import _probe_children, legend_child, direction_child

        try:
            doc = analysis_obj.Document
        except Exception:
            return
        if doc is None:
            return
        try:
            children = list(_probe_children(analysis_obj))
            legend = legend_child(analysis_obj)
            if legend is not None:
                children.append(legend)
            direction = direction_child(analysis_obj)
            if direction is not None:
                children.append(direction)
            for child in children:
                try:
                    doc.removeObject(child.Name)
                except Exception:
                    pass
            doc.removeObject(analysis_obj.Name)
            doc.recompute()
        except Exception as exc:
            App.Console.PrintWarning(f"DFM contour: could not remove unsaved analysis. {exc}\n")

    def accept(self):
        self._on_save()
        if not self._saved:
            delete_unsaved = self._analysis_obj is not None and not self._analysis_persisted
            obj = self._analysis_obj
            self._teardown()
            if delete_unsaved and obj is not None:
                self._delete_unsaved_analysis(obj)
