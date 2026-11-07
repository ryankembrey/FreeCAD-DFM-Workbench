# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


from typing import Optional
import math
import time

from PySide6 import QtCore, QtGui, QtWidgets

from pivy import coin

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ..visuals import DirectionIndicator
from ..utils import resources  # noqa: F401
from ..utils.body_select import auto_select_body
from ..utils.colormap_combo import populate_colormap_combo
from ..utils.arrow_flip import ArrowFlipper
from ..utils.direction_row import (
    FLAT_BUTTON_CSS,
    PICK_SVG,
    DirectionRow,
    ViewSwitch,
    axis_name,
    icon_button,
    muted_color,
    style_primary_button,
)
from ..utils.picking import CrossCursorOverride, first_selected_subelement
from ..utils.pull_direction import PULL_DIRECTION_COLOR, compute_direction
from ..utils.pull_preview import PullPreview
from ..utils.qt_filters import EnterKeyFilter, EscapeFilter
from ..utils.view import get_active_view, get_view_widget, pick_document_object

from ...app.contour.meshing import generate_uniform_mesh
from ...app.contour.colormap import COLORMAPS
from ...app.contour.measures import BoolOption, ChoiceOption

from .scene import ContourNode, register, clear_all
from .legend import ContourLegend
from .category_legend import CategoryLegend
from .resolution import ResolutionField
from . import document  # noqa: F401

from ...app.contour.colormap import _HIGHLIGHT_IN as _FLAG_RGB

_LEGEND_STATE = {"pos": None, "size": None, "horizontal": False}
# Where the category legend sat and how big it was, for the next one.
_CATEGORY_LEGEND_STATE = {"pos": None, "size": None}

# Side actions: the color and help text of the DFM analysis setup.
_SIDE_ACTION_COLOR = (0.62, 0.32, 0.9)
_SIDE_ACTION_MAX = 6
_SIDE_ACTION_HELP = (
    "Directions the mold also opens in, besides the main pull: a slide, lifter, "
    "pickout or hand-loaded core. A face the main pull can't release but a side "
    "action can is shown in the side action's color, not as an undercut."
)
_ADD_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>'
)

# A value saved for "no value" (inf), which JSON can't hold everywhere.
_NO_VALUE_SAVED = 1e300


def _finite_range(values):
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return 0.0, 1.0
    return min(finite), max(finite)


class _SideAction:
    """One side action in the panel: a direction with its row and arrow."""

    def __init__(self, number, direction, ref):
        self.number = number
        self.dir = direction  # App.Vector, unit
        self.ref = ref  # "+X" or a picked "Face12"
        self.flipped = False
        self.anchor = None  # where a picked direction's arrow sits
        self.row = None
        self.indicator = DirectionIndicator(_SIDE_ACTION_COLOR, self.label)

    @property
    def label(self):
        return f"Side action {self.number}"

    def picked(self):
        return bool(self.ref) and self.ref[0] not in "+-\u2212"

