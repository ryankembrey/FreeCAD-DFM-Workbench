# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The Mold Flow task panel.

Laid out and used like the contour tools' panel (gui/contour/panel.py)::

    Model        [Body                    ] [pick]
    Mesh         Resolution [Fine v] [0.53 mm]
    Process      Material, melt and mold temperature, fill time, machine limit
    Gates        Gate 1  12.0, 30.5, 2.0   [pick] [x]     [+ Add gate] [Suggest]
    [> Run simulation]  [Clear]
    -- once it has run --
    | v Fills  needs 45 MPa, 45% of the machine |    (the results' verdict banner)
    Display      Result [Fill time v], color map, smooth shading, what to show
    Animation    [>] ----o------------- 0.42 s of 0.71 s
    Summary      fill time, pressure, volume, weld lines, air traps ...

Running puts a Mold Flow Analysis item in the tree (Save keeps it, Close
drops it unless it was saved); double-clicking it opens the panel again with
its result. The result is drawn over the model with a legend in the 3D view
(gui/moldflow/scene.py); hovering shows the value under the cursor.
"""

import math
import time
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from pivy import coin

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ...app.prefs import dfm_params
from ...app.contour.colormap import COLORMAPS
from ...app.contour.measures import Category
from ...app.moldflow.analysis import CONFIDENCE, RESULTS
from ...app.moldflow.material import material_for, material_names
from ..utils import resources  # noqa: F401
from ..utils.body_select import auto_select_body
from ..utils.colormap_combo import populate_colormap_combo
from ..utils.direction_row import PICK_SVG, icon_button, muted_color, style_primary_button
from ..utils.picking import CrossCursorOverride
from ..utils.qt_filters import EscapeFilter
from ..utils.view import get_active_view, get_view_widget
from ..contour.legend import ContourLegend
from ..contour.category_legend import CategoryLegend
from ..contour.resolution import ResolutionField
from ..contour import panel as _contour_panel
from .scene import GateMarker, MoldFlowScene

_ICON = ":/icons/dfm_moldflow.svg"
_EXPERIMENTAL_RGB = "#D4900A"  # the warning amber, as in the verdict banner
_RESULT_IDS = list(RESULTS)
_CLOSE_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>'
)
_ADD_SVG = _contour_panel._ADD_SVG
_PAUSE_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    b'<path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" fill="COLOR"/></svg>'
)
_PLAY_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    b'<path d="M7 4l13 8-13 8z" fill="COLOR"/></svg>'
)
_SUGGEST_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    b'<circle cx="12" cy="12" r="7"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/></svg>'
)
_STATUS = {
    "good": ("Fills", "#639922", ":/icons/dfm_success.svg"),
    "hard": ("Hard to fill", "#D4900A", ":/icons/dfm_warning.svg"),
    "short": ("Short shot", "#E24B4A", ":/icons/dfm_error.svg"),
}
_FRAME_MS = 33


def _pref_float(key, default):
    return dfm_params().GetFloat(key, default)


def _pref_bool(key, default):
    return dfm_params().GetBool(key, default)


def _pref_str(key, default):
    return dfm_params().GetString(key, default) or default


def format_value(result_id, value):
    """A value as shown in the 3D view and the status bar."""
    if value is None or not math.isfinite(value):
        return "Not filled" if result_id == "fill_time" else "No value"
    if result_id == "fill_time":
        return f"{value:.3f} s"
    if result_id == "pressure":
        return f"{value:.1f} MPa"
    if result_id == "confidence":
        return CONFIDENCE.get(int(round(value)), ("?",))[0]
    if result_id == "frozen":
        return f"{value:.0f}% frozen"
    if result_id == "thickness":
        return f"{value:.2f} mm"
    if result_id == "shear":
        return f"{value:,.0f} 1/s"
    return f"{value:g}"


class _Gate:
    """One gate in the panel: where, the surface normal there, its row and arrow."""

    def __init__(self, number, point=None, normal=None, suggested=False):
        self.number = number
        self.point = point  # App.Vector or None (not picked yet)
        self.normal = normal  # App.Vector, outward
        self.suggested = suggested
        self.row = None
        self.coords = None
        self.pick_btn = None
        self.marker = GateMarker(self.label)

    @property
    def label(self):
        return f"Gate {self.number}"

    def show_marker(self):
        if self.point is None or self.normal is None:
            self.marker.set_visible(False)
            return
        self.marker.set_visible(True)
        self.marker.show(self.point, App.Vector(-self.normal.x, -self.normal.y, -self.normal.z))


class MoldFlowTaskPanel:
    def __init__(self, analysis_obj=None):
        self._analysis_obj = analysis_obj
        self._persisted = analysis_obj is not None
        self._saved = False
        self.target_object = None
        self.target_shape = None
        self.result = None
        self._scene: Optional[MoldFlowScene] = None
        self._legend = None
        self._legend_obj = None
        self._suppress_legend_sync = False
        self._ranges = {}
        self._muted = frozenset()
        self._gates: list = []
        self._mesh_cache = None
        self.picking_mode = None
        self._cursor = CrossCursorOverride()
        self._running = False
        self._abort = False
        self._time = None
        self._playing = False
        self._timer = QtCore.QTimer()
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._on_frame)
        self._last_frame = 0.0
        self._hover_cb = None
        self._hover_view = None
        self._hover_label = None
        self._gate_pick_cb = None  # Coin mouse callback while placing a gate
        self._gate_pick_view = None
        self._last_hover_t = 0.0
        self._suppress_prop_sync = False

        self._build_form()
        Gui.Selection.addObserver(self)
        self._escape_filter = EscapeFilter(self._on_escape)
        self.form.installEventFilter(self._escape_filter)
        if analysis_obj is not None:
            try:
                analysis_obj.Proxy._live_panel = self
            except Exception:
                pass
            self._load_from_object(analysis_obj)
        else:
            auto_select_body(self._apply_object)
        self._refresh_state()

    # -- Building ---------------------------------------------------------------------

    @staticmethod
    def _grid(box):
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
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(6)
        combo.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                            QtWidgets.QSizePolicy.Policy.Fixed)

    @staticmethod
    def _spin(lo, hi, step, decimals, suffix, tip):
        sb = QtWidgets.QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setSingleStep(step)
        sb.setDecimals(decimals)
        sb.setSuffix(suffix)
        sb.setToolTip(tip)
        sb.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                         QtWidgets.QSizePolicy.Policy.Fixed)
        return sb

    def _build_form(self):
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Mold Flow Analysis")
        self.form.setWindowIcon(QtGui.QIcon(_ICON))
        root = QtWidgets.QVBoxLayout(self.form)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)
        self._muted_css = f"color: {muted_color(self.form)};"

        root.addWidget(self._build_experimental_banner())

        # Model: the name, then an icon button to pick it in the 3D view.
        obj_box = QtWidgets.QGroupBox("Model")
        orow = QtWidgets.QHBoxLayout(obj_box)
        orow.setContentsMargins(8, 8, 8, 8)
        orow.setSpacing(4)
        self.le_object = QtWidgets.QLineEdit()
        self.le_object.setReadOnly(True)
        self.le_object.setPlaceholderText("No model selected")
        self.pb_object = icon_button(
            PICK_SVG, "Pick the model in the 3D view, or select it before opening the tool",
            checkable=True)
        self.pb_object.clicked.connect(self._on_pick_object)
        orow.addWidget(self.le_object, 1)
        orow.addWidget(self.pb_object)
        root.addWidget(obj_box)

        # Mesh
        mesh_box = QtWidgets.QGroupBox("Mesh")
        mg = self._grid(mesh_box)
        mg.addWidget(QtWidgets.QLabel("Resolution"), 0, 0)
        self.resolution = ResolutionField()
        self.resolution.set_state(_pref_str("MoldFlowDefaultResolution", "Fine"))
        self.resolution.setToolTip(
            "Elements should be about a wall thickness or smaller. Finer meshes give "
            "sharper fronts and seams, and take longer to fill.")
        self.resolution.changed.connect(self._refresh_state)
        mg.addWidget(self.resolution, 0, 1)
        root.addWidget(mesh_box)

        # Process
        proc_box = QtWidgets.QGroupBox("Process")
        pg = self._grid(proc_box)
        pg.addWidget(QtWidgets.QLabel("Material"), 0, 0)
        self.cb_material = QtWidgets.QComboBox()
        self.cb_material.addItems(material_names())
        self.cb_material.setToolTip(
            "The plastic. The generic grades are approximate; enter your supplier's "
            "Cross-WLF data as the Custom material in Preferences > DFM > Mold Flow.")
        self._compact_combo(self.cb_material)
        self.cb_material.currentIndexChanged.connect(self._on_material_changed)
        pg.addWidget(self.cb_material, 0, 1)
        pg.addWidget(QtWidgets.QLabel("Melt temperature"), 1, 0)
        self.sb_melt = self._spin(50, 450, 5, 0, " °C", "Temperature of the melt entering the mold.")
        pg.addWidget(self.sb_melt, 1, 1)
        pg.addWidget(QtWidgets.QLabel("Mold temperature"), 2, 0)
        self.sb_mold = self._spin(0, 250, 5, 0, " °C", "Temperature of the mold walls.")
        pg.addWidget(self.sb_mold, 2, 1)
        pg.addWidget(QtWidgets.QLabel("Fill time"), 3, 0)
        self.sb_fill = self._spin(0.0, 60.0, 0.1, 2, " s",
                                  "How long the machine takes to fill the part. Automatic picks a "
                                  "typical time for the part's volume.")
        self.sb_fill.setSpecialValueText("Automatic")
        pg.addWidget(self.sb_fill, 3, 1)
        pg.addWidget(QtWidgets.QLabel("Machine limit"), 4, 0)
        self.sb_limit = self._spin(10, 400, 5, 0, " MPa",
                                   "The most injection pressure the machine can give. Confidence of "
                                   "fill compares what each place needs against it.")
        self.sb_limit.setValue(_pref_float("MoldFlowMaxPressure", 100.0))
        pg.addWidget(self.sb_limit, 4, 1)
        for w in (self.sb_melt, self.sb_mold, self.sb_fill, self.sb_limit):
            w.valueChanged.connect(self._mark_dirty)
        root.addWidget(proc_box)
        default_material = _pref_str("MoldFlowDefaultMaterial", "ABS (generic)")
        if default_material in material_names():
            self.cb_material.setCurrentText(default_material)
        self._on_material_changed()

        # Gates
        gate_box = QtWidgets.QGroupBox("Gates")
        gl = QtWidgets.QVBoxLayout(gate_box)
        gl.setContentsMargins(8, 8, 8, 8)
        gl.setSpacing(6)
        self._gate_rows = QtWidgets.QVBoxLayout()
        self._gate_rows.setContentsMargins(0, 0, 0, 0)
        self._gate_rows.setSpacing(4)
        gl.addLayout(self._gate_rows)
        self.lbl_no_gate = QtWidgets.QLabel(
            "No gate yet: the best place for one is found when you run.")
        self.lbl_no_gate.setWordWrap(True)
        self.lbl_no_gate.setStyleSheet(self._muted_css)
        gl.addWidget(self.lbl_no_gate)
        brow = QtWidgets.QHBoxLayout()
        brow.setContentsMargins(0, 0, 0, 0)
        brow.setSpacing(6)
        self.pb_add_gate = QtWidgets.QPushButton("Add gate")
        self._set_icon(self.pb_add_gate, _ADD_SVG)
        self.pb_add_gate.setToolTip("Add a gate, then click where it goes on the model")
        self.pb_add_gate.clicked.connect(self._on_add_gate)
        self.pb_suggest = QtWidgets.QPushButton("Suggest")
        self._set_icon(self.pb_suggest, _SUGGEST_SVG)
        self.pb_suggest.setToolTip(
            "Put one gate where the flow lengths are best balanced (replaces the gates)")
        self.pb_suggest.clicked.connect(self._on_suggest_gate)
        brow.addWidget(self.pb_add_gate, 1)
        brow.addWidget(self.pb_suggest, 1)
        gl.addLayout(brow)
        root.addWidget(gate_box)
        self.gate_box = gate_box

        # Run / Clear
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        self.pb_run = QtWidgets.QPushButton()
        style_primary_button(self.pb_run, "Run simulation")
        self.pb_run.clicked.connect(self._on_run)
        self.pb_clear = QtWidgets.QPushButton("Clear")
        self.pb_clear.setMinimumHeight(30)
        self.pb_clear.setEnabled(False)
        self.pb_clear.clicked.connect(self._on_clear)
        row.addWidget(self.pb_run, 1)
        row.addWidget(self.pb_clear, 1)
        root.addLayout(row)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.progress.hide()
        root.addWidget(self.progress)

        # -- Results (shown once it has run) --
        self.results_widget = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(self.results_widget)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)

        self.banner = QtWidgets.QFrame()
        self.banner.setObjectName("dfmVerdictBanner")
        self.banner.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        brow = QtWidgets.QHBoxLayout(self.banner)
        brow.setContentsMargins(8, 6, 10, 6)
        brow.setSpacing(8)
        self._banner_icon = QtWidgets.QLabel()
        self._banner_icon.setFixedSize(16, 16)
        brow.addWidget(self._banner_icon)
        self._banner_word = QtWidgets.QLabel()
        self._banner_word.setTextFormat(QtCore.Qt.TextFormat.RichText)
        f = self._banner_word.font()
        f.setBold(True)
        self._banner_word.setFont(f)
        brow.addWidget(self._banner_word)
        self._banner_detail = QtWidgets.QLabel()
        self._banner_detail.setWordWrap(True)
        brow.addWidget(self._banner_detail, 1)
        rl.addWidget(self.banner)

        disp_box = QtWidgets.QGroupBox("Display")
        dg = self._grid(disp_box)
        dg.addWidget(QtWidgets.QLabel("Result"), 0, 0)
        self.cb_result = QtWidgets.QComboBox()
        for rid in _RESULT_IDS:
            self.cb_result.addItem(RESULTS[rid][0], rid)
        self.cb_result.setToolTip("What the model is colored by")
        self._compact_combo(self.cb_result)
        self.cb_result.currentIndexChanged.connect(self._on_result_changed)
        dg.addWidget(self.cb_result, 0, 1)
        self.lbl_colormap = QtWidgets.QLabel("Color map")
        dg.addWidget(self.lbl_colormap, 1, 0)
        self.cb_colormap = QtWidgets.QComboBox()
        populate_colormap_combo(self.cb_colormap, COLORMAPS.keys())
        self.cb_colormap.setCurrentText(_pref_str("MoldFlowDefaultColormap", "Viridis"))
        self._compact_combo(self.cb_colormap)
        self.cb_colormap.currentIndexChanged.connect(self._on_style_changed)
        dg.addWidget(self.cb_colormap, 1, 1)
        self.cb_smooth = QtWidgets.QCheckBox("Smooth shading")
        self.cb_smooth.setChecked(True)
        self.cb_smooth.setToolTip(
            "Blend colors across triangles so the mesh facets disappear. "
            "Off shows one flat color per triangle.")
        self.cb_smooth.toggled.connect(self._on_smooth_changed)
        dg.addWidget(self.cb_smooth, 2, 0, 1, 2)
        self.cb_welds = QtWidgets.QCheckBox("Weld and meld lines")
        self.cb_welds.setChecked(_pref_bool("MoldFlowShowWelds", True))
        self.cb_welds.setToolTip(
            "Where melt fronts met (solid) and the seams they leave downstream (dashed). "
            "Weld lines can be weak and show on the surface.")
        self.cb_welds.toggled.connect(self._on_overlays_changed)
        dg.addWidget(self.cb_welds, 3, 0)
        self.cb_traps = QtWidgets.QCheckBox("Air traps")
        self.cb_traps.setChecked(_pref_bool("MoldFlowShowAirTraps", True))
        self.cb_traps.setToolTip(
            "Places the melt closes in on from all sides. Without a vent there, trapped "
            "air can burn the plastic or leave it short.")
        self.cb_traps.toggled.connect(self._on_overlays_changed)
        dg.addWidget(self.cb_traps, 3, 1)
        self.cb_last = QtWidgets.QCheckBox("Last to fill")
        self.cb_last.setChecked(False)
        self.cb_last.setToolTip("The place the melt reaches last: where a short shot shows first.")
        self.cb_last.toggled.connect(self._on_overlays_changed)
        dg.addWidget(self.cb_last, 4, 0)
        self.cb_gates = QtWidgets.QCheckBox("Gates")
        self.cb_gates.setChecked(True)
        self.cb_gates.setToolTip("Show the gate arrows.")
        self.cb_gates.toggled.connect(self._refresh_gate_markers)
        dg.addWidget(self.cb_gates, 4, 1)
        rl.addWidget(disp_box)

        self.anim_box = QtWidgets.QGroupBox("Animation")
        al = QtWidgets.QHBoxLayout(self.anim_box)
        al.setContentsMargins(8, 8, 8, 8)
        al.setSpacing(6)
        self.pb_play = icon_button(_PLAY_SVG, "Play the fill")
        self.pb_play.clicked.connect(self._on_play)
        al.addWidget(self.pb_play)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setValue(1000)
        self.slider.setToolTip("Drag to see how far the melt has got at any moment")
        self.slider.valueChanged.connect(self._on_slider)
        al.addWidget(self.slider, 1)
        self.lbl_time = QtWidgets.QLabel()
        self.lbl_time.setStyleSheet(self._muted_css)
        self.lbl_time.setMinimumWidth(QtGui.QFontMetrics(self.lbl_time.font()).horizontalAdvance(
            "0.000 s of 0.000 s") + 4)
        self.lbl_time.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight
                                   | QtCore.Qt.AlignmentFlag.AlignVCenter)
        al.addWidget(self.lbl_time)
        rl.addWidget(self.anim_box)

        sum_box = QtWidgets.QGroupBox("Summary")
        self.summary_grid = QtWidgets.QGridLayout(sum_box)
        self.summary_grid.setContentsMargins(8, 8, 8, 8)
        self.summary_grid.setHorizontalSpacing(10)
        self.summary_grid.setVerticalSpacing(4)
        self.summary_grid.setColumnStretch(1, 1)
        rl.addWidget(sum_box)

        self.results_widget.hide()
        root.addWidget(self.results_widget)
        root.addStretch(1)

    def _build_experimental_banner(self):
        """The fixed warning at the top: styled like the results' verdict banner."""
        color = _EXPERIMENTAL_RGB
        frame = QtWidgets.QFrame()
        frame.setObjectName("dfmExperimentalBanner")
        frame.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        c = QtGui.QColor(color)
        frame.setStyleSheet(
            "QFrame#dfmExperimentalBanner {"
            f" background: rgba({c.red()}, {c.green()}, {c.blue()}, 40);"
            f" border: none; border-left: 4px solid {color}; border-radius: 3px; }}"
            " QFrame#dfmExperimentalBanner QLabel { background: transparent; }")
        row = QtWidgets.QHBoxLayout(frame)
        row.setContentsMargins(8, 6, 10, 6)
        row.setSpacing(8)
        icon = QtWidgets.QLabel()
        icon.setFixedSize(16, 16)
        ratio = self.form.devicePixelRatioF() or 1.0
        icon.setPixmap(QtGui.QIcon(":/icons/dfm_warning.svg").pixmap(QtCore.QSize(16, 16), ratio))
        row.addWidget(icon, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        text = QtWidgets.QLabel(
            f'<b style="color: {color}">Extremely experimental.</b> '
            "Results are rough estimates for comparing gate positions and spotting likely "
            "problems, not for sign-off. Assumes thin, sheet-like walls: thick parts "
            "give patchy, unreliable fills.")
        text.setTextFormat(QtCore.Qt.TextFormat.RichText)
        text.setWordWrap(True)
        row.addWidget(text, 1)
        self.experimental_banner = frame
        return frame

    @staticmethod
    def _set_icon(button, svg):
        from ..rule_cards import apply_svg_icon

        apply_svg_icon(button, svg, 14)

    # -- Model ---------------------------------------------------------------------------

    def _apply_object(self, obj):
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull() or not shape.Solids:
            return False
        if obj is not self.target_object:
            self._mesh_cache = None
        self.target_object = obj
        self.target_shape = shape
        self.le_object.setText(obj.Label)
        self.resolution.set_shape(shape)
        self._refresh_state()
        return True

    def _on_pick_object(self):
        if self.pb_object.isChecked():
            self._enter_pick("object")
        else:
            self._exit_pick()

    # -- Picking ----------------------------------------------------------------------------

    def _enter_pick(self, mode):
        self.picking_mode = mode
        self._cursor.push()
        Gui.Selection.clearSelection()
        for btn in [self.pb_object] + [g.pick_btn for g in self._gates if g.pick_btn]:
            want = (mode == "object" and btn is self.pb_object) or (
                isinstance(mode, tuple) and any(g.pick_btn is btn and g.number == mode[1]
                                                for g in self._gates))
            btn.blockSignals(True)
            btn.setChecked(bool(want))
            btn.blockSignals(False)
        if mode == "object":
            self._remove_gate_pick()
            Gui.getMainWindow().statusBar().showMessage("Click the model to analyze", 8000)
        else:
            self._install_gate_pick()
            Gui.getMainWindow().statusBar().showMessage(
                "Click on the model where the melt goes in", 8000)

    def _exit_pick(self):
        self.picking_mode = None
        self._cursor.pop()
        self._remove_gate_pick()
        for btn in [self.pb_object] + [g.pick_btn for g in self._gates if g.pick_btn]:
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)

    def _on_escape(self):
        if self.picking_mode is not None:
            self._exit_pick()
            return True
        return False

    # Selection observer
    def addSelection(self, doc, obj, sub, pnt=None):
        mode = self.picking_mode
        if mode is None:
            return
        try:
            picked = App.getDocument(doc).getObject(obj)
        except Exception:
            picked = None
        if mode == "object":
            if picked is not None and self._apply_object(picked):
                self._exit_pick()
                QtCore.QTimer.singleShot(0, Gui.Selection.clearSelection)
        # Gates are placed by _on_gate_click, not through the selection: a click on
        # the result overlay selects nothing, and a part inside a Body or Part
        # container is reported as its leaf feature rather than the analyzed object.

    # Gate picking: a ray pick in the 3D view, snapped onto the model's surface

    def _install_gate_pick(self):
        if self._gate_pick_cb is not None:
            return
        view = get_active_view()
        if view is None:
            return
        try:
            self._gate_pick_cb = view.addEventCallbackPivy(
                coin.SoMouseButtonEvent.getClassTypeId(), self._on_gate_click)
            self._gate_pick_view = view
        except Exception as exc:
            self._gate_pick_cb = None
            App.Console.PrintWarning(f"DFM mold flow: can't pick in this view ({exc}).\n")

    def _remove_gate_pick(self):
        if self._gate_pick_cb is not None and self._gate_pick_view is not None:
            try:
                self._gate_pick_view.removeEventCallbackPivy(
                    coin.SoMouseButtonEvent.getClassTypeId(), self._gate_pick_cb)
            except Exception:
                pass
        self._gate_pick_cb = None
        self._gate_pick_view = None

    def _on_gate_click(self, event_cb):
        mode = self.picking_mode
        if not (isinstance(mode, tuple) and mode[0] == "gate"):
            return
        try:
            event = event_cb.getEvent()
            if event.getButton() != coin.SoMouseButtonEvent.BUTTON1:
                return
            # Take both the press and the release so FreeCAD doesn't also select.
            event_cb.setHandled()
            if event.getState() != coin.SoButtonEvent.DOWN:
                return
            hit = self._surface_point_under(event_cb, self._gate_pick_view)
        except Exception as exc:
            App.Console.PrintWarning(f"DFM mold flow: gate pick failed ({exc}).\n")
            return
        if hit is None:
            Gui.getMainWindow().statusBar().showMessage(
                "That's not on the model: click on its surface where the melt goes in", 5000)
            return
        point, normal = hit
        # Leave the Coin callback before it's removed.
        QtCore.QTimer.singleShot(0, lambda: self._place_gate(mode[1], point, normal))

    def _surface_point_under(self, event_cb, view=None):
        """(point, normal) on the model's surface under the cursor, or None.

        The first ray hit lying on the model (within a small tolerance, so a hit
        on the offset result overlay counts) is snapped onto the surface."""
        shape = self.target_shape
        view = view or get_active_view()
        if shape is None or view is None:
            return None
        import Part  # type: ignore

        pos = event_cb.getEvent().getPosition()
        rm = view.getViewer().getSoRenderManager()
        action = coin.SoRayPickAction(rm.getViewportRegion())
        action.setPoint(coin.SbVec2s(int(pos[0]), int(pos[1])))
        action.setRadius(4)
        action.setPickAll(True)
        action.apply(rm.getSceneGraph())
        picked = action.getPickedPointList()
        try:
            count = picked.getLength()
        except Exception:
            count = len(picked)
        diag = shape.BoundBox.DiagonalLength
        tol = max(0.005 * diag, 0.2)
        if self._scene is not None:
            tol += 3.0 * getattr(self._scene, "_offset", 0.0)
        for i in range(min(count, 12)):
            p = picked[i].getPoint()
            probe = App.Vector(float(p[0]), float(p[1]), float(p[2]))
            try:
                dist, pairs, _infos = shape.distToShape(Part.Vertex(probe))
            except Exception:
                continue
            if dist <= tol:
                on = pairs[0][0]
                point = App.Vector(on.x, on.y, on.z)
                return point, self._surface_normal(point)
        return None

    def _place_gate(self, number, point, normal):
        if self.picking_mode != ("gate", number):
            return
        gate = next((g for g in self._gates if g.number == number), None)
        if gate is None:
            return
        gate.point = point
        gate.normal = normal
        gate.suggested = False
        self._exit_pick()
        self._refresh_gate_rows()
        self._mark_dirty()

    def removeSelection(self, *args):
        pass

    def setSelection(self, *args):
        pass

    def clearSelection(self, *args):
        pass

    def _surface_normal(self, point, sub=None):
        """The outward normal of the model's surface at (or nearest) point."""
        shape = self.target_shape
        face = None
        try:
            if sub and sub.startswith("Face"):
                face = shape.getElement(sub)
        except Exception:
            face = None
        try:
            import Part  # type: ignore

            if face is None:
                _d, _pairs, infos = shape.distToShape(Part.Vertex(point))
                kind, index = infos[0][0], infos[0][1]
                if kind == "Face":
                    face = shape.Faces[index]
            u, v = face.Surface.parameter(point)
            n = face.normalAt(u, v)
            return App.Vector(n.x, n.y, n.z).normalize()
        except Exception:
            return App.Vector(0, 0, 1)

    # -- Gates --------------------------------------------------------------------------------

    def _next_gate_number(self):
        return max([g.number for g in self._gates] or [0]) + 1

    def _on_add_gate(self):
        if self.target_object is None:
            return
        gate = _Gate(self._next_gate_number())
        self._gates.append(gate)
        self._refresh_gate_rows()
        self._enter_pick(("gate", gate.number))

    def _remove_gate(self, gate):
        try:
            gate.marker.remove()
        except Exception:
            pass
        self._gates = [g for g in self._gates if g is not gate]
        for i, g in enumerate(self._gates, 1):
            if g.number != i:
                g.number = i
                try:
                    g.marker.remove()
                except Exception:
                    pass
                g.marker = GateMarker(g.label)
        if isinstance(self.picking_mode, tuple):
            self._exit_pick()
        self._refresh_gate_rows()
        self._mark_dirty()

    def _on_pick_gate(self, gate, checked):
        if checked:
            self._enter_pick(("gate", gate.number))
        else:
            self._exit_pick()

    def _clear_gates(self):
        for g in self._gates:
            try:
                g.marker.remove()
            except Exception:
                pass
        self._gates = []

    def _on_suggest_gate(self):
        if self.target_shape is None:
            return
        mesh = self._mesh()
        if mesh is None:
            return
        from ...app.moldflow.gating import suggest_gate
        from ...app.moldflow.mesh import FlowMesh

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            flow = FlowMesh.from_uniform_mesh(mesh).prepare()
            node, _cost = suggest_gate(flow)
        except Exception as exc:
            App.Console.PrintError(f"DFM mold flow: could not suggest a gate. {exc}\n")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._clear_gates()
        p = flow.nodes[node]
        n = flow.node_normal[node]
        self._gates.append(_Gate(1, App.Vector(*p), App.Vector(*n), suggested=True))
        self._refresh_gate_rows()
        self._mark_dirty()

    def _refresh_gate_rows(self):
        while self._gate_rows.count():
            item = self._gate_rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for gate in self._gates:
            row = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            name = QtWidgets.QLabel(f"Gate {gate.number}")
            h.addWidget(name)
            coords = QtWidgets.QLabel()
            coords.setStyleSheet(self._muted_css)
            if gate.point is None:
                coords.setText("Click on the model…")
            else:
                p = gate.point
                coords.setText(f"{p.x:.1f}, {p.y:.1f}, {p.z:.1f}"
                               + ("  (suggested)" if gate.suggested else ""))
            coords.setToolTip("Gate position (mm)")
            h.addWidget(coords, 1)
            pick = icon_button(PICK_SVG, "Pick where this gate goes on the model", checkable=True)
            pick.setChecked(self.picking_mode == ("gate", gate.number))
            pick.toggled.connect(lambda on, g=gate: self._on_pick_gate(g, on))
            h.addWidget(pick)
            remove = icon_button(_CLOSE_SVG, "Remove this gate")
            remove.clicked.connect(lambda _c=False, g=gate: self._remove_gate(g))
            h.addWidget(remove)
            gate.row, gate.coords, gate.pick_btn = row, coords, pick
            self._gate_rows.addWidget(row)
        self.lbl_no_gate.setVisible(not self._gates)
        self._refresh_gate_markers()
        self._refresh_state()

    def _refresh_gate_markers(self, *_args):
        show = self.cb_gates.isChecked() if not self.results_widget.isHidden() else True
        for g in self._gates:
            try:
                if show:
                    g.show_marker()
                else:
                    g.marker.set_visible(False)
            except Exception:
                pass

    # -- Process -------------------------------------------------------------------------------

    def _material(self):
        name = self.cb_material.currentText()
        try:
            mat = material_for(name)
        except KeyError:
            mat = material_for(material_names()[0])
        return mat.with_temperatures(self.sb_melt.value(), self.sb_mold.value())

    def _on_material_changed(self, *_args):
        try:
            mat = material_for(self.cb_material.currentText())
        except KeyError:
            return
        for sb, value in ((self.sb_melt, mat.melt_temp), (self.sb_mold, mat.mold_temp)):
            sb.blockSignals(True)
            sb.setValue(value)
            sb.blockSignals(False)
        self._mark_dirty()

    def _mark_dirty(self, *_args):
        if self.result is not None and not self._running:
            self.pb_run.setText("Run again")

    def _refresh_state(self, *_args):
        ready = (self.target_shape is not None and self.resolution.is_safe()
                 and all(g.point is not None for g in self._gates))
        if self._running:
            self.pb_run.setEnabled(True)
            return
        self.pb_run.setEnabled(ready)
        self.pb_add_gate.setEnabled(self.target_shape is not None)
        self.pb_suggest.setEnabled(self.target_shape is not None and self.resolution.is_safe())
        if self.target_shape is None:
            self.pb_run.setToolTip("Select a model first")
        elif not self.resolution.is_safe():
            self.pb_run.setToolTip("The mesh would be too fine: choose a coarser resolution")
        elif not ready:
            self.pb_run.setToolTip("Pick where each gate goes, or remove it")
        else:
            self.pb_run.setToolTip("Simulate the fill")

    # -- Running ------------------------------------------------------------------------------

    def _mesh(self):
        size = self.resolution.element_size()
        key = (getattr(self.target_object, "Name", None), round(size or 0.0, 9),
               self._shape_key())
        if self._mesh_cache is not None and self._mesh_cache[0] == key:
            return self._mesh_cache[1]
        from ...app.contour.meshing import generate_uniform_mesh

        self.progress.show()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("Meshing the model")
        QtWidgets.QApplication.processEvents()
        try:
            mesh = generate_uniform_mesh(self.target_shape, size)
        except Exception as exc:
            App.Console.PrintError(f"DFM mold flow: {exc}\n")
            self.progress.hide()
            return None
        self._mesh_cache = (key, mesh)
        return mesh

    def _shape_key(self):
        try:
            return self.target_shape.hashCode()
        except Exception:
            return id(self.target_shape)

    def _settings(self):
        from ...app.moldflow.analysis import Settings

        params = dfm_params()
        return Settings(
            material=self._material(),
            gates=[(g.point.x, g.point.y, g.point.z) for g in self._gates if g.point is not None],
            injection_time=self.sb_fill.value() or None,
            max_pressure=self.sb_limit.value() * 1e6,
            vp_switch=params.GetFloat("MoldFlowVPSwitch", 99.0) / 100.0,
            cooling=params.GetBool("MoldFlowCooling", True),
            front_fraction=params.GetFloat("MoldFlowFrontPerStep", 10.0) / 100.0,
            weld_angle=params.GetFloat("MoldFlowWeldAngle", 45.0),
        )

    def _set_inputs_enabled(self, on):
        for w in (self.pb_object, self.resolution, self.cb_material, self.sb_melt, self.sb_mold,
                  self.sb_fill, self.sb_limit, self.gate_box, self.pb_clear, self.results_widget):
            w.setEnabled(on)

    def _on_run(self):
        if self._running:
            self._abort = True
            self.progress.setFormat("Stopping…")
            return
        if self.target_shape is None:
            return
        if self.picking_mode is not None:
            self._exit_pick()
        self._pause()
        mesh = self._mesh()
        if mesh is None:
            self._refresh_state()
            return
        from ...app.moldflow import analysis
        from ...app.moldflow.solver import Aborted

        self._running = True
        self._abort = False
        self._set_inputs_enabled(False)
        self.pb_run.setText("Stop")
        self.progress.show()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        started = time.monotonic()
        last = [0.0]

        def progress(text, share):
            now = time.monotonic()
            if share is not None and now - last[0] < 0.05:
                return
            last[0] = now
            if share is None:
                self.progress.setRange(0, 1)
                self.progress.setValue(0)
                self.progress.setFormat(text)
            else:
                self.progress.setRange(0, 1000)
                self.progress.setValue(int(1000 * share))
                self.progress.setFormat(f"{text}  %p%")
            QtWidgets.QApplication.processEvents()

        result = None
        try:
            result = analysis.run(mesh, self._settings(), progress=progress,
                                  check_abort=lambda: self._abort)
        except Aborted:
            App.Console.PrintMessage("DFM mold flow: stopped.\n")
        except Exception as exc:
            import traceback

            App.Console.PrintError(f"DFM mold flow: {exc}\n")
            App.Console.PrintLog(traceback.format_exc())
        finally:
            self._running = False
            self._set_inputs_enabled(True)
            self.progress.hide()
            style_primary_button(self.pb_run, "Run simulation")
        if result is None:
            self._refresh_state()
            return
        App.Console.PrintMessage(
            f"DFM mold flow: {result.summary['nodes']:,} nodes filled in "
            f"{time.monotonic() - started:.1f} s; injection pressure "
            f"{result.summary['injection_pressure']:.1f} MPa.\n")
        if not self._gates:
            for i, (p, n) in enumerate(result.gates, 1):
                self._gates.append(_Gate(i, App.Vector(*p), App.Vector(*n), suggested=True))
            self._refresh_gate_rows()
        self.result = result
        self._ranges = {}
        self._muted = frozenset()
        self._show_result()
        self._ensure_analysis_object()
        self._refresh_state()

    # -- Showing the result -------------------------------------------------------------------

    def _result_id(self):
        return self.cb_result.currentData() or "fill_time"

    def _show_result(self):
        """(Re)build the overlay for self.result and show the panel's result part."""
        self._remove_hover()
        if self._scene is not None:
            self._scene.remove()
        self._scene = None
        if self.result is None:
            return
        scene = MoldFlowScene()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            scene.build(self.result, smooth=self.cb_smooth.isChecked())
            scene.attach()
        except Exception as exc:
            App.Console.PrintError(f"DFM mold flow: could not show the result. {exc}\n")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._scene = scene
        self.results_widget.show()
        self.pb_clear.setEnabled(True)
        self._update_summary()
        self._recolor(rebuild_legend=True)
        self._on_overlays_changed()
        self._refresh_gate_markers()
        self._install_hover()

    def _field_range(self, rid):
        if rid in self._ranges:
            return self._ranges[rid]
        v = self.result.fields[rid]
        finite = v[_finite(v)]
        if not len(finite):
            return (0.0, 1.0)
        if rid == "fill_time":
            rng = (0.0, float(finite.max()))
        elif rid == "pressure":
            rng = (0.0, float(finite.max()))
        elif rid == "frozen":
            rng = (0.0, 100.0)
        elif rid == "shear":
            import numpy as np

            rng = (0.0, float(np.percentile(finite, 98)))
        else:
            rng = (float(finite.min()), float(finite.max()))
        if rng[1] <= rng[0]:
            rng = (rng[0], rng[0] + 1.0)
        return rng

    def _recolor(self, rebuild_legend=False):
        if self._scene is None or self.result is None:
            return
        rid = self._result_id()
        values = self.result.fields[rid]
        low, high = self._field_range(rid)
        categorical = rid == "confidence"
        cats = {code: rgb for code, (_l, rgb, _t) in CONFIDENCE.items()} if categorical else None
        animated = rid == "fill_time"
        self.anim_box.setVisible(animated)
        self.lbl_colormap.setVisible(not categorical)
        self.cb_colormap.setVisible(not categorical)
        t = self._time if animated else None
        self._scene.recolor(values, low, high, self.cb_colormap.currentText(), categories=cats,
                            muted=self._muted, time_cut=t,
                            fill_time=self.result.fields["fill_time"])
        if rebuild_legend or self._legend is None or (
                isinstance(self._legend, CategoryLegend) != categorical):
            self._build_legend()
        elif not categorical:
            self._legend.set_style(self.cb_colormap.currentText(), 0.0)
            self._legend.set_range(low, high)
        self._update_time_label()

    def _build_legend(self):
        self._destroy_legend()
        view_widget = get_view_widget()
        if view_widget is None or self.result is None:
            return
        rid = self._result_id()
        label, unit = RESULTS[rid]
        if rid == "confidence":
            import numpy as np

            legend = CategoryLegend(view_widget)
            legend.configure(label)
            cats = [Category(code, name, rgb, tip) for code, (name, rgb, tip) in
                    sorted(CONFIDENCE.items(), reverse=True)]
            v = self.result.fields["confidence"]
            shares = {code: float(np.mean(np.rint(v) == code)) for code in CONFIDENCE}
            legend.set_categories(cats, shares)
            legend.set_muted_state(set(self._muted), None)
            legend.mutedChanged.connect(self._on_categories_muted)
        else:
            legend = ContourLegend(view_widget)
            v = self.result.fields[rid]
            finite = v[_finite(v)]
            dmin = float(finite.min()) if len(finite) else 0.0
            dmax = float(finite.max()) if len(finite) else 1.0
            low, high = self._field_range(rid)
            dom_lo, dom_hi = min(dmin, low), max(dmax, high)
            legend.set_no_value_label("Not filled" if rid == "fill_time" and len(finite) < len(v)
                                      else None)
            legend.configure(label, unit, self.cb_colormap.currentText(), 0.0,
                             dom_lo, dom_hi, low, high, dmin, dmax)
            legend.rangeChanged.connect(self._on_legend_range)
            legend.colormapChanged.connect(self._on_legend_colormap)
            legend.fitRequested.connect(self._on_legend_fit)
        try:
            from ..contour.document import auto_legend_text_rgb, legend_text_rgb

            rgb = (legend_text_rgb(self._analysis_obj) if self._analysis_obj is not None
                   else auto_legend_text_rgb())
            legend.set_text_color(rgb)
        except Exception:
            pass
        self._legend = legend
        legend.show()
        legend.raise_()
        self._restore_legend_geometry()
        if self._analysis_obj is not None:
            self._ensure_legend_object()

    def _restore_legend_geometry(self):
        """Where the contour tools' legend last was (they share the place)."""
        legend = self._legend
        if legend is None:
            return
        parent = legend.parentWidget()
        pw = parent.width() if parent is not None else legend.width()
        if isinstance(legend, CategoryLegend):
            state = _contour_panel._CATEGORY_LEGEND_STATE
            if state.get("size") is not None:
                legend.resize(state["size"])
            pos = state.get("pos") or QtCore.QPoint(max(0, (pw - legend.width()) // 2), 12)
            legend.move(pos)
            legend._clamp_into_parent()
            return
        state = _contour_panel._LEGEND_STATE
        if state.get("size") is not None and state.get("pos") is not None:
            try:
                legend.set_orientation(state.get("horizontal", True))
                legend.resize(state["size"])
                legend.move(state["pos"])
                legend._clamp_into_parent()
                return
            except Exception:
                pass
        try:
            legend.set_orientation(True)
            w = min(max(320, pw // 3), max(200, pw - 40))
            legend.resize(w, 96)
            x = (pw - w) // 2
            right_limit = int(pw * 0.62)
            if x + w > right_limit:
                x = max(8, right_limit - w)
            legend.move(x, 12)
        except Exception:
            pass

    def _destroy_legend(self):
        legend = self._legend
        if legend is None:
            return
        try:
            if isinstance(legend, CategoryLegend):
                _contour_panel._CATEGORY_LEGEND_STATE["pos"] = legend.pos()
                _contour_panel._CATEGORY_LEGEND_STATE["size"] = legend.size()
            else:
                _contour_panel._LEGEND_STATE["pos"] = legend.pos()
                _contour_panel._LEGEND_STATE["size"] = legend.size()
                _contour_panel._LEGEND_STATE["horizontal"] = legend.orientation_horizontal()
        except Exception:
            pass
        try:
            legend.cleanup()
            legend.setParent(None)
            legend.deleteLater()
        except Exception:
            pass
        self._legend = None

    def _on_legend_range(self, low, high):
        self._ranges[self._result_id()] = (low, high)
        self._recolor()

    def _on_legend_colormap(self, name):
        if name and name != self.cb_colormap.currentText():
            self.cb_colormap.setCurrentText(name)

    def _on_legend_fit(self):
        self._ranges.pop(self._result_id(), None)
        low, high = self._field_range(self._result_id())
        if isinstance(self._legend, ContourLegend):
            self._legend.set_range(low, high)
        self._recolor()

    def _on_categories_muted(self, _muted):
        if isinstance(self._legend, CategoryLegend):
            hidden, solo = self._legend.muted_state()
            if solo is not None:
                self._muted = frozenset(c for c in CONFIDENCE if c != solo)
            else:
                self._muted = frozenset(hidden)
        self._recolor()

    def _on_result_changed(self, *_args):
        self._sync_object_display()
        if self.result is not None:
            self._recolor(rebuild_legend=True)

    def _on_style_changed(self, *_args):
        self._sync_object_display()
        self._recolor()

    def _on_smooth_changed(self, *_args):
        self._sync_object_display()
        if self.result is not None:
            self._show_result()

    def _on_overlays_changed(self, *_args):
        self._sync_object_display()
        if self._scene is not None:
            self._scene.set_overlay_visible("welds", self.cb_welds.isChecked())
            self._scene.set_overlay_visible("traps", self.cb_traps.isChecked())
            self._scene.set_overlay_visible("last", self.cb_last.isChecked())

    def _update_summary(self):
        res = self.result
        s = res.summary
        share = s["injection_pressure"] / max(s["max_pressure"], 1e-9)
        missed = 1.0 - s["filled"]
        if missed > 0.01 or share >= 1.0:
            key = "short"
            if missed > 0.01:
                detail = f"{missed * 100:.1f}% of the part is never reached"
            else:
                detail = (f"needs {s['injection_pressure']:.0f} MPa, over the machine's "
                          f"{s['max_pressure']:.0f} MPa")
        elif missed > 0.001:
            key = "hard"
            detail = (f"small features ({missed * 100:.2f}% of the part) may not fill; "
                      f"needs {s['injection_pressure']:.1f} MPa, {share:.0%} of the machine")
        elif share > 0.8:
            key = "hard"
            detail = (f"needs {s['injection_pressure']:.0f} MPa, {share:.0%} of the "
                      f"machine's {s['max_pressure']:.0f} MPa")
        else:
            key = "good"
            detail = (f"needs {s['injection_pressure']:.1f} MPa, {share:.0%} of the "
                      f"machine's {s['max_pressure']:.0f} MPa")
        word, color, icon = _STATUS[key]
        self._banner_word.setText(f'<span style="color: {color}">{word}</span>')
        self._banner_detail.setText(detail)
        ratio = self.form.devicePixelRatioF() or 1.0
        self._banner_icon.setPixmap(QtGui.QIcon(icon).pixmap(QtCore.QSize(16, 16), ratio))
        c = QtGui.QColor(color)
        self.banner.setStyleSheet(
            "QFrame#dfmVerdictBanner {"
            f" background: rgba({c.red()}, {c.green()}, {c.blue()}, 40);"
            f" border: none; border-left: 4px solid {color}; border-radius: 3px; }}"
            " QFrame#dfmVerdictBanner QLabel { background: transparent; }")

        grid = self.summary_grid
        while grid.count():
            item = grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        welds = s.get("weld_lines", 0)
        melds = s.get("meld_lines", 0)
        rows = [
            ("Material", f"{s['material']}, {s['melt_temp']:.0f} / {s['mold_temp']:.0f} °C",
             "Melt / mold temperature"),
            ("Fill time", f"{s['fill_time']:.2f} s" + ("" if self.sb_fill.value() else
                                                       " (automatic)"), None),
            ("Injection pressure", f"{s['injection_pressure']:.1f} MPa",
             "At the V/P switchover, when the cavity is nearly full"),
            ("Part volume", f"{s['volume_cm3']:.2f} cm³" + (
                f", {(1 - s['filled']) * 100:.2f}% not filled" if s["filled"] < 0.9995 else ""),
             "Places the melt doesn't reach show gray in Fill time and red in Confidence of fill"),
            ("Weld lines", "None" if not welds else
             f"{welds}" + (f", with {melds} meld line{'s' if melds != 1 else ''}" if melds else ""),
             None),
            ("Air traps", "None" if not s.get("air_traps") else f"{s['air_traps']}", None),
            ("Mesh", f"{s['nodes']:,} nodes, {s['element_size']:.2f} mm, "
                     f"solved in {s['seconds']:.0f} s", None),
        ]
        for i, (name, value, tip) in enumerate(rows):
            lab = QtWidgets.QLabel(name)
            lab.setStyleSheet(self._muted_css)
            val = QtWidgets.QLabel(value)
            val.setWordWrap(True)
            if tip:
                lab.setToolTip(tip)
                val.setToolTip(tip)
            grid.addWidget(lab, i, 0, QtCore.Qt.AlignmentFlag.AlignTop)
            grid.addWidget(val, i, 1)

    # -- Animation ----------------------------------------------------------------------------

    def _fill_end(self):
        return self.result.fill_end if self.result is not None else 0.0

    def _on_play(self):
        if self._playing:
            self._pause()
            return
        if self.result is None:
            return
        if self._time is None or self._time >= self._fill_end() - 1e-12:
            self._time = 0.0
        self._playing = True
        self._last_frame = time.monotonic()
        self._set_play_icon()
        self._timer.start()

    def _pause(self):
        self._playing = False
        self._timer.stop()
        self._set_play_icon()

    def _set_play_icon(self):
        from ..rule_cards import apply_svg_icon

        apply_svg_icon(self.pb_play, _PAUSE_SVG if self._playing else _PLAY_SVG, 16)
        self.pb_play.setToolTip("Pause" if self._playing else "Play the fill")

    def _on_frame(self):
        end = self._fill_end()
        if self.result is None or end <= 0:
            self._pause()
            return
        now = time.monotonic()
        dt = now - self._last_frame
        self._last_frame = now
        length = max(0.5, _pref_float("MoldFlowAnimationSeconds", 6.0))
        t = (self._time or 0.0) + dt * end / length
        if t >= end:
            if _pref_bool("MoldFlowAnimationLoop", False):
                t = 0.0
            else:
                t = end
                self._pause()
        self._set_time(t)

    def _on_slider(self, value):
        if self.result is None:
            return
        if self._playing and self.slider.isSliderDown():
            self._pause()
        if self._suppress_slider:
            return
        self._set_time(self._fill_end() * value / 1000.0, from_slider=True)

    _suppress_slider = False

    def _set_time(self, t, from_slider=False):
        end = self._fill_end()
        self._time = None if t is None or t >= end - 1e-12 else max(0.0, t)
        if self._scene is not None and self._result_id() == "fill_time":
            self._scene.set_time(self._time)
            if isinstance(self._legend, ContourLegend):
                self._legend.set_marker(self._time)
        if not from_slider:
            self._suppress_slider = True
            self.slider.setValue(1000 if self._time is None or end <= 0
                                 else int(round(1000 * self._time / end)))
            self._suppress_slider = False
        self._update_time_label()

    def _update_time_label(self):
        end = self._fill_end()
        t = end if self._time is None else self._time
        self.lbl_time.setText(f"{t:.3f} s of {end:.3f} s")

    # -- Hover: the value under the cursor --------------------------------------------------------

    def _install_hover(self):
        if self._hover_cb is not None:
            return
        view = get_active_view()
        if view is None:
            return
        try:
            self._hover_cb = view.addEventCallbackPivy(
                coin.SoLocation2Event.getClassTypeId(), self._on_hover)
            self._hover_view = view
        except Exception as exc:
            App.Console.PrintWarning(f"DFM mold flow: hover unavailable. {exc}\n")

    def _remove_hover(self):
        if self._hover_cb is not None and self._hover_view is not None:
            try:
                self._hover_view.removeEventCallbackPivy(
                    coin.SoLocation2Event.getClassTypeId(), self._hover_cb)
            except Exception:
                pass
        self._hover_cb = None
        self._hover_view = None
        if self._hover_label is not None:
            self._hover_label.hide()
        if self._legend is not None and isinstance(self._legend, ContourLegend):
            self._legend.set_marker(self._time if self._result_id() == "fill_time" else None)

    def _on_hover(self, event_cb):
        now = time.monotonic()
        if now - self._last_hover_t < 0.03 or self._scene is None:
            return
        self._last_hover_t = now
        hit = self._pick(event_cb)
        rid = self._result_id()
        if hit is None:
            if self._hover_label is not None:
                self._hover_label.hide()
            if isinstance(self._legend, ContourLegend):
                self._legend.set_marker(self._time if rid == "fill_time" else None)
            return
        _key, value, _point = hit
        text = format_value(rid, value)
        label = self._hover_label_widget()
        if label is not None:
            label.setText(text)
            label.adjustSize()
            parent = label.parentWidget()
            pos = parent.mapFromGlobal(QtGui.QCursor.pos()) + QtCore.QPoint(14, 14)
            label.move(pos)
            label.show()
            label.raise_()
        Gui.getMainWindow().statusBar().showMessage(f"{RESULTS[rid][0]}: {text}")
        if isinstance(self._legend, ContourLegend) and math.isfinite(value):
            self._legend.set_marker(value)

    def _pick(self, event_cb):
        view = self._hover_view or get_active_view()
        if view is None:
            return None
        try:
            pos = event_cb.getEvent().getPosition()
            rm = view.getViewer().getSoRenderManager()
            action = coin.SoRayPickAction(rm.getViewportRegion())
            action.setPoint(coin.SbVec2s(int(pos[0]), int(pos[1])))
            action.setPickAll(True)
            action.apply(rm.getSceneGraph())
            picked = action.getPickedPointList()
            try:
                count = picked.getLength()
            except Exception:
                count = len(picked)
            first = None
            tol = max(0.01 * self.target_shape.BoundBox.DiagonalLength, 0.05) \
                if self.target_shape is not None else 1.0
            for i in range(count):
                pp = picked[i]
                p = pp.getPoint()
                if first is None:
                    first = (p[0], p[1], p[2])
                hit = self._scene.pick_value(pp)
                if hit is None:
                    continue
                return hit if math.dist(first, hit[2]) <= tol else None
        except Exception:
            return None
        return None

    def _hover_label_widget(self):
        if self._hover_label is not None:
            return self._hover_label
        view_widget = get_view_widget()
        if view_widget is None:
            return None
        label = QtWidgets.QLabel(view_widget)
        label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.setStyleSheet(
            "QLabel { background: rgba(20,20,22,180); color: #fff;"
            " border: 1px solid rgba(255,255,255,70); border-radius: 4px;"
            " padding: 2px 6px; font-weight: bold; }")
        self._hover_label = label
        return label

    # -- The saved analysis -----------------------------------------------------------------------

    def _gather_params(self):
        resolution, element_size = self.resolution.state()
        return {
            "source": self.target_object,
            "resolution": resolution,
            "element_size": element_size,
            "material": self.cb_material.currentText(),
            "melt_temp": self.sb_melt.value(),
            "mold_temp": self.sb_mold.value(),
            "fill_time": self.sb_fill.value(),
            "max_pressure": self.sb_limit.value(),
            "gates": [(g.point.x, g.point.y, g.point.z) for g in self._gates
                      if g.point is not None],
            "result": RESULTS[self._result_id()][0],
            "colormap": self.cb_colormap.currentText(),
            "smooth": self.cb_smooth.isChecked(),
            "show_welds": self.cb_welds.isChecked(),
            "show_traps": self.cb_traps.isChecked(),
        }

    def _ensure_analysis_object(self):
        if self.result is None:
            return None
        from ...app.moldflow.analysis import to_field
        from .document import create_or_update_analysis

        try:
            self._analysis_obj = create_or_update_analysis(
                self._analysis_obj, self._gather_params(), to_field(self.result))
            self._analysis_obj.Proxy._live_panel = self
        except Exception as exc:
            App.Console.PrintError(f"DFM mold flow: could not save the analysis. {exc}\n")
            return None
        self._ensure_legend_object()
        return self._analysis_obj

    def _sync_object_display(self):
        """Display settings changed here: keep the tree item's properties in step."""
        obj = self._analysis_obj
        if obj is None or self._suppress_prop_sync:
            return
        self._suppress_prop_sync = True
        try:
            obj.Proxy._storing = True
            obj.Result = RESULTS[self._result_id()][0]
            obj.ColorMap = self.cb_colormap.currentText()
            obj.Smooth = self.cb_smooth.isChecked()
            obj.ShowWeldLines = self.cb_welds.isChecked()
            obj.ShowAirTraps = self.cb_traps.isChecked()
        except Exception:
            pass
        finally:
            try:
                obj.Proxy._storing = False
            except Exception:
                pass
            self._suppress_prop_sync = False

    def apply_property_change(self, prop):
        """A display property was edited in the tree: follow it here."""
        obj = self._analysis_obj
        if obj is None or self._suppress_prop_sync:
            return
        self._suppress_prop_sync = True
        try:
            if prop == "Result":
                for i, rid in enumerate(_RESULT_IDS):
                    if RESULTS[rid][0] == obj.Result:
                        self.cb_result.setCurrentIndex(i)
            elif prop == "ColorMap":
                self.cb_colormap.setCurrentText(obj.ColorMap)
            elif prop == "Smooth":
                self.cb_smooth.setChecked(bool(obj.Smooth))
            elif prop == "ShowWeldLines":
                self.cb_welds.setChecked(bool(obj.ShowWeldLines))
            elif prop == "ShowAirTraps":
                self.cb_traps.setChecked(bool(obj.ShowAirTraps))
        finally:
            self._suppress_prop_sync = False

    def _ensure_legend_object(self):
        if self._analysis_obj is None or self._legend is None:
            return
        from .document import ensure_legend

        try:
            horizontal = (self._legend.orientation_horizontal()
                          if isinstance(self._legend, ContourLegend) else True)
            self._legend_obj = ensure_legend(self._analysis_obj, horizontal)
            if App.GuiUp and self._legend_obj is not None and self._legend_obj.ViewObject:
                self._legend.setVisible(bool(self._legend_obj.ViewObject.Visibility))
        except Exception as exc:
            App.Console.PrintWarning(f"DFM mold flow: could not add the legend item. {exc}\n")

    # The legend tree item calls these (see contour/document.ContourLegendFeature).
    def apply_legend_orientation(self, vertical):
        if isinstance(self._legend, ContourLegend):
            try:
                self._legend.set_orientation(not vertical)
            except Exception:
                pass

    def apply_legend_visible(self, visible):
        if self._legend is not None:
            self._legend.setVisible(bool(visible))

    def apply_legend_text_color(self):
        if self._legend is None or self._analysis_obj is None:
            return
        from ..contour.document import legend_text_rgb

        try:
            self._legend.set_text_color(legend_text_rgb(self._analysis_obj))
        except Exception:
            pass

    def on_legend_deleted(self):
        self._legend_obj = None
        self._destroy_legend()

    def _load_from_object(self, obj):
        try:
            src = getattr(obj, "Source", None)
            if src is not None:
                self._apply_object(src)
            self.resolution.set_state(getattr(obj, "Resolution", ""),
                                      getattr(obj, "ElementSize", 0.0))
            if obj.Material in material_names():
                self.cb_material.blockSignals(True)
                self.cb_material.setCurrentText(obj.Material)
                self.cb_material.blockSignals(False)
            for sb, value in ((self.sb_melt, obj.MeltTemperature),
                              (self.sb_mold, obj.MoldTemperature),
                              (self.sb_fill, obj.FillTime), (self.sb_limit, obj.MaxPressure)):
                sb.blockSignals(True)
                sb.setValue(float(value))
                sb.blockSignals(False)
            self.cb_colormap.setCurrentText(obj.ColorMap or self.cb_colormap.currentText())
            self.cb_smooth.blockSignals(True)
            self.cb_smooth.setChecked(bool(obj.Smooth))
            self.cb_smooth.blockSignals(False)
            self.cb_welds.setChecked(bool(obj.ShowWeldLines))
            self.cb_traps.setChecked(bool(obj.ShowAirTraps))
            self.cb_result.blockSignals(True)
            for i, rid in enumerate(_RESULT_IDS):
                if RESULTS[rid][0] == obj.Result:
                    self.cb_result.setCurrentIndex(i)
            self.cb_result.blockSignals(False)
            field = getattr(obj, "FieldData", None)
            gates_saved = list(getattr(obj, "Gates", []) or [])
            if field:
                from ...app.moldflow.analysis import from_field

                self.result = from_field(field)
                normals = {tuple(round(c, 3) for c in p): n for p, n in self.result.gates}
            else:
                normals = {}
            for i, p in enumerate(gates_saved, 1):
                key = (round(p.x, 3), round(p.y, 3), round(p.z, 3))
                n = normals.get(key)
                normal = App.Vector(*n) if n else self._surface_normal(App.Vector(p))
                suggested = bool(self.result and self.result.summary.get("gate_suggested"))
                self._gates.append(_Gate(i, App.Vector(p), normal, suggested=suggested))
            self._refresh_gate_rows()
            if self.result is not None:
                self._show_result()
        except Exception as exc:
            App.Console.PrintError(f"DFM mold flow: could not load the analysis. {exc}\n")

    # -- Task panel ----------------------------------------------------------------------------

    def _on_clear(self):
        self._pause()
        self._remove_hover()
        if self._scene is not None:
            self._scene.remove()
        self._scene = None
        self._destroy_legend()
        self.result = None
        self._time = None
        self.results_widget.hide()
        self.pb_clear.setEnabled(False)
        style_primary_button(self.pb_run, "Run simulation")
        self._refresh_gate_markers()
        self._refresh_state()

    def getStandardButtons(self):
        return (QtWidgets.QDialogButtonBox.StandardButton.Save
                | QtWidgets.QDialogButtonBox.StandardButton.Close)

    def _teardown(self):
        if self._running:
            self._abort = True
        self._pause()
        if self.picking_mode is not None:
            self._exit_pick()
        self._cursor.pop()
        if self._analysis_obj is not None:
            try:
                if getattr(self._analysis_obj.Proxy, "_live_panel", None) is self:
                    self._analysis_obj.Proxy._live_panel = None
            except Exception:
                pass
        self._remove_hover()
        if self._hover_label is not None:
            self._hover_label.deleteLater()
            self._hover_label = None
        self._destroy_legend()
        if self._scene is not None:
            self._scene.remove()
            self._scene = None
        self._clear_gates()
        try:
            Gui.Selection.removeObserver(self)
        except Exception:
            pass
        Gui.Control.closeDialog()

    def accept(self):
        if self._running:
            self._abort = True
            return False
        if self.result is not None:
            self._ensure_analysis_object()
            self._persisted = True
            self._saved = True
        unsaved = self._analysis_obj if not self._persisted else None
        self._teardown()
        if unsaved is not None:
            from .document import delete_analysis

            delete_analysis(unsaved)
        return True

    def reject(self):
        if self._running:
            # Close while running: stop first; the run's own cleanup follows.
            self._abort = True
            return False
        unsaved = self._analysis_obj if not self._persisted else None
        self._teardown()
        if unsaved is not None:
            from .document import delete_analysis

            delete_analysis(unsaved)
        return True


def _finite(values):
    import numpy as np

    return np.isfinite(values)