_BAND_STEPS = {
    "Smooth": 0.0,
    "1 unit bands": 1.0,
    "2 unit bands": 2.0,
    "5 unit bands": 5.0,
    "10 unit bands": 10.0,
}


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
        self.pull_ref = "+Z"
        self.pull_flipped = False
        self.pull_anchor = None
        self._preview: Optional[PullPreview] = None
        self._pull_indicator_hidden = False
        self.indicator = (
            DirectionIndicator(PULL_DIRECTION_COLOR, "Pull direction")
            if measure.needs_pull_direction
            else None
        )

        self.picking_mode: Optional[str] = None
        self._cursor = CrossCursorOverride()
        # The pull direction arrow flips when clicked in the 3D view.
        self._flipper = ArrowFlipper()

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

        # Categorical measures (undercut): the legend hides/solos categories
        # and visits regions; the options the shown values were measured with.
        self._categorical = measure.is_categorical()
        self._cat_hidden = set()
        self._cat_solo = None
        self._regions = None
        self._region_index = {}
        self._region_camera = None
        self._measured_opts = None

        # Side actions (undercut).
        self._side_actions: list = []
        self._side_enabled = False
        self._side_box = None
        self._side_rows = None
        self._side_add = None
        self._side_holder = None

        self._build_form()
        Gui.Selection.addObserver(self)
        self._escape_filter = EscapeFilter(self._on_escape)
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

        # Settings: the pull direction (when the measure needs one) and the
        # measure's own options.
        self.direction_row = None
        self.pb_pull = None
        if (self.measure.needs_pull_direction or self.measure.options
                or self.measure.needs_side_actions):
            opt_box = QtWidgets.QGroupBox("Settings")
            og2 = self._grid(opt_box)
            orow = 0
            if self.measure.needs_pull_direction:
                self.direction_row = DirectionRow("Pull direction", color=PULL_DIRECTION_COLOR)
                self.direction_row.pick_btn.setToolTip(
                    "Pick a face or edge. Planar faces give their normal; cylindrical, conical "
                    "and toroidal faces and circular edges give their axis; other edges give "
                    "their tangent.")
                self.direction_row.axis_clicked.connect(self._on_pull_axis)
                self.direction_row.pick_clicked.connect(self._on_pick_pull)
                self.direction_row.flip_clicked.connect(self._on_flip_pull)
                self.pb_pull = self.direction_row.pick_btn
                self.flip_btn = self.direction_row.flip_btn
                og2.addWidget(self.direction_row, orow, 0, 1, 2)
                orow += 1
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
            if self.measure.needs_side_actions:
                self._build_side_actions(og2, orow)
                orow += 2
            root.addWidget(opt_box)

        # Mesh: changes here need "Update contour".
        mesh_box = QtWidgets.QGroupBox("Mesh")
        mg = self._grid(mesh_box)
        mg.addWidget(QtWidgets.QLabel("Resolution"), 0, 0)
        self.resolution = ResolutionField()
        self.resolution.changed.connect(self._on_resolution_changed)
        mg.addWidget(self.resolution, 0, 1)
        # Right under the model, above the settings.
        root.insertWidget(root.indexOf(obj_box) + 1, mesh_box)

        # Display: applies straight away. Pick the view first; only the
        # settings for that view show.
        disp_box = QtWidgets.QGroupBox("Display")
        cg = self._grid(disp_box)
        self.cb_highlight = ViewSwitch("Color scale", "Pass/fail")
        self.cb_highlight.buttons[0].setToolTip("Color every value along the color map")
        self.cb_highlight.buttons[1].setToolTip(
            "Flag values in a range red and show everything else gray")
        self.cb_highlight.toggled.connect(self._on_highlight_toggled)
        cg.addWidget(self.cb_highlight, 0, 0, 1, 2)
        cg.setRowMinimumHeight(1, 2)

        self.lbl_colormap = QtWidgets.QLabel("Color map")
        cg.addWidget(self.lbl_colormap, 2, 0)
        self.cb_colormap = QtWidgets.QComboBox()
        populate_colormap_combo(self.cb_colormap, COLORMAPS.keys())
        self.cb_colormap.setCurrentText(self.measure.default_colormap)
        self._compact_combo(self.cb_colormap)
        self.cb_colormap.currentIndexChanged.connect(self._on_style_changed)
        cg.addWidget(self.cb_colormap, 2, 1)

        self.lbl_bands = QtWidgets.QLabel("Bands")
        cg.addWidget(self.lbl_bands, 3, 0)
        self.cb_bands = QtWidgets.QComboBox()
        self.cb_bands.addItems(list(_BAND_STEPS.keys()))
        self.cb_bands.setToolTip("Quantize colors into fixed steps of the measured value.")
        self._compact_combo(self.cb_bands)
        self.cb_bands.currentIndexChanged.connect(self._on_style_changed)
        cg.addWidget(self.cb_bands, 3, 1)

        self.lbl_range = QtWidgets.QLabel("Range")
        self.lbl_range.setTextFormat(QtCore.Qt.TextFormat.RichText)
        cg.addWidget(self.lbl_range, 4, 0)
        default_opts = {o.id: o.default for o in self.measure.options}
        lo0, hi0 = self.measure.initial_range(default_opts) or (0.0, 1.0)
        bmin, bmax = self.measure.value_limits(default_opts) or (-1.0e6, 1.0e6)
        range_row = QtWidgets.QHBoxLayout()
        range_row.setContentsMargins(0, 0, 0, 0)
        range_row.setSpacing(4)
        self.sb_range_lo = QtWidgets.QDoubleSpinBox()
        self.sb_range_hi = QtWidgets.QDoubleSpinBox()
        self._spinbox_enter_filter = EnterKeyFilter(self.form)
        range_decimals = max(int(self.measure.range_decimals), 2)
        range_step = min(self.measure.range_step, 0.1)
        for sb, val in ((self.sb_range_lo, lo0), (self.sb_range_hi, hi0)):
            sb.setRange(bmin, bmax)
            sb.setDecimals(range_decimals)
            sb.setSingleStep(range_step)
            if self.measure.unit:
                sb.setSuffix(f" {self.measure.unit}")
            sb.setValue(val)
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
        cg.addWidget(range_w, 4, 1)

        self.lbl_flag_hint = QtWidgets.QLabel("Everything else is shown in gray.")
        self.lbl_flag_hint.setStyleSheet("background: transparent;")
        cg.addWidget(self.lbl_flag_hint, 5, 0, 1, 2)

        self.cb_smooth = QtWidgets.QCheckBox("Smooth shading")
        self.cb_smooth.setChecked(True)  # saved analyses keep their own setting
        self.cb_smooth.setToolTip(
            "Blend colors across triangles so the mesh facets disappear. "
            "Off shows one flat color per triangle."
        )
        self.cb_smooth.toggled.connect(self._on_smooth_changed)
        if self._categorical:
            # A fixed palette: no color map, bands, range or pass/fail. The
            # legend in the 3D view hides and solos categories instead, so
            # smooth shading is the only setting left: put it on the left.
            self.cb_highlight.setChecked(False)
            self.cb_highlight.hide()
            cg.addWidget(self.cb_smooth, 6, 0, 1, 2)
        else:
            cg.addWidget(self.cb_smooth, 6, 1)
        self.cb_arrows = None
        if self.measure.needs_pull_direction or self.measure.needs_side_actions:
            self.cb_arrows = QtWidgets.QCheckBox("Show direction arrows")
            self.cb_arrows.setChecked(True)
            self.cb_arrows.setToolTip(
                "Show the pull direction and side action arrows in the 3D view. "
                "Same as the Pull Direction item under the saved analysis in the tree.")
            self.cb_arrows.toggled.connect(self._on_arrows_toggled)
            if self._categorical:
                cg.addWidget(self.cb_arrows, 7, 0, 1, 2)
            else:
                cg.addWidget(self.cb_arrows, 7, 1)
        root.addWidget(disp_box)
        self._update_range_label()

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        self.pb_generate = QtWidgets.QPushButton()
        style_primary_button(self.pb_generate, "Generate contour")
        self.pb_generate.clicked.connect(self._on_generate)

        self.pb_clear = QtWidgets.QPushButton("Clear")
        self.pb_clear.setMinimumHeight(30)
        self.pb_clear.setEnabled(False)
        self.pb_clear.clicked.connect(self._on_clear)
        row.addWidget(self.pb_generate, 1)
        row.addWidget(self.pb_clear, 1)
        root.addLayout(row)

        # One message at a time, in the bar itself.
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.progress.hide()
        root.addWidget(self.progress)

        root.addStretch(1)
        self._refresh_direction_row()

    def _auto_select(self):
        auto_select_body(self._apply_object)

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

    def setPreselection(self, doc_name, obj_name, sub_name, *_):
        """Show a live direction arrow when hovering a face or edge while picking the pull direction."""
        if self._preview is not None and (
                self.picking_mode == "pull" or str(self.picking_mode).startswith("side:")):
            self._preview.show_for(doc_name, obj_name, sub_name)

    def removePreselection(self, *args):
        """Hide the direction preview when the mouse leaves the geometry."""
        if self._preview is not None:
            self._preview.hide()

    def _begin_pull_preview(self):
        # Hide the committed arrow while picking so it doesn't clash with the preview.
        if self.indicator is not None and not self._pull_indicator_hidden:
            self.indicator.set_visible(False)
            self._pull_indicator_hidden = True
        if self._preview is None:
            self._preview = PullPreview(PULL_DIRECTION_COLOR)
        self._preview.begin()

    def _end_pull_preview(self):
        if self._preview is not None:
            self._preview.end()
            self._preview = None
        if self._pull_indicator_hidden:
            self._pull_indicator_hidden = False
            if self.indicator is not None:
                self.indicator.set_visible(self._arrows_visible())

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
        elif str(self.picking_mode).startswith("side:"):
            ok = self._apply_side_from_selection(int(self.picking_mode.split(":")[1]))
        else:
            return
        if ok:
            self.picking_mode = None
            self._reset_pick_ui()
            Gui.Selection.clearSelection()
            self._update_generate_state()

    def _enter_pick(self, mode):
        self._reset_pick_ui()
        self.picking_mode = mode
        if mode == "pull":
            self._begin_pull_preview()
            self._refresh_direction_row()
        elif str(mode).startswith("side:"):
            if self._preview is None:
                self._preview = PullPreview(_SIDE_ACTION_COLOR)
            self._preview.begin()
            self._refresh_side_rows()
        else:
            self.pb_object.setChecked(True)
            self.le_object.setText("")
            self.le_object.setPlaceholderText("Click a model in the 3D view")
        self._flipper.set_enabled(False)
        self._cursor.push()

    def _reset_pick_ui(self):
        self._end_pull_preview()
        self.pb_object.setChecked(False)
        self.le_object.setText(self.target_object.Label if self.target_object is not None else "")
        self.le_object.setPlaceholderText("No model selected")
        self._refresh_direction_row()
        self._refresh_side_rows()
        self._flipper.set_enabled(True)
        self._cursor.pop()

    def _refresh_direction_row(self):
        if self.direction_row is None:
            return
        d = self.pull_dir
        self.direction_row.set_state(
            (d.x, d.y, d.z), self.pull_ref, self.pull_flipped, picking=self.picking_mode == "pull")

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
        self._enter_pick("object")

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
        self._refresh_side_arrows()
        self._mark_dirty()
        return True

    def _on_pick_pull(self):
        if self.picking_mode == "pull":
            self.picking_mode = None
            self._reset_pick_ui()
            return
        # hide contour so user can select from model
        if self._has_contour:
            self._on_clear()
            App.Console.PrintMessage(
                "DFM contour: cleared so you can pick a new pull direction; regenerate when done.\n"
            )
            self._enter_pick("pull")
            return
        if self._apply_pull_from_selection():
            self._reset_pick_ui()
            Gui.Selection.clearSelection()
            return
        self._enter_pick("pull")

    def _apply_pull_from_selection(self):
        try:
            picked = first_selected_subelement()
            if picked is None:
                return False
            sub, name = picked
            # Same rules as task setup: cylinder/cone/torus faces and circle/arc edges
            # give their axis; planar faces give the normal; other edges the tangent.
            result = compute_direction(sub)
            if result is None:
                App.Console.PrintWarning("DFM contour: pick a face or an edge.\n")
                return False
            pnt, direction = result
            self.pull_dir = App.Vector(direction.x, direction.y, direction.z)
            self.pull_anchor = App.Vector(pnt.x, pnt.y, pnt.z)
            self.pull_ref = name
            self.pull_flipped = False
            self._refresh_direction_row()
            self._refresh_arrow()
            self._mark_dirty()
            return True
        except Exception as exc:
            App.Console.PrintError(f"DFM contour: could not read pull direction. {exc}\n")
            return False

    def _on_flip_pull(self):
        if self.picking_mode == "pull":
            return
        self.pull_dir = App.Vector(-self.pull_dir.x, -self.pull_dir.y, -self.pull_dir.z)
        self.pull_flipped = not self.pull_flipped
        self._refresh_direction_row()
        self._refresh_arrow()
        self._mark_dirty()

    def _on_pull_axis(self, vec):
        """Point along an axis, such as (0, 0, -1)."""
        if self.picking_mode == "pull":
            self.picking_mode = None
            self._reset_pick_ui()
        self.pull_dir = App.Vector(*vec)
        self.pull_ref = axis_name(vec).replace("\u2212", "-")
        self.pull_flipped = False
        self.pull_anchor = None  # the arrow sits at the model's center
        self._refresh_direction_row()
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
        self._flipper.add(self.indicator, self._on_flip_pull)

    # -- Side actions ----------------------------------------------------------

    def _build_side_actions(self, grid, row):
        """A "Side actions" checkbox, then (when ticked) a row per side action."""
        from ..rule_cards import apply_svg_icon

        self._side_box = QtWidgets.QCheckBox("Side actions")
        self._side_box.setToolTip(_SIDE_ACTION_HELP)
        self._side_box.setMinimumHeight(28)
        self._side_box.toggled.connect(self._on_side_toggled)
        grid.addWidget(self._side_box, row, 0, 1, 2)

        holder = QtWidgets.QWidget()
        outer = QtWidgets.QHBoxLayout(holder)
        outer.setContentsMargins(4, 0, 0, 0)
        outer.setSpacing(0)
        block = QtWidgets.QFrame()
        block.setObjectName("dfmSideActions")
        block.setStyleSheet(
            "QFrame#dfmSideActions { border: none; border-left: 2px solid rgba(158, 82, 230, 150); }")
        rows = QtWidgets.QVBoxLayout(block)
        rows.setContentsMargins(8, 0, 0, 0)
        rows.setSpacing(2)
        outer.addWidget(block)
        self._side_rows = QtWidgets.QVBoxLayout()
        self._side_rows.setContentsMargins(0, 0, 0, 0)
        self._side_rows.setSpacing(2)
        rows.addLayout(self._side_rows)
        add_row = QtWidgets.QHBoxLayout()
        add_row.setContentsMargins(0, 2, 0, 0)
        self._side_add = QtWidgets.QPushButton("Add side action")
        self._side_add.setStyleSheet(FLAT_BUTTON_CSS.replace("padding: 0px", "padding: 3px 6px"))
        self._side_add.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._side_add.setToolTip("Add a slide, lifter or pickout direction")
        apply_svg_icon(self._side_add, _ADD_SVG, 14)
        self._side_add.clicked.connect(lambda _c=False: self._add_side_action())
        add_row.addWidget(self._side_add)
        add_row.addStretch(1)
        rows.addLayout(add_row)
        grid.addWidget(holder, row + 1, 0, 1, 2)
        self._side_holder = holder
        holder.setVisible(False)

    def _on_side_toggled(self, on):
        self._side_enabled = bool(on)
        if self._side_holder is not None:
            self._side_holder.setVisible(self._side_enabled)
        if self._side_enabled and not self._side_actions:
            self._add_side_action()
            return
        self._refresh_side_arrows()
        self._mark_dirty()

    def _side_anchor(self, item):
        """Where an axis side action's arrow sits: on the side of the part it comes from."""
        if item.anchor is not None and item.picked():
            return item.anchor
        if self.target_shape is None:
            return None
        bb = self.target_shape.BoundBox
        c = bb.Center
        d = item.dir
        reach = (abs(d.x) * bb.XLength + abs(d.y) * bb.YLength + abs(d.z) * bb.ZLength) / 2
        return App.Vector(c.x + d.x * reach, c.y + d.y * reach, c.z + d.z * reach)

    def _free_side_axis(self):
        """The axis across the pull that no side action uses yet."""
        pull = self.pull_dir
        used = {(round(i.dir.x), round(i.dir.y), round(i.dir.z)) for i in self._side_actions}
        axes = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
        axes.sort(key=lambda a: abs(a[0] * pull.x + a[1] * pull.y + a[2] * pull.z))
        for axis in axes:
            if axis not in used:
                return axis
        return axes[0]

    def _add_side_action(self, direction=None, ref=None, flipped=False, anchor=None, mark=True):
        if len(self._side_actions) >= _SIDE_ACTION_MAX:
            return
        if direction is None:
            axis = self._free_side_axis()
            direction = App.Vector(*axis)
            ref = axis_name(axis).replace("\u2212", "-")
        item = _SideAction(len(self._side_actions) + 1, App.Vector(direction).normalize(), ref or "")
        item.flipped = bool(flipped)
        item.anchor = anchor
        self._side_actions.append(item)
        self._rebuild_side_rows()
        self._refresh_side_arrows()
        if mark:
            self._mark_dirty()

    def _remove_side_action(self, item):
        if item not in self._side_actions:
            return
        if self.picking_mode == f"side:{self._side_actions.index(item)}":
            self.picking_mode = None
            self._reset_pick_ui()
        self._flipper.discard(item.indicator)
        item.indicator.remove()
        self._side_actions.remove(item)
        for number, other in enumerate(self._side_actions, start=1):
            if other.number != number:
                other.number = number
                self._flipper.discard(other.indicator)
                other.indicator.remove()
                other.indicator = DirectionIndicator(_SIDE_ACTION_COLOR, other.label)
        self._rebuild_side_rows()
        self._refresh_side_arrows()
        self._mark_dirty()

    def _rebuild_side_rows(self):
        if self._side_rows is None:
            return
        while self._side_rows.count():
            w = self._side_rows.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for i, item in enumerate(self._side_actions):
            row = DirectionRow(item.label, removable=True, color=_SIDE_ACTION_COLOR)
            row.axis_clicked.connect(lambda vec, k=i: self._on_side_axis(k, vec))
            row.pick_clicked.connect(lambda k=i: self._on_pick_side(k))
            row.flip_clicked.connect(lambda k=i: self._on_flip_side(k))
            row.remove_clicked.connect(lambda it=item: self._remove_side_action(it))
            item.row = row
            self._side_rows.addWidget(row)
        if self._side_add is not None:
            self._side_add.setEnabled(len(self._side_actions) < _SIDE_ACTION_MAX)
        self._refresh_side_rows()

    def _refresh_side_rows(self):
        for i, item in enumerate(self._side_actions):
            if item.row is None:
                continue
            picking = self.picking_mode == f"side:{i}"
            try:
                item.row.pick_btn.setChecked(picking)
                item.row.set_state((item.dir.x, item.dir.y, item.dir.z), item.ref, item.flipped,
                                   picking=picking)
            except RuntimeError:  # the row was deleted
                item.row = None

    def _refresh_side_arrows(self):
        for item in self._side_actions:
            anchor = self._side_anchor(item) if self._side_enabled else None
            if anchor is None:
                item.indicator.remove()
                continue
            item.indicator.set_visible(self._arrows_visible())
            item.indicator.show(anchor, App.Vector(item.dir.x, item.dir.y, item.dir.z))
            self._flipper.add(item.indicator, lambda it=item: self._on_flip_side(
                self._side_actions.index(it) if it in self._side_actions else -1))

    def _on_side_axis(self, index, vec):
        if not 0 <= index < len(self._side_actions):
            return
        if self.picking_mode == f"side:{index}":
            self.picking_mode = None
            self._reset_pick_ui()
        item = self._side_actions[index]
        item.dir = App.Vector(*vec)
        item.ref = axis_name(vec).replace("\u2212", "-")
        item.flipped = False
        item.anchor = None
        self._refresh_side_rows()
        self._refresh_side_arrows()
        self._mark_dirty()

    def _on_flip_side(self, index):
        if not 0 <= index < len(self._side_actions):
            return
        if str(self.picking_mode).startswith("side:"):
            return
        item = self._side_actions[index]
        item.dir = App.Vector(-item.dir.x, -item.dir.y, -item.dir.z)
        item.flipped = not item.flipped
        if not item.picked():
            item.ref = axis_name((item.dir.x, item.dir.y, item.dir.z)).replace("\u2212", "-")
            item.flipped = False
        self._refresh_side_rows()
        self._refresh_side_arrows()
        self._mark_dirty()

    def _on_pick_side(self, index):
        mode = f"side:{index}"
        if self.picking_mode == mode:
            self.picking_mode = None
            self._reset_pick_ui()
            return
        if self._has_contour:
            self._on_clear()
            App.Console.PrintMessage(
                "DFM contour: cleared so you can pick a side action; regenerate when done.\n"
            )
        elif self._apply_side_from_selection(index):
            self._reset_pick_ui()
            Gui.Selection.clearSelection()
            return
        self._enter_pick(mode)

    def _apply_side_from_selection(self, index):
        if not 0 <= index < len(self._side_actions):
            return False
        try:
            picked = first_selected_subelement()
            if picked is None:
                return False
            sub, name = picked
            result = compute_direction(sub)
            if result is None:
                App.Console.PrintWarning("DFM contour: pick a face or an edge.\n")
                return False
            pnt, direction = result
            item = self._side_actions[index]
            item.dir = App.Vector(direction.x, direction.y, direction.z).normalize()
            item.anchor = App.Vector(pnt.x, pnt.y, pnt.z)
            item.ref = name
            item.flipped = False
            self._refresh_side_rows()
            self._refresh_side_arrows()
            self._mark_dirty()
            return True
        except Exception as exc:
            App.Console.PrintError(f"DFM contour: could not read side action direction. {exc}\n")
            return False

    def _side_action_options(self):
        if not self._side_enabled:
            return []
        return [
            {"dir": (i.dir.x, i.dir.y, i.dir.z), "ref": i.ref, "flipped": i.flipped,
             "anchor": None if i.anchor is None else (i.anchor.x, i.anchor.y, i.anchor.z)}
            for i in self._side_actions
        ]

    def _load_side_actions(self, items):
        for item in list(self._side_actions):
            self._flipper.discard(item.indicator)
            item.indicator.remove()
        self._side_actions = []
        for entry in items or []:
            try:
                d = entry.get("dir")
                anchor = entry.get("anchor")
                self._add_side_action(
                    App.Vector(*d), entry.get("ref", ""), entry.get("flipped", False),
                    App.Vector(*anchor) if anchor else None, mark=False)
            except Exception:
                continue
        if self._side_box is not None:
            self._side_box.blockSignals(True)
            self._side_box.setChecked(bool(self._side_actions))
            self._side_box.blockSignals(False)
        self._side_enabled = bool(self._side_actions)
        if self._side_holder is not None:
            self._side_holder.setVisible(self._side_enabled)
        self._refresh_side_arrows()

    # -- Display -----------------------------------------------------------------

    def _cmap(self):
        """The color map to draw with: the measure's palette when categorical."""
        if self._categorical:
            return self.measure.default_colormap
        return self.cb_colormap.currentText()

    def _band(self):
        if self._categorical:
            return 0.0
        return _BAND_STEPS.get(self.cb_bands.currentText(), 0.0)

    def _highlight_spec(self):
        from ...app.contour.colormap import HighlightSpec

        if self._categorical:
            return HighlightSpec(active=False, muted=self._muted_codes())
        if not getattr(self, "cb_highlight", None) or not self.cb_highlight.isChecked():
            return HighlightSpec(active=False)
        lo, hi = self._current_range()
        return HighlightSpec(active=True, lo=lo, hi=hi)

    def _on_highlight_toggled(self, _checked=False):
        spec = self._highlight_spec()
        if self._node is not None:
            low, high = self._current_range()
            self._node.recolor(low, high, self._cmap(), self._band(), spec)
        if self._legend is not None:
            self._legend.set_highlight(spec)
        self._update_range_label()

    def _update_range_label(self):
        """Show the settings for the current view: color scale or pass/fail."""
        if not hasattr(self, "lbl_range"):
            return
        if self._categorical:
            for widget in (self.lbl_colormap, self.cb_colormap, self.lbl_bands, self.cb_bands,
                           self.lbl_range, self.sb_range_lo.parentWidget(), self.lbl_flag_hint):
                widget.setVisible(False)
            return
        flag = self.cb_highlight.isChecked()
        for widget in (self.lbl_colormap, self.cb_colormap, self.lbl_bands, self.cb_bands):
            widget.setVisible(not flag)
        self.lbl_flag_hint.setVisible(flag)
        if flag:
            r, g, b = (round(c * 255) for c in _FLAG_RGB)
            self.lbl_range.setText(
                f'<span style="color: rgb({r}, {g}, {b})">\u25a0</span>&nbsp;Flag values from')
            tip = ("Values in this range are flagged red. "
                   "You can also drag the legend ends in the 3D view.")
        else:
            self.lbl_range.setText("Range")
            tip = ("The values the color map spans. "
                   "You can also drag the legend ends in the 3D view.")
        for sb in (self.sb_range_lo, self.sb_range_hi):
            sb.setToolTip(tip)
        self.lbl_flag_hint.setStyleSheet(
            f"background: transparent; color: {muted_color(self.lbl_flag_hint)};")

    def _options(self):
        out = {}
        for oid, w in self._option_widgets.items():
            if isinstance(w, QtWidgets.QComboBox):
                out[oid] = w.currentText()
            else:
                out[oid] = w.isChecked()
        if self.measure.needs_side_actions:
            out["side_actions"] = self._side_action_options()
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
            self.pb_generate.setText("Generate contour")
            self.pb_generate.setEnabled(valid)
            if self.target_object is None:
                tip = "Select a model first."
            elif not self.resolution.is_safe():
                tip = "Resolution is too fine; choose a coarser one."
            else:
                tip = "Run the contour."
        else:
            self.pb_generate.setText("Update contour")
            self.pb_generate.setEnabled(valid and self._dirty)
            if self.target_object is None:
                tip = "Select a model first."
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
        # Not a busy bar: Qt shows no text on one.
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("Meshing the model")
        QtWidgets.QApplication.processEvents()
        try:
            self._mesh = generate_uniform_mesh(
                self.target_shape, size, with_curvature=self.measure.needs_curvature)
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
        message = getattr(self.measure, "progress_text", "Measuring").replace("%", "%%")
        self.progress.setFormat(f"{message}  %p%")
        try:

            def progress_cb(done, total):
                self.progress.setValue(done)
                QtWidgets.QApplication.processEvents(
                    QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                )

            opts = self._options()
            values, normals = self.measure.measure(
                self.target_shape,
                self._mesh,
                pull=pull,
                options=opts,
                progress_cb=progress_cb,
            )
            dmin, dmax = _finite_range(values)
            self._measured_opts = opts
            self._regions = None
            self._region_index = {}
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
            self._node.recolor(low, high, self._cmap(), self._band(), spec)
        if self._legend is not None:
            self._legend.set_range(low, high)
            self._legend.set_highlight(spec)

    def _on_range_changed(self, low, high):
        self._set_range_spins(low, high)
        spec = self._highlight_spec()
        if self._node is not None:
            self._node.recolor(low, high, self._cmap(), self._band(), spec)
        if self._legend is not None:
            self._legend.set_highlight(spec)

    def _on_style_changed(self):
        if self._node is None:
            return
        low, high = self._current_range()
        colormap = self._cmap()
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
        opts = self._options()
        dom_lo, dom_hi = self.measure.data_domain(dmin, dmax, opts)
        if not self._range_initialized:
            rng = self.measure.initial_range(opts)
            if rng is None:
                rng = self.measure.initial_range_for_data(values, opts)
            low, high = (dmin, dmax) if rng is None else rng
            self._range_initialized = True
        else:
            low, high = self._current_range()
        low = max(dom_lo, min(low, dom_hi))
        high = max(dom_lo, min(high, dom_hi))
        if low > high:
            low, high = high, low
        self._set_range_spins(low, high)
        colormap = self._cmap()
        band = self._band()

        node = ContourNode(self.target_object)
        smooth = self.cb_smooth.isChecked()
        span = dom_hi - dom_lo
        value_gap = 0.5 * span if (smooth and span > 0) else None
        if self._categorical:
            value_gap = 0.5  # categories never blend into each other
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

        view_widget = get_view_widget()
        if view_widget is not None and self._categorical:
            self._build_category_legend(view_widget, mesh, values)
        elif view_widget is not None:
            self._legend = ContourLegend(view_widget)
            self._legend.set_no_value_label(
                self.measure.no_value_label if any(not math.isfinite(v) for v in values) else None)
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

    # -- Category legend ---------------------------------------------------------

    def _build_category_legend(self, view_widget, mesh, values):
        from .regions import area_shares, triangle_areas

        opts = self._measured_opts if self._measured_opts is not None else self._options()
        cats = self.measure.categories_for(opts) or []
        try:
            areas = triangle_areas(mesh.vertices, mesh.triangles)
            shares = area_shares(values, areas)
        except Exception:
            areas, shares = None, {}
        self._areas = areas
        legend = CategoryLegend(view_widget)
        # Black or white text from the viewport background, as on the gradient
        # legend (a saved analysis's legend may override it; applied below).
        try:
            from .document import auto_legend_text_rgb, legend_text_rgb

            rgb = (legend_text_rgb(self._analysis_obj) if self._analysis_obj is not None
                   else auto_legend_text_rgb())
            legend.set_text_color(rgb)
        except Exception:
            pass
        legend.configure(self.measure.label)
        legend.set_categories(cats, shares)
        legend.set_muted_state(self._cat_hidden, self._cat_solo)
        legend.mutedChanged.connect(self._on_categories_muted)
        legend.regionRequested.connect(self._on_region_requested)
        self._legend = legend
        legend.show()
        legend.raise_()
        self._restore_legend_geometry()
        self._ensure_legend_object()
        for code, (index, count) in self._region_index.items():
            legend.set_region_position(code, index, count)

    def _muted_codes(self):
        if self._cat_solo is not None and self._legend is not None and isinstance(
                self._legend, CategoryLegend):
            return self._legend.muted()
        if self._cat_solo is not None:
            cats = self.measure.categories_for(self._measured_opts or {}) or []
            return frozenset(c.code for c in cats if c.code != self._cat_solo)
        return frozenset(self._cat_hidden)

    def _on_categories_muted(self, _muted):
        if isinstance(self._legend, CategoryLegend):
            self._cat_hidden, self._cat_solo = self._legend.muted_state()
        if self._node is not None:
            low, high = self._current_range()
            self._node.recolor(low, high, self._cmap(), self._band(), self._highlight_spec())

    def _on_region_requested(self, code, step):
        if self._last is None or self.target_object is None:
            return
        from .regions import CategoryRegions, RegionCamera

        mesh, values, normals, _dmin, _dmax = self._last
        if self._regions is None:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
            try:
                self._regions = CategoryRegions(
                    mesh.vertices, mesh.triangles, values, normals, getattr(self, "_areas", None))
                regions = self._regions.regions(code)
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
        else:
            regions = self._regions.regions(code)
        if not regions:
            return
        index, _count = self._region_index.get(code, (-1 if step > 0 else 0, len(regions)))
        index = (index + step) % len(regions)
        self._region_index[code] = (index, len(regions))
        if isinstance(self._legend, CategoryLegend):
            self._legend.set_region_position(code, index, len(regions))
        if self._region_camera is None or self._region_camera.target_object is not self.target_object:
            self._region_camera = RegionCamera(self.target_object)
        self._region_camera.show(regions[index])
        region = regions[index]
        Gui.getMainWindow().statusBar().showMessage(
            f"{self.measure.format_value(code)}: region {index + 1} of {len(regions)}, "
            f"{region.area:.1f} mm\u00b2", 6000)

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
        dom_lo, dom_hi = self.measure.data_domain(dmin, dmax, self._options())
        low = max(dom_lo, min(dmin, dom_hi))
        high = max(dom_lo, min(dmax, dom_hi))
        if low > high:
            low, high = high, low
        self._set_range_spins(low, high)
        spec = self._highlight_spec()
        if self._node is not None:
            self._node.recolor(low, high, self._cmap(), self._band(), spec)
        if self._legend is not None:
            self._legend.set_range(low, high)
            self._legend.set_highlight(spec)

    def _restore_legend_geometry(self):
        if self._legend is None:
            return
        if isinstance(self._legend, CategoryLegend):
            size = _CATEGORY_LEGEND_STATE.get("size")
            if size is not None:
                self._legend.resize(size)
            pos = _CATEGORY_LEGEND_STATE.get("pos")
            if pos is None:
                # First time: centered along the top of the 3D view, clear of
                # the tree and the task panel docked over its sides.
                parent = self._legend.parentWidget()
                pw = parent.width() if parent is not None else self._legend.width()
                pos = QtCore.QPoint(max(0, (pw - self._legend.width()) // 2), 12)
            self._legend.move(pos)
            self._legend._clamp_into_parent()
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
        self._regions = None
        self._region_index = {}
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
            "values": [v if math.isfinite(v) else _NO_VALUE_SAVED for v in values],
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
            self._update_range_label()
            for oid, w in self._option_widgets.items():
                if oid in (obj.Options or {}):
                    val = obj.Options[oid]
                    w.blockSignals(True)
                    if isinstance(w, QtWidgets.QComboBox):
                        w.setCurrentText(str(val))
                    else:
                        w.setChecked(bool(val))
                    w.blockSignals(False)
            self._measured_opts = dict(obj.Options or {})
            if self.measure.needs_pull_direction:
                self.pull_dir = App.Vector(obj.PullDirection)
                self.pull_ref = obj.PullReference or self.pull_ref
                if self.pull_ref.endswith(" (default)"):  # saved by older versions
                    self.pull_ref = self.pull_ref[: -len(" (default)")]
                self._refresh_direction_row()
                self._refresh_arrow()
            if self.measure.needs_side_actions:
                self._load_side_actions((obj.Options or {}).get("side_actions") or [])
            self._set_range_spins(obj.RangeLow, obj.RangeHigh)

            field = getattr(obj, "FieldData", None)
            if field:
                import types

                stub = types.SimpleNamespace(
                    vertices=field["vertices"], triangles=field["triangles"]
                )
                self._auto_range = False
                self._range_initialized = True
                values = [
                    math.inf if v >= 0.5 * _NO_VALUE_SAVED else float(v) for v in field["values"]
                ]
                self._last = (
                    stub,
                    values,
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
                if isinstance(self._legend, CategoryLegend):
                    _CATEGORY_LEGEND_STATE["pos"] = self._legend.pos()
                    _CATEGORY_LEGEND_STATE["size"] = self._legend.size()
                else:
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
        from .document import direction_child, ensure_direction_object

        existed = direction_child(analysis) is not None
        try:
            self._direction_obj = ensure_direction_object(analysis)
        except Exception as exc:
            App.Console.PrintWarning(f"DFM contour: could not create direction item. {exc}\n")
            return
        obj = self._direction_obj
        if obj is None or not App.GuiUp or obj.ViewObject is None:
            return
        try:
            if existed:
                # A saved analysis: its tree item says whether the arrows show.
                self.apply_indicator_visible(bool(obj.ViewObject.Visibility))
            elif bool(obj.ViewObject.Visibility) != self._arrows_visible():
                # A new one: the tree item starts as the checkbox is.
                obj.ViewObject.Visibility = self._arrows_visible()
        except Exception:
            pass

    def _arrows_visible(self):
        cb = getattr(self, "cb_arrows", None)
        return True if cb is None else cb.isChecked()

    def _apply_arrow_visibility(self):
        visible = self._arrows_visible()
        if self.indicator is not None and not self._pull_indicator_hidden:
            try:
                self.indicator.set_visible(visible)
            except Exception:
                pass
        for item in self._side_actions:
            try:
                item.indicator.set_visible(visible)
            except Exception:
                pass

    def _on_arrows_toggled(self, checked):
        self._apply_arrow_visibility()
        obj = self._direction_obj
        if obj is None or not App.GuiUp:
            return
        try:
            if obj.ViewObject is not None and bool(obj.ViewObject.Visibility) != bool(checked):
                obj.ViewObject.Visibility = bool(checked)  # calls back apply_indicator_visible
        except Exception:
            pass

    def apply_indicator_visible(self, visible):
        """The tree item's visibility changed (or a saved analysis was opened)."""
        cb = getattr(self, "cb_arrows", None)
        if cb is not None and cb.isChecked() != bool(visible):
            cb.blockSignals(True)
            cb.setChecked(bool(visible))
            cb.blockSignals(False)
        self._apply_arrow_visibility()

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
        view = get_active_view()
        if view is None:
            return
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
        view_widget = get_view_widget()
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
        vw = get_view_widget()
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

    def _pick_contour(self, event_cb):
        """(key, value, point) of the contour under the cursor, or None.

        The body stays visible just underneath the contour, and FreeCAD draws it
        with its own tessellation, so the view's single picked point lands on the
        body or the contour more or less at random. Cast our own ray, keep every
        hit, and take the first one on the contour, unless something else (not
        the body just underneath) is clearly in front of it.
        """
        node = self._node
        view = self._hover_view or get_active_view()
        if node is None or view is None:
            return None
        try:
            pos = event_cb.getEvent().getPosition()
            render_manager = view.getViewer().getSoRenderManager()
            action = coin.SoRayPickAction(render_manager.getViewportRegion())
            action.setPoint(coin.SbVec2s(int(pos[0]), int(pos[1])))
            action.setPickAll(True)
            action.apply(render_manager.getSceneGraph())
            picked = action.getPickedPointList()
            try:
                count = picked.getLength()
            except Exception:
                count = len(picked)
            first = None
            for i in range(count):
                pp = picked[i]
                point = pp.getPoint()
                if first is None:
                    first = (point[0], point[1], point[2])
                result = node.pick_value(pp)
                if result is None:
                    continue
                hit = result[2]
                gap = math.dist(first, hit)
                return result if gap <= self._occlusion_tolerance() else None
            return None
        except Exception:
            try:
                return node.pick_value(event_cb.getPickedPoint())
            except Exception:
                return None

    def _occlusion_tolerance(self):
        """How far in front of the contour a hit may be and still be the body
        underneath it (its coarser facets), not something hiding the contour."""
        try:
            diag = self.target_shape.BoundBox.DiagonalLength
        except Exception:
            diag = 100.0
        return max(0.01 * diag, 0.05)

    def _probe_at_cursor(self, event_cb):
        view = self._hover_view
        if view is None:
            return None
        return pick_document_object(view, event_cb.getEvent())

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
        result = self._pick_contour(event_cb)

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
        result = self._pick_contour(event_cb)
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
        text = self.measure.probe_text(value)

        obj = doc.addObject("App::FeaturePython", f"Probe{index:03d}")
        ContourProbeFeature(obj)
        obj.Parent = analysis
        obj.Position = App.Vector(point[0], point[1], point[2])
        obj.Value = float(value) if math.isfinite(value) else 0.0
        obj.Unit = unit
        obj.FormattedValue = formatted
        if text:
            obj.DisplayText = text
            obj.Label = f"Probe {index} - {text}"
        else:
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
        self._flipper.detach()
        if self.indicator is not None:
            self.indicator.remove()
        for item in self._side_actions:
            try:
                item.indicator.remove()
            except Exception:
                pass
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
