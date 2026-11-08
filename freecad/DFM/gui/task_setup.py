# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from pathlib import Path
from typing import Optional, Any
from PySide6 import QtGui, QtWidgets, QtCore

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore
import Part  # type: ignore

from OCP.gp import gp_Dir

from ..core.registries.process_registry import ProcessRegistry
from ..app.analysis_runner import AnalysisRunner
from ..app.process_requirements import required_inputs
from ..core.models import CheckResult, GeometryRef, ProcessRequirement
from ..core.utils.conversion import freecad_to_ocp
from ..core.utils.nominal_wall import (
    AUTO as NOMINAL_AUTO,
    MANUAL as NOMINAL_MANUAL,
    make_requirement as make_nominal_requirement,
    parse_requirement as parse_nominal_requirement,
)

from ..app.history import HistoryManager

from ..gui.utils import resources  # noqa: F401
from ..gui.results.bridge import DFMViewProvider
from ..gui.results.models import DFMReportModel
from ..gui.results.presenter import TaskResultsPresenter
from ..gui.task_results import TaskResults
from ..gui.visuals import DirectionIndicator
from ..gui.utils.body_select import auto_select_body
from ..gui.utils.picking import (
    PICK_BUTTON_STYLE,
    CrossCursorOverride,
    first_selected_subelement,
)
from ..gui.utils.pull_direction import PULL_DIRECTION_COLOR, compute_direction
from ..gui.utils.pull_preview import PullPreview
from ..gui.utils.qt_filters import EscapeFilter
from ..gui.utils.direction_row import (
    FLAT_BUTTON_CSS, ICON_BUTTON_CSS, PICK_SVG, DirectionRow, ViewSwitch, axis_name, axis_of, icon_button,
    is_element_ref, muted_color, style_primary_button,
)
from ..gui.utils.arrow_flip import ArrowFlipper
from ..gui.rule_cards import apply_svg_icon

# Labels in sentence case, as the FreeCAD handbook asks.
_REQUIREMENT_LABELS = {
    ProcessRequirement.PULL_DIRECTION: "Pull direction",
    ProcessRequirement.SIDE_ACTIONS: "Side actions",
    ProcessRequirement.PRINT_ORIENTATION: "Print direction",
    ProcessRequirement.NEUTRAL_PLANE: "Neutral plane",
    ProcessRequirement.NOMINAL_WALL_THICKNESS: "Nominal wall",
}

_muted = muted_color
_icon_button = icon_button
_ICON_BUTTON_CSS = ICON_BUTTON_CSS


class RequirementHandler(QtCore.QObject):
    """Base handler: owns the widgets and state for one ProcessRequirement."""

    def __init__(self, requirement: ProcessRequirement):
        super().__init__()
        self.requirement = requirement

        self.value: Any = None
        self.pnt = None
        self.ref: str = ""
        self.flipped: bool = False

        self.pb: Optional[QtWidgets.QPushButton] = None
        self.le: Optional[QtWidgets.QLineEdit] = None
        self.flip_btn: Optional[QtWidgets.QAbstractButton] = None
        self._orig_text: str = ""

    @property
    def label(self) -> str:
        return _REQUIREMENT_LABELS.get(
            self.requirement, self.requirement.name.replace("_", " ").capitalize())

    def pick_hint(self) -> str:
        return "Click a face"

    def build_row(self, layout: QtWidgets.QLayout, on_pick) -> None:
        """Create this requirement's row of widgets and append to `layout`."""
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)

        self.pb = QtWidgets.QPushButton(f"Select {self.label}")
        self.pb.setMinimumHeight(28)
        self.pb.setCheckable(True)
        self.pb.setStyleSheet(PICK_BUTTON_STYLE)
        self._orig_text = self.pb.text()
        self.pb.clicked.connect(lambda _checked=False: on_pick(self))

        self.le = QtWidgets.QLineEdit()
        self.le.setMinimumHeight(28)
        self.le.setReadOnly(True)
        self.le.setPlaceholderText("Not defined")

        right = QtWidgets.QHBoxLayout()
        right.setSpacing(4)
        right.addWidget(self.le, 1)
        self._build_extra(right)

        row.addWidget(self.pb, 1)
        row.addLayout(right, 1)
        layout.addLayout(row)

        self.refresh_display()

    def _build_extra(self, row: QtWidgets.QHBoxLayout) -> None:
        """Hook for subclasses to add trailing widgets (e.g. a flip button)."""
        pass

    def detach(self) -> None:
        """Drop references to widgets that are about to be deleted."""
        self.pb = None
        self.le = None
        self.flip_btn = None

    def refresh_display(self) -> None:
        if self.le is None:
            return
        if self.ref:
            suffix = " (flipped)" if self.flipped else ""
            self.le.setText(f"{self.ref}{suffix}")
        else:
            self.le.setText("")

    def reset_ui(self) -> None:
        """Return the pick button to its idle look."""
        if self.pb is not None:
            self.pb.setText(self._orig_text)
            self.pb.setChecked(False)

    def enter_picking_ui(self) -> None:
        if self.pb is not None:
            self.pb.setText(self.pick_hint())
            self.pb.setChecked(True)

    def apply_selection(self) -> bool:
        """Read the current 3D selection into state. True on success."""
        raise NotImplementedError

    def is_satisfied(self) -> bool:
        return self.value is not None

    def clear(self) -> None:
        self.value = None
        self.pnt = None
        self.ref = ""
        self.flipped = False

    def remove_indicator(self) -> None:
        """Remove any 3D indicator this handler owns. Base handlers have none."""
        pass


# =============================================================================


class VectorRequirement(RequirementHandler):
    """A direction picked from a face normal or an edge tangent (with flip)."""

    _COLORS = {
        ProcessRequirement.PULL_DIRECTION: PULL_DIRECTION_COLOR,
        ProcessRequirement.PRINT_ORIENTATION: (0.15, 0.45, 1.0),  # blue
        ProcessRequirement.SIDE_ACTIONS: (0.62, 0.32, 0.9),  # purple
    }

    def __init__(self, requirement):
        super().__init__(requirement)
        color = self._COLORS.get(requirement, (1.0, 0.15, 0.15))
        self.indicator = DirectionIndicator(color, self.label)
        self.row: Optional[DirectionRow] = None
        self.axis_btns: list[QtWidgets.QToolButton] = []
        self.ref_label: Optional[QtWidgets.QLabel] = None
        # Set by the panel: where an axis direction's arrow sits, and what
        # to do after the direction changes, its row is hovered, or its
        # direction list opens.
        self.center_provider = lambda: App.Vector(0, 0, 0)
        self.on_changed = lambda: None
        self.on_hover = lambda _handler, _on: None
        self.on_editing = lambda _handler, _on: None

    def pick_hint(self) -> str:
        return "Click a face or edge"

    def build_row(self, layout: QtWidgets.QLayout, on_pick) -> None:
        """Label, then the picked face (if any), +X/+Y/+Z, pick and flip."""
        self.row = DirectionRow(self.label)
        self._connect_row_signals()
        self.row.axis_clicked.connect(self.set_axis)
        self.row.pick_clicked.connect(lambda: on_pick(self))
        self.row.flip_clicked.connect(self.flip)
        self.pb = self.row.pick_btn
        self.flip_btn = self.row.flip_btn
        self.axis_btns = self.row.axis_btns
        self.ref_label = self.row.ref_label
        layout.addWidget(self.row)
        self.refresh_display()

    def _connect_row_signals(self) -> None:
        self.row.hovered.connect(lambda on: self.on_hover(self, on))
        self.row.editing.connect(lambda on: self.on_editing(self, on))

    def detach(self) -> None:
        super().detach()
        self.row = None
        self.axis_btns = []
        self.ref_label = None

    def _axis_index(self) -> tuple[int, int]:
        """(axis, sign) of the current direction when it lies along X, Y or Z; else (-1, 0)."""
        if self.value is None:
            return -1, 0
        return axis_of((self.value.X(), self.value.Y(), self.value.Z()))

    def refresh_display(self) -> None:
        if self.row is None:
            return
        direction = None if self.value is None else (self.value.X(), self.value.Y(), self.value.Z())
        self.row.set_state(direction, self.ref, self.flipped,
                           picking=self.pb is not None and self.pb.isChecked(), hint=self.pick_hint())

    def picked_element(self) -> bool:
        """True when the direction comes from a face or edge, not an axis button."""
        return is_element_ref(self.ref)

    def reset_ui(self) -> None:
        if self.pb is not None:
            self.pb.setChecked(False)
        self.refresh_display()

    def enter_picking_ui(self) -> None:
        if self.pb is not None:
            self.pb.setChecked(True)
        self.refresh_display()

    def set_axis(self, vec: tuple) -> None:
        """Point along an axis, such as (0, 0, -1)."""
        self.value = gp_Dir(*vec)
        self.ref = axis_name(vec).replace("\u2212", "-")
        self.flipped = False
        self.pnt = self.center_provider()
        self.indicator.show(self.pnt, App.Vector(*vec))
        self.refresh_display()
        self.on_changed()

    def apply_selection(self) -> bool:
        try:
            picked = first_selected_subelement()
            if picked is None:
                return False
            sub_obj, sub_name = picked

            result = compute_direction(sub_obj)
            if result is None:
                App.Console.PrintError("Selected element must be a Face or an Edge.\n")
                return False

            pnt, dir_vec = result

            self.pnt = pnt
            self.value = gp_Dir(dir_vec.x, dir_vec.y, dir_vec.z)
            self.ref = sub_name
            self.flipped = False

            self.indicator.show(pnt, dir_vec)
            self.refresh_display()
            self.on_changed()
            return True

        except Exception as e:
            App.Console.PrintError(f"Visualizing {self.label.lower()} failed: {e}\n")
            return False

    def flip(self) -> None:
        if not self.value or not self.pnt:
            return
        self.value.Reverse()
        self.flipped = not self.flipped
        dx, dy, dz = self.value.X(), self.value.Y(), self.value.Z()
        self.indicator.show(self.pnt, App.Vector(dx, dy, dz))
        self.refresh_display()
        self.on_changed()

    def remove_indicator(self) -> None:
        self.indicator.remove()


# =============================================================================

# A plus: add a side action.
_ADD_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>'
)

_SIDE_ACTION_HELP = (
    "Directions the mold also opens in, besides the main pull: a slide, lifter, "
    "pickout or hand-loaded core. A face the main pull can't release but a side "
    "action can is drafted along that side action's direction, and isn't an undercut."
)


class SideActionDirection(VectorRequirement):
    """One side action: a direction, like the pull direction, numbered in the list."""

    def __init__(self, number: int):
        self._number = number
        super().__init__(ProcessRequirement.SIDE_ACTIONS)

    @property
    def label(self) -> str:
        return f"Side action {self._number}"

    def build_row(self, layout: QtWidgets.QLayout, on_pick, on_remove=None) -> None:
        """A numbered row with a remove button, inside the side actions block."""
        self.row = DirectionRow(self.label, removable=True)
        self._connect_row_signals()
        self.row.axis_clicked.connect(self.set_axis)
        self.row.pick_clicked.connect(lambda: on_pick(self))
        self.row.flip_clicked.connect(self.flip)
        if on_remove is not None:
            self.row.remove_clicked.connect(lambda: on_remove(self))
        self.pb = self.row.pick_btn
        self.flip_btn = self.row.flip_btn
        layout.addWidget(self.row)
        self.refresh_display()

    def renumber(self, number: int) -> None:
        """Change the number, and the arrow's label with it."""
        if number == self._number:
            return
        self._number = number
        shown = self.indicator.base_pnt is not None if hasattr(self.indicator, "base_pnt") else False
        self.indicator.remove()
        self.indicator = DirectionIndicator(self._COLORS[self.requirement], self.label)
        if shown and self.value is not None and self.pnt is not None:
            self.indicator.show(self.pnt, App.Vector(self.value.X(), self.value.Y(), self.value.Z()))


class SideActionsRequirement(RequirementHandler):
    """The list of side actions: zero or more extra directions the mold opens in.

    Each one is a SideActionDirection with its own row and arrow. Its value
    is the list of their directions, which may be empty.
    """

    MAX = 6

    def __init__(self, requirement: ProcessRequirement):
        self.items: list[SideActionDirection] = []
        super().__init__(requirement)
        self.active: Optional[SideActionDirection] = None
        # Set by the panel.
        self.bounds_provider = lambda: None
        self.pull_provider = lambda: None
        self.on_changed = lambda: None
        self.on_structure_changed = lambda: None
        self.on_hover = lambda _handler, _on: None
        self.on_editing = lambda _handler, _on: None
        self._add_btn: Optional[QtWidgets.QPushButton] = None
        self.enabled = False  # the "Side actions" box

    @property
    def value(self):
        if not self.enabled:
            return []
        return [item.value for item in self.items if item.value is not None]

    @value.setter
    def value(self, _ignored) -> None:
        pass  # the list follows its items

    def is_satisfied(self) -> bool:
        return True  # none is fine

    def clear(self) -> None:
        for item in list(self.items):
            item.remove_indicator()
        self.items = []
        self.active = None

    # -- Rows --------------------------------------------------------------

    def build_row(self, layout: QtWidgets.QLayout, on_pick) -> None:
        """A checkbox, then (when ticked) the side actions in an indented block."""
        box = QtWidgets.QCheckBox(self.label)
        box.setChecked(self.enabled)
        box.setToolTip(_SIDE_ACTION_HELP)
        box.setMinimumHeight(28)
        box.toggled.connect(self.set_enabled)
        layout.addWidget(box)
        if not self.enabled:
            return

        # Indented, with a line down the left: these belong together.
        holder = QtWidgets.QWidget()
        outer = QtWidgets.QHBoxLayout(holder)
        outer.setContentsMargins(4, 0, 0, 0)
        outer.setSpacing(0)
        block = QtWidgets.QFrame()
        block.setObjectName("dfmSideActions")
        block.setStyleSheet(
            "QFrame#dfmSideActions { border: none; border-left: 2px solid rgba(127, 127, 127, 110); }")
        rows = QtWidgets.QVBoxLayout(block)
        rows.setContentsMargins(8, 0, 0, 0)
        rows.setSpacing(2)
        outer.addWidget(block)

        for item in self.items:
            item.build_row(rows, lambda _h, it=item: self._pick(it, on_pick), self.remove)
            if item.value is not None and item.pnt is not None:
                v = item.value
                item.indicator.show(item.pnt, App.Vector(v.X(), v.Y(), v.Z()))

        add_row = QtWidgets.QHBoxLayout()
        add_row.setContentsMargins(0, 2, 0, 0)
        self._add_btn = QtWidgets.QPushButton("Add side action")
        self._add_btn.setStyleSheet(FLAT_BUTTON_CSS.replace("padding: 0px", "padding: 3px 6px"))
        self._add_btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._add_btn.setToolTip("Add a slide, lifter or pickout direction")
        apply_svg_icon(self._add_btn, _ADD_SVG, 14)
        self._add_btn.setEnabled(len(self.items) < self.MAX)
        self._add_btn.clicked.connect(lambda _c=False: self.add())
        add_row.addWidget(self._add_btn)
        add_row.addStretch(1)
        rows.addLayout(add_row)
        layout.addWidget(holder)

    def set_enabled(self, on: bool) -> None:
        """Tick or untick the box. Side actions are kept, just not used, while unticked."""
        if on == self.enabled:
            return
        self.enabled = on
        if on and not self.items:
            self.add()  # one to start with; add() rebuilds the rows
            return
        if not on:
            self.remove_indicator()
        self.on_structure_changed()
        self.on_changed()

    def _pick(self, item: SideActionDirection, on_pick) -> None:
        self.active = item
        on_pick(self)

    def detach(self) -> None:
        super().detach()
        self._add_btn = None
        for item in self.items:
            item.detach()

    def refresh_display(self) -> None:
        for item in self.items:
            item.refresh_display()

    def reset_ui(self) -> None:
        for item in self.items:
            item.reset_ui()

    def enter_picking_ui(self) -> None:
        if self.active is not None:
            self.active.enter_picking_ui()

    def apply_selection(self) -> bool:
        return self.active.apply_selection() if self.active is not None else False

    def remove_indicator(self) -> None:
        for item in self.items:
            item.remove_indicator()

    # -- Adding and removing -------------------------------------------------

    def anchor_for(self, item: SideActionDirection):
        """Where a side action's arrow sits: on the side of the part it comes from."""
        bb = self.bounds_provider()
        if bb is None:
            return App.Vector(0, 0, 0)
        center = bb.Center
        if item.value is None:
            return center
        d = item.value
        reach = (abs(d.X()) * bb.XLength + abs(d.Y()) * bb.YLength + abs(d.Z()) * bb.ZLength) / 2
        return App.Vector(center.x + d.X() * reach, center.y + d.Y() * reach, center.z + d.Z() * reach)

    def _free_axis(self) -> tuple:
        """The axis direction across the pull that no side action uses yet."""
        pull = self.pull_provider()
        used = {(round(i.value.X()), round(i.value.Y()), round(i.value.Z()))
                for i in self.items if i.value is not None}
        axes = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
        if pull is not None:
            axes.sort(key=lambda a: abs(a[0] * pull.X() + a[1] * pull.Y() + a[2] * pull.Z()))
        for axis in axes:
            if axis not in used:
                return axis
        return axes[0]

    def add(self) -> None:
        if len(self.items) >= self.MAX:
            return
        item = SideActionDirection(len(self.items) + 1)
        item.on_changed = self.on_changed
        item.on_hover = lambda h, on: self.on_hover(h, on)
        item.on_editing = lambda h, on: self.on_editing(h, on)
        item.center_provider = lambda it=item: self.anchor_for(it)
        axis = self._free_axis()
        item.value = gp_Dir(*axis)
        name = {(1, 0, 0): "X", (0, 1, 0): "Y", (0, 0, 1): "Z"}
        sign = "+" if sum(axis) > 0 else "-"
        item.ref = sign + name[tuple(abs(c) for c in axis)]
        item.pnt = self.anchor_for(item)
        if self.bounds_provider() is not None:
            item.indicator.show(item.pnt, App.Vector(*axis))
        self.items.append(item)
        self.on_structure_changed()
        self.on_changed()

    def remove(self, item: SideActionDirection) -> None:
        if item not in self.items:
            return
        item.remove_indicator()
        self.items.remove(item)
        if self.active is item:
            self.active = None
        for number, other in enumerate(self.items, start=1):
            other.renumber(number)
        self.on_structure_changed()
        self.on_changed()

    def move_arrows(self) -> None:
        """Put the arrows back on the sides of the part (after the model changes)."""
        if not self.enabled:
            return
        for item in self.items:
            if item.value is None or item.picked_element():
                continue
            item.pnt = self.anchor_for(item)
            v = item.value
            item.indicator.show(item.pnt, App.Vector(v.X(), v.Y(), v.Z()))


# =============================================================================


class PlaneRequirement(RequirementHandler):
    """A reference plane picked from a face."""

    def pick_hint(self) -> str:
        return "Click a face"

    def apply_selection(self) -> bool:
        try:
            sel = Gui.Selection.getSelectionEx()
            if not sel or not sel[0].SubElementNames:
                return False

            face_name = sel[0].SubElementNames[0]
            face_obj = sel[0].SubObjects[0]

            if not isinstance(face_obj, Part.Face):
                App.Console.PrintError(f"Selected element '{face_name}' is not a face.\n")
                return False

            self.value = freecad_to_ocp(face_obj)
            self.ref = face_name
            self.flipped = False

            if self.le is not None:
                self.le.setText(face_name)
            return True

        except Exception as e:
            App.Console.PrintError(f"Selection error: {e}\n")
            return False


# =============================================================================


_NUMERIC_DEFAULTS: dict[ProcessRequirement, tuple[float, float, float, float, str, str]] = {
    #                                          default, min, max,  step, unit, suffix
    ProcessRequirement.NOZZLE_DIAMETER: (0.4, 0.1, 2.0, 0.05, "mm", "Nozzle diameter"),
    ProcessRequirement.LAYER_HEIGHT: (0.2, 0.01, 1.0, 0.05, "mm", "Layer height"),
}


class NumericRequirement(RequirementHandler):
    """A numeric value entered via a spinbox (no 3D picking needed)."""

    def __init__(self, requirement: ProcessRequirement):
        super().__init__(requirement)
        defaults = _NUMERIC_DEFAULTS.get(requirement)
        if defaults:
            self.default_val, self.min_val, self.max_val, self.step, self.unit, self._label = defaults
        else:
            self.default_val, self.min_val, self.max_val, self.step, self.unit, self._label = (
                1.0, 0.0, 100.0, 0.1, "", requirement.name.replace("_", " ").capitalize(),
            )
        self.spin: Optional[QtWidgets.QDoubleSpinBox] = None
        # Set the default value immediately
        self.value = self.default_val

    @property
    def label(self) -> str:
        return self._label

    def make_spin(self) -> QtWidgets.QDoubleSpinBox:
        """The value's spin box (also kept as ``self.spin``)."""
        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setMinimumHeight(26)
        self.spin.setRange(self.min_val, self.max_val)
        self.spin.setSingleStep(self.step)
        self.spin.setDecimals(2)
        self.spin.setSuffix(f" {self.unit}")
        self.spin.setValue(self.value if self.value is not None else self.default_val)
        self.spin.setMinimumWidth(90)
        self.spin.valueChanged.connect(self._on_value_changed)
        return self.spin

    def build_row(self, layout: QtWidgets.QLayout, on_pick) -> None:
        """Create a label + spinbox row. No pick button needed."""
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        lbl = QtWidgets.QLabel(self._label)
        lbl.setMinimumHeight(26)
        row.addWidget(lbl, 1)
        row.addWidget(self.make_spin())
        layout.addLayout(row)

    def _on_value_changed(self, val: float):
        self.value = val

    def detach(self) -> None:
        self.spin = None

    def apply_selection(self) -> bool:
        # No 3D selection needed; value comes from the spinbox
        return True

    def is_satisfied(self) -> bool:
        return self.value is not None

    def clear(self) -> None:
        self.value = self.default_val
        if self.spin:
            self.spin.setValue(self.default_val)

    def refresh_display(self) -> None:
        pass

    def reset_ui(self) -> None:
        pass

    def enter_picking_ui(self) -> None:
        pass


# =============================================================================

# A ruler: measure the model now.
_MEASURE_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    b'<path d="M3 17L17 3l4 4L7 21z"/><path d="M7.5 12.5l2 2M10.5 9.5l2 2M13.5 6.5l2 2"/></svg>'
)

_NOMINAL_HELP = (
    "The part's main wall thickness. Boss walls, ribs, root radii and spacings are "
    "judged as fractions or multiples of it.\n\n"
    "Auto measures it from the model when the analysis runs (click the ruler to "
    "measure it now). Manual uses the value you type."
)

#: Mode and typed value per model, for this FreeCAD session.
_NOMINAL_MEMORY: dict[str, dict] = {}


def _object_key(obj) -> str:
    try:
        return f"{obj.Document.Name}:{obj.Name}"
    except Exception:
        return str(id(obj))


class NominalWallRequirement(RequirementHandler):
    """The nominal wall: measured from the model (Auto) or typed in (Manual).

    ``value`` is the plain dict the analysis reads (core/utils/nominal_wall.py):
    {"mode": "auto"} or {"mode": "manual", "value": mm}.
    """

    MIN, MAX, STEP, DEFAULT = 0.1, 20.0, 0.1, 2.0

    def __init__(self, requirement: ProcessRequirement):
        super().__init__(requirement)
        self.value = make_nominal_requirement(NOMINAL_AUTO)
        #: Measured on the current model (Auto), once known; None before.
        self.measured: Optional[float] = None
        #: The last measurement found no clear wall.
        self.measure_failed = False
        self._target = None
        self.switch: Optional[ViewSwitch] = None
        self.spin: Optional[QtWidgets.QDoubleSpinBox] = None
        self.measure_btn: Optional[QtWidgets.QPushButton] = None
        # Set by the panel.
        self.on_changed = lambda: None

    @property
    def manual(self) -> bool:
        return self.value.get("mode") == NOMINAL_MANUAL

    # -- Row ---------------------------------------------------------------------

    def build_row(self, layout: QtWidgets.QLayout, on_pick) -> None:
        """Nominal wall   [Auto|Manual]  [2.48 mm]  [ruler]"""
        holder = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        label = QtWidgets.QLabel(self.label)
        label.setMinimumHeight(28)
        label.setToolTip(_NOMINAL_HELP)
        row.addWidget(label)
        row.addStretch(1)

        self.switch = ViewSwitch("Auto", "Manual")
        self.switch.setToolTip(_NOMINAL_HELP)
        self.switch.setMinimumWidth(110)
        self.switch.setChecked(self.manual)
        self.switch.toggled.connect(self.set_manual)
        row.addWidget(self.switch)

        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setMinimumHeight(26)
        self.spin.setMinimumWidth(116)
        self.spin.setDecimals(2)
        self.spin.setSingleStep(self.STEP)
        self.spin.setSuffix(" mm")
        self.spin.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        self.spin.setKeyboardTracking(False)
        self.spin.valueChanged.connect(self._on_spin_changed)
        label.setBuddy(self.spin)
        row.addWidget(self.spin)

        self.measure_btn = icon_button(_MEASURE_SVG, "Measure the nominal wall of the model now")
        policy = self.measure_btn.sizePolicy()
        policy.setRetainSizeWhenHidden(True)  # the row doesn't shift when it hides
        self.measure_btn.setSizePolicy(policy)
        self.measure_btn.clicked.connect(lambda _c=False: self.measure_now())
        row.addWidget(self.measure_btn)

        layout.addWidget(holder)
        self.refresh_display()

    def detach(self) -> None:
        super().detach()
        self.switch = None
        self.spin = None
        self.measure_btn = None

    def refresh_display(self) -> None:
        """Show the mode, and the typed value (Manual) or what was measured (Auto)."""
        spin, name = self.spin, self._target_name()
        if spin is None:
            return
        spin.blockSignals(True)
        if self.switch is not None:
            self.switch.setChecked(self.manual)
        if self.manual:
            spin.setReadOnly(False)
            spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.UpDownArrows)
            spin.setSpecialValueText("")
            spin.setRange(self.MIN, self.MAX)
            spin.setValue(float(self.value["value"]))
            spin.setToolTip("The part's main wall thickness, used by every feature rule")
        else:
            spin.setReadOnly(True)
            spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
            spin.setRange(0.0, self.MAX)
            if self.measured is not None:
                spin.setSpecialValueText("")
                spin.setValue(self.measured)
                spin.setToolTip(f"Measured on {name}. Switch to Manual to change it.")
            else:
                spin.setSpecialValueText("not found" if self.measure_failed else "measured at run")
                spin.setValue(0.0)
                spin.setToolTip(
                    f"No clear nominal wall was found on {name}. Switch to Manual and type "
                    "the part's main wall thickness." if self.measure_failed else
                    "Measured from the model when the analysis runs. Click the ruler to "
                    "measure it now.")
        spin.blockSignals(False)
        if self.measure_btn is not None:
            self.measure_btn.setVisible(not self.manual)
            self.measure_btn.setEnabled(self._target is not None)

    def reset_ui(self) -> None:
        pass

    def enter_picking_ui(self) -> None:
        pass

    def apply_selection(self) -> bool:
        return False  # nothing to pick in the 3D view

    # -- State ---------------------------------------------------------------------

    def is_satisfied(self) -> bool:
        """Auto always is; Manual needs a value above 0."""
        if not self.manual:
            return True
        return parse_nominal_requirement(self.value)[0] == NOMINAL_MANUAL

    def clear(self) -> None:
        self.value = make_nominal_requirement(NOMINAL_AUTO)
        self.refresh_display()

    def set_manual(self, manual: bool) -> None:
        """Switch mode. Manual starts from the measured value, if there is one."""
        if manual == self.manual:
            return
        if manual:
            typed = self._remembered().get("manual")
            start = self.measured or typed or self.DEFAULT
            self.value = make_nominal_requirement(NOMINAL_MANUAL, round(start, 2))
        else:
            self.value = make_nominal_requirement(NOMINAL_AUTO)
        self._remember()
        self.refresh_display()
        self.on_changed()

    def _on_spin_changed(self, mm: float) -> None:
        if not self.manual:
            return
        self.value = make_nominal_requirement(NOMINAL_MANUAL, mm)
        self._remember()
        self.on_changed()

    def target_changed(self, obj) -> None:
        """A new model: forget what was measured; bring back its mode and typed
        value from earlier in the session, else keep a typed value as it is."""
        if obj is self._target:
            return
        self._target = obj
        self.measured = None
        self.measure_failed = False
        remembered = self._remembered()
        if remembered:
            self.value = dict(remembered["value"])
        self.refresh_display()

    def _remembered(self) -> dict:
        return _NOMINAL_MEMORY.get(_object_key(self._target), {}) if self._target else {}

    def _remember(self) -> None:
        if self._target is None:
            return
        entry = _NOMINAL_MEMORY.setdefault(_object_key(self._target), {})
        entry["value"] = dict(self.value)
        if self.manual:
            entry["manual"] = self.value["value"]

    def _target_name(self) -> str:
        try:
            return self._target.Label if self._target is not None else "the model"
        except Exception:
            return "the model"

    # -- Measuring now -------------------------------------------------------------

    def measure_now(self) -> Optional[float]:
        """Recognise the model and measure its nominal wall (Auto).

        Uses the workbench's recognition cache, so the analysis that follows
        doesn't recognise the model again.
        """
        if self._target is None:
            return None
        from ..app.recognition_service import features_for_object
        from ..core.utils.geometry import FaceIndex
        from ..core.utils.nominal_wall import measure_nominal_wall

        if self.spin is not None:
            self.spin.setSpecialValueText("measuring…")
            self.spin.setValue(0.0)
        app = QtWidgets.QApplication.instance()

        def pump(_stage: str = "", _fraction: float = 0.0) -> None:
            if app is not None:
                app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        pump()
        try:
            shape_occ, result = features_for_object(self._target, progress=pump)
            measured = measure_nominal_wall(shape_occ, FaceIndex(shape_occ), result)
        except Exception as exc:
            App.Console.PrintWarning(f"DFM: could not measure the nominal wall: {exc}\n")
            measured = None
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.measured = measured
        self.measure_failed = measured is None
        self.refresh_display()
        return measured


# =============================================================================

REQUIREMENT_HANDLER_TYPES: dict[ProcessRequirement, type[RequirementHandler]] = {
    ProcessRequirement.PULL_DIRECTION: VectorRequirement,
    ProcessRequirement.SIDE_ACTIONS: SideActionsRequirement,
    ProcessRequirement.PRINT_ORIENTATION: VectorRequirement,
    ProcessRequirement.NEUTRAL_PLANE: PlaneRequirement,
    # After the directions: what the part is, then the machine's values.
    ProcessRequirement.NOMINAL_WALL_THICKNESS: NominalWallRequirement,
    ProcessRequirement.NOZZLE_DIAMETER: NumericRequirement,
    ProcessRequirement.LAYER_HEIGHT: NumericRequirement,
}


# =============================================================================


class TaskSetup:
    def __init__(
        self,
        preset_process: Optional[str] = None,
        preset_material: Optional[str] = None,
        auto_run: bool = False,
    ):
        self.form = Gui.PySideUic.loadUi(":/ui/task_setup.ui")  # type: ignore
        if not self.form:
            raise RuntimeError("Failed to load task_setup.ui from resources.")
        self.form.setWindowTitle("DFM Analysis")
        self.form.setWindowIcon(QtGui.QIcon(":/icons/dfm_analysis.svg"))

        # Start the worker processes now, in the background, so they are
        # ready by the time the analysis runs (see app/parallel).
        try:
            from ..app.parallel.pool import prewarm

            prewarm()
        except Exception:
            pass

        self.registry = ProcessRegistry.get_instance()

        self.target_object = None
        self.target_shape = None
        self.process = None

        self.indicator = DirectionIndicator()

        self.handlers: dict[ProcessRequirement, RequirementHandler] = {
            req: cls(req) for req, cls in REQUIREMENT_HANDLER_TYPES.items()
        }
        # Direction arrows in the 3D view flip when clicked.
        self._flipper = ArrowFlipper()
        for handler in self.handlers.values():
            if isinstance(handler, VectorRequirement):
                handler.center_provider = self._model_center
                handler.on_changed = self._direction_changed
        sides = self.handlers[ProcessRequirement.SIDE_ACTIONS]
        sides.bounds_provider = lambda: self.target_shape.BoundBox if self.target_shape else None
        sides.pull_provider = lambda: self.handlers[ProcessRequirement.PULL_DIRECTION].value
        sides.on_changed = self._direction_changed
        sides.on_structure_changed = self._side_actions_changed
        self.handlers[ProcessRequirement.NOMINAL_WALL_THICKNESS].on_changed = \
            self._update_run_button_state
        # Which direction's row or arrow is hovered, and which is being edited.
        self._hovered_dir = None
        self._arrow_hovered_dir = None
        self._editing_dir = None
        for handler in self.handlers.values():
            if isinstance(handler, (VectorRequirement, SideActionsRequirement)):
                handler.on_hover = self._row_hovered
                handler.on_editing = self._row_editing
        self._flipper.on_hover = self._arrow_hovered

        self.is_running = False
        self.abort_requested = False
        self.picking_mode: Any = None
        self._cursor = CrossCursorOverride()
        self._preview: Optional[PullPreview] = None


        Gui.Selection.addObserver(self)

        self._escape_filter = EscapeFilter(self._on_escape)
        self.form.installEventFilter(self._escape_filter)

        self._setup_widgets()
        self._populate_categories()
        self._connect_signals()
        self._auto_select_model()
        self._update_run_button_state()

        if preset_process:
            self._apply_presets(preset_process, preset_material, auto_run)

    def _setup_widgets(self):
        f = self.form
        f.gbTarget.setTitle("Model")
        f.gbParams.setTitle("Manufacturing")
        f.gbOptions.setTitle("Settings")
        f.gbAnalysis.setTitle("Analysis")

        # Model: the name, then an icon button to pick it in the 3D view.
        target_layout = f.gbTarget.layout()
        target_layout.removeWidget(f.pbSelectModel)
        target_layout.removeWidget(f.leSelectModel)
        for i in range(target_layout.count()):
            target_layout.setStretch(i, 0)
        target_layout.addWidget(f.leSelectModel, 1)
        target_layout.addWidget(f.pbSelectModel, 0)
        f.leSelectModel.setReadOnly(True)
        f.leSelectModel.setPlaceholderText("No model selected")
        f.pbSelectModel.setText("")
        f.pbSelectModel.setCheckable(True)
        f.pbSelectModel.setFixedSize(28, 26)
        f.pbSelectModel.setToolTip("Pick the model in the 3D view")
        f.pbSelectModel.setStyleSheet(_ICON_BUTTON_CSS)
        apply_svg_icon(f.pbSelectModel, PICK_SVG, 16)

        # Manufacturing: one process list grouped by category, then material.
        f.cbManCategory.hide()
        f.lSelectProcess.hide()
        grid = f.gbParams.layout()
        grid.removeWidget(f.cbManCategory)
        grid.removeWidget(f.lSelectProcess)
        f.lMProcess.setText("Process")
        f.lMaterial.setText("Material")
        f.cbManProcess.setPlaceholderText("Choose a process")
        f.cbMaterial.setPlaceholderText("Choose a process first")
        f.cbMaterial.setEnabled(False)
        row, col, _rs, _cs = grid.getItemPosition(grid.indexOf(f.cbManProcess))
        self.library_btn = QtWidgets.QPushButton()
        self.library_btn.setIcon(QtGui.QIcon(":/icons/process_library.svg"))
        self.library_btn.setFixedSize(28, 26)
        self.library_btn.setStyleSheet(_ICON_BUTTON_CSS)
        self.library_btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.library_btn.setToolTip("Open the Process Library to edit processes, materials and rules")
        self.library_btn.clicked.connect(self._open_process_library)
        grid.addWidget(self.library_btn, row, col + 1)
        # The material list reaches the right edge, under the library button.
        m_row, m_col, _rs, _cs = grid.getItemPosition(grid.indexOf(f.cbMaterial))
        grid.removeWidget(f.cbMaterial)
        grid.addWidget(f.cbMaterial, m_row, m_col, 1, 2)

        f.gbOptions.hide()
        f.gbAnalysis.hide()
        # The bar shows the current step in words; no separate log.
        f.lProgress.hide()
        f.progressBar.setValue(0)
        f.progressBar.setTextVisible(True)
        f.progressBar.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        f.progressBar.setToolTip("")

        if not f.gbOptions.layout():
            layout = QtWidgets.QVBoxLayout(f.gbOptions)
            f.gbOptions.setLayout(layout)

        # Run: full width, in the theme's own button style.
        form_layout = f.layout()
        index = form_layout.indexOf(f.pbRunAnalysis)
        form_layout.removeWidget(f.pbRunAnalysis)
        run_box = QtWidgets.QWidget()
        run_layout = QtWidgets.QVBoxLayout(run_box)
        run_layout.setContentsMargins(0, 6, 0, 0)
        run_layout.setSpacing(4)
        style_primary_button(f.pbRunAnalysis, "Run analysis")
        f.pbRunAnalysis.setMinimumWidth(0)
        f.pbRunAnalysis.setMaximumWidth(16777215)
        f.pbRunAnalysis.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                      QtWidgets.QSizePolicy.Policy.Fixed)
        run_layout.addWidget(f.pbRunAnalysis)
        form_layout.insertWidget(index, run_box)
        self.run_box = run_box

    def _populate_categories(self):
        """Fill the process list: every process, grouped by category with a divider between."""
        combo = self.form.cbManProcess
        combo.blockSignals(True)
        combo.clear()
        processes = []
        for category in self.registry.get_categories():
            members = self.registry.get_processes_for_category(category)
            if not members:
                continue
            # A divider between categories; the category itself shows in
            # each process's tooltip (headings can come back with more processes).
            if combo.count():
                combo.insertSeparator(combo.count())
            for process in members:
                combo.addItem(process.name, userData=process.name)
                combo.setItemData(combo.count() - 1, category, QtCore.Qt.ItemDataRole.ToolTipRole)
                processes.append(process.name)
        combo.setCurrentIndex(-1)
        if len(processes) == 1:
            combo.setCurrentIndex(combo.findData(processes[0]))
        combo.setEnabled(bool(processes))
        if not processes:
            combo.setPlaceholderText("No processes in the Process Library")
        combo.blockSignals(False)
        self.on_process_changed()

    def _connect_signals(self):
        self.form.pbRunAnalysis.clicked.connect(self.on_run_clicked)
        self.form.pbAbort.clicked.connect(self.on_abort_clicked)
        self.form.pbSelectModel.clicked.connect(self.on_select_shape)
        self.form.cbManProcess.currentIndexChanged.connect(self.on_process_changed)
        self.form.cbMaterial.currentIndexChanged.connect(self._update_run_button_state)

    def _open_process_library(self, _checked: bool = False) -> None:
        try:
            Gui.runCommand("DFM_ProcessLibrary")
        except Exception as e:
            App.Console.PrintError(f"DFM: could not open the Process Library: {e}\n")

    def _model_center(self):
        if self.target_shape is not None:
            return self.target_shape.BoundBox.Center
        return App.Vector(0, 0, 0)

    def _direction_changed(self) -> None:
        self._register_arrows()
        self._update_run_button_state()

    def _register_arrows(self) -> None:
        """Make every shown direction arrow flippable from the 3D view."""
        for handler in self.handlers.values():
            if isinstance(handler, VectorRequirement) and handler.is_satisfied():
                self._flipper.add(handler.indicator, handler.flip)
            elif isinstance(handler, SideActionsRequirement) and handler.enabled:
                for item in handler.items:
                    if item.is_satisfied():
                        self._flipper.add(item.indicator, item.flip)

    # -- Matching rows and arrows -----------------------------------------

    def _directions(self) -> list:
        """Every direction shown in the panel: its rows and arrows."""
        out = []
        for req in (self._active_requirements() if self.process else []):
            handler = self.handlers[req]
            if isinstance(handler, VectorRequirement):
                out.append(handler)
            elif isinstance(handler, SideActionsRequirement) and handler.enabled:
                out.extend(handler.items)
        return out

    def _row_hovered(self, handler, on: bool) -> None:
        if on:
            self._hovered_dir = handler
        elif self._hovered_dir is handler:
            self._hovered_dir = None
        self._update_emphasis()

    def _row_editing(self, handler, on: bool) -> None:
        if on:
            self._editing_dir = handler
        elif self._editing_dir is handler:
            self._editing_dir = None
        self._update_emphasis()

    def _arrow_hovered(self, indicator) -> None:
        """An arrow in the 3D view was hovered (or left): tint its row."""
        self._arrow_hovered_dir = None
        for handler in self._directions():
            match = indicator is not None and handler.indicator is indicator
            if match:
                self._arrow_hovered_dir = handler
            if handler.row is not None:
                handler.row.set_highlighted(match)
        self._update_emphasis()

    def _update_emphasis(self) -> None:
        """Highlight the arrow being edited (dimming the rest), else the one hovered."""
        editing = self._editing_dir
        if editing is None and self.picking_mode in self.handlers:
            editing = self._direction_of(self.handlers[self.picking_mode])
        hovered = self._hovered_dir or self._arrow_hovered_dir
        for handler in self._directions():
            if editing is not None:
                state = "highlight" if handler is editing else "dim"
            else:
                state = "highlight" if handler is hovered else "normal"
            handler.indicator.set_emphasis(state)

    def _side_actions_changed(self) -> None:
        """A side action was added or removed: rebuild the rows and the clickable arrows."""
        self._flipper.detach()
        self._update_options_visibility()
        self._register_arrows()

    @staticmethod
    def _direction_of(handler) -> Optional["VectorRequirement"]:
        """The direction being picked for *handler*: itself, or the side action picked."""
        if isinstance(handler, VectorRequirement):
            return handler
        if isinstance(handler, SideActionsRequirement):
            return handler.active
        return None

    def _on_escape(self) -> bool:
        """Cancel picking mode on Escape. Returns True if handled."""
        if self.picking_mode:
            self._cleanup_preview()
            self._flipper.set_enabled(True)
            # Restore committed indicator if the handler already had a value
            if self.picking_mode in self.handlers:
                handler = self._direction_of(self.handlers[self.picking_mode])
                if handler is not None and handler.is_satisfied():
                    handler.indicator.set_visible(True)
            self.picking_mode = None
            self._reset_picking_ui()
            return True
        return False

    def addSelection(self, *args):
        if not self.picking_mode:
            return
        QtCore.QTimer.singleShot(50, self._process_picked_selection)

    def _ensure_preview_indicator(self):
        """Create the preview arrow once for the current picking session."""
        if self._preview is not None:
            return
        handler = self._direction_of(self.handlers.get(self.picking_mode))
        if handler is not None:
            color = VectorRequirement._COLORS.get(handler.requirement, PULL_DIRECTION_COLOR)
            self._preview = PullPreview(color)
            self._preview.begin()

    def setPreselection(self, doc_name, obj_name, sub_name, *_):
        """Show a live direction preview arrow when hovering over geometry during pick mode."""
        if not self.picking_mode or self.picking_mode == "model":
            return
        if self._direction_of(self.handlers.get(self.picking_mode)) is None:
            return
        if self._preview is not None:
            self._preview.show_for(doc_name, obj_name, sub_name)

    def removePreselection(self, *args):
        """Hide the direction preview when the mouse leaves geometry."""
        if self._preview is not None:
            self._preview.hide()

    def _cleanup_preview(self):
        """Remove the preview arrow from the scene graph."""
        if self._preview is not None:
            self._preview.end()
            self._preview = None

    def _process_picked_selection(self):
        if not self.picking_mode:
            return

        if self.picking_mode == "model":
            success = self._apply_model_selection()
            picked_handler = None
        else:
            picked_handler = self.handlers.get(self.picking_mode)
            success = picked_handler.apply_selection() if picked_handler else False

        if success:
            self._cleanup_preview()
            # Restore committed indicator visibility (was hidden while picking)
            picked = self._direction_of(picked_handler)
            if picked is not None:
                picked.indicator.set_visible(True)
            self.picking_mode = None
            self._reset_picking_ui()
            Gui.Selection.clearSelection()
            self._update_run_button_state()

    def _reset_picking_ui(self):
        """Reset the active states of the model button and all requirement buttons."""
        self.form.pbSelectModel.setChecked(False)
        self.form.leSelectModel.setText(self.target_object.Label if self.target_object else "")
        self.form.leSelectModel.setPlaceholderText("No model selected")

        for handler in self.handlers.values():
            handler.reset_ui()

        self._cursor.pop()
        self._flipper.set_enabled(True)
        self._editing_dir = None
        if self.picking_mode not in self.handlers:
            self._update_emphasis()

    def _toggle_pick_mode_model(self):
        if self.picking_mode == "model":
            self.picking_mode = None
            self._reset_picking_ui()
            return

        self._reset_picking_ui()
        self.picking_mode = "model"
        self.form.pbSelectModel.setChecked(True)
        self.form.leSelectModel.setText("")
        self.form.leSelectModel.setPlaceholderText("Click a model in the 3D view")
        self._flipper.set_enabled(False)
        self._cursor.push()

    def on_select_shape(self):
        if self._apply_model_selection():
            Gui.Selection.clearSelection()
            self.form.pbSelectModel.setChecked(False)
            self._update_run_button_state()
        else:
            self._toggle_pick_mode_model()

    def _apply_model_selection(self) -> bool:
        sel = Gui.Selection.getSelection()
        return self._set_target(sel[0]) if sel else False

    def _on_requirement_pick(self, handler: RequirementHandler):
        """Pick-button handler shared by every dynamic requirement row."""
        # A second click on the active button cancels picking.
        if self.picking_mode == handler.requirement:
            self._cleanup_preview()
            # Restore the committed indicator if the handler already had a value
            direction = self._direction_of(handler)
            if direction is not None and direction.is_satisfied():
                direction.indicator.set_visible(True)
            self.picking_mode = None
            self._reset_picking_ui()
            return

        if handler.apply_selection():
            Gui.Selection.clearSelection()
            handler.reset_ui()
            self._update_run_button_state()
            return

        self._reset_picking_ui()
        self.picking_mode = handler.requirement
        handler.enter_picking_ui()
        self._update_emphasis()
        self._flipper.set_enabled(False)
        # Hide the committed indicator while picking a new direction
        direction = self._direction_of(handler)
        if direction is not None:
            if direction.is_satisfied():
                direction.indicator.set_visible(False)
            self._ensure_preview_indicator()
        self._cursor.push()

    def _set_target(self, obj) -> bool:
        """Make ``obj`` the analysis target. Returns False if it can't be used."""
        try:
            shape = obj.Shape
        except Exception as e:
            App.Console.PrintError(f"Could not use the selected object. {e}\n")
            return False
        self.target_object = obj
        self.target_shape = shape
        self.form.leSelectModel.setText(obj.Label)
        self.form.leSelectModel.setCursorPosition(0)
        # Arrows set along an axis sit at the model's center: move them along.
        active = self._active_requirements() if self.process else []
        for req in active:
            handler = self.handlers[req]
            if isinstance(handler, VectorRequirement) and handler.is_satisfied() \
                    and handler._axis_index()[0] >= 0:
                handler.pnt = self._model_center()
                v = handler.value
                handler.indicator.show(handler.pnt, App.Vector(v.X(), v.Y(), v.Z()))
        self.handlers[ProcessRequirement.SIDE_ACTIONS].move_arrows()
        self.handlers[ProcessRequirement.NOMINAL_WALL_THICKNESS].target_changed(obj)
        self._register_arrows()
        return True

    def _auto_select_model(self):
        auto_select_body(self._set_target)

    def _remove_all_indicators(self):
        self._flipper.detach()
        for handler in self.handlers.values():
            handler.remove_indicator()

    def on_category_changed(self):
        selected_category = self.form.cbManCategory.currentText()

        self.form.cbManProcess.blockSignals(True)
        self.form.cbManProcess.clear()
        processes = self.registry.get_processes_for_category(selected_category)
        if processes:
            for process in processes:
                self.form.cbManProcess.addItem(process.name, userData=process.name)
            self.form.cbManProcess.setEnabled(True)
            self.form.cbManProcess.setPlaceholderText("Select a process")
        else:
            self.form.cbManProcess.setEnabled(False)
            self.form.cbManProcess.setPlaceholderText("No processes in this category")
        self.form.cbManProcess.setCurrentIndex(0 if len(processes) == 1 else -1)
        self.form.cbManProcess.blockSignals(False)

        self.on_process_changed()

    def on_process_changed(self):
        process_id = self.form.cbManProcess.currentData()

        self.form.cbMaterial.blockSignals(True)
        self.form.cbMaterial.clear()

        if not process_id:
            self.process = None
            self.form.cbMaterial.setEnabled(False)
            self.form.cbMaterial.setPlaceholderText("Choose a process first")
            self.form.cbMaterial.setCurrentIndex(-1)
            self.form.cbMaterial.blockSignals(False)
            self._update_options_visibility()
            self._update_run_button_state()
            return

        self.process = self.registry.get_process_by_id(process_id)
        materials = list(self.process.materials.keys())
        if materials:
            self.form.cbMaterial.addItems(materials)
            self.form.cbMaterial.setEnabled(True)
            self.form.cbMaterial.setPlaceholderText("Choose a material")
        else:
            self.form.cbMaterial.setEnabled(False)
            self.form.cbMaterial.setPlaceholderText("No materials defined")
        self.form.cbMaterial.setCurrentIndex(0 if len(materials) == 1 else -1)
        self.form.cbMaterial.blockSignals(False)

        self._update_options_visibility()
        self._update_run_button_state()

    def _clear_options_ui(self):
        """Recursively delete every widget from gbOptions and detach handlers."""
        layout = self.form.gbOptions.layout()

        def delete_items(layout_node):
            if layout_node is not None:
                while layout_node.count():
                    item = layout_node.takeAt(0)
                    widget = item.widget()
                    if widget is not None:
                        widget.hide()  # gone now, not only when the event loop next runs
                        widget.deleteLater()
                    else:
                        delete_items(item.layout())

        delete_items(layout)

        for handler in self.handlers.values():
            handler.detach()
        # The rows are gone, so nothing is hovered or edited any more.
        self._hovered_dir = self._arrow_hovered_dir = self._editing_dir = None

    def _active_requirements(self) -> list[ProcessRequirement]:
        """Active requirements that have a handler, in a fixed, stable order."""
        active = self.get_active_requirements()

        for req in active:
            if req != ProcessRequirement.NONE and req not in REQUIREMENT_HANDLER_TYPES:
                App.Console.PrintWarning(
                    f"DFM: no input handler for requirement '{req.name}'; it will be skipped.\n"
                )

        return [req for req in REQUIREMENT_HANDLER_TYPES if req in active]

    def _update_options_visibility(self):
        active_reqs = self._active_requirements()

        # Remove indicators for requirements that are no longer active
        for req, handler in self.handlers.items():
            if req not in active_reqs:
                handler.remove_indicator()

        self._clear_options_ui()
        if not self.process:
            self.form.gbOptions.hide()
            return

        if not active_reqs:
            self.form.gbOptions.hide()
            return

        layout = self.form.gbOptions.layout()
        numeric = [r for r in active_reqs if isinstance(self.handlers[r], NumericRequirement)]
        for req in active_reqs:
            if req in numeric and len(numeric) == 2:
                continue
            self.handlers[req].build_row(layout, self._on_requirement_pick)
        if len(numeric) == 2:
            # Nozzle and layer height (or any two values) share one row, as
            # two equal halves: each label sits right next to its value.
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(6)
            short = {ProcessRequirement.NOZZLE_DIAMETER: "Nozzle",
                     ProcessRequirement.LAYER_HEIGHT: "Layer"}
            for i, req in enumerate(numeric):
                handler = self.handlers[req]
                if i:
                    row.addSpacing(10)
                lbl = QtWidgets.QLabel(short.get(req, handler.label))
                lbl.setToolTip(handler.label)
                lbl.setMinimumHeight(26)
                spin = handler.make_spin()
                spin.setToolTip(handler.label)
                spin.setMinimumWidth(70)
                spin.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                   QtWidgets.QSizePolicy.Policy.Fixed)
                lbl.setBuddy(spin)
                row.addWidget(lbl)
                row.addWidget(spin, 1)
            layout.addLayout(row)

        self.form.gbOptions.setTitle("Settings")
        self.form.gbOptions.show()

        # Directions start along +Z, the most common pull and print direction.
        for req in active_reqs:
            handler = self.handlers[req]
            if isinstance(handler, VectorRequirement) and not handler.is_satisfied():
                self._set_default_direction(handler)

    def _set_default_direction(self, handler: "VectorRequirement"):
        """Default to +Z, with the arrow at the model's bounding box center."""
        handler.value = gp_Dir(0, 0, 1)
        handler.ref = "+Z"
        handler.flipped = False
        handler.pnt = self._model_center()
        if self.target_object and self.target_shape:
            handler.indicator.show(handler.pnt, App.Vector(0, 0, 1))
        handler.refresh_display()
        self._register_arrows()
        self._update_run_button_state()

    def get_active_requirements(self) -> set[ProcessRequirement]:
        """The inputs this process's active rules need (see app/process_requirements)."""
        return required_inputs(
            self.process, warn=lambda text: App.Console.PrintWarning(text + "\n"))

    def _missing_requirements(self) -> list[str]:
        missing = []
        if not self.target_shape:
            missing.append("target model")
        if not self.process:
            missing.append("manufacturing process")
        if self.form.cbMaterial.currentIndex() < 0:
            missing.append("material")

        if self.process:
            for req in self._active_requirements():
                if not self.handlers[req].is_satisfied():
                    missing.append(req.name.replace("_", " ").lower())
        return missing

    def _update_run_button_state(self):
        missing = self._missing_requirements()
        btn = self.form.pbRunAnalysis
        if missing:
            btn.setEnabled(False)
            btn.setToolTip("Missing: " + ", ".join(missing))
        else:
            btn.setEnabled(True)
            n = len(self.process.active_rules) if self.process else 0
            btn.setToolTip(f"Checks {n} rule{'s' if n != 1 else ''} for "
                           f"{self.form.cbMaterial.currentText()}")

    def _apply_presets(self, process_name: str, material_name: Optional[str], auto_run: bool):
        """Pre-fill the setup form with a known process/material and optionally auto-run."""
        index = self.form.cbManProcess.findData(process_name)
        if index >= 0:
            self.form.cbManProcess.setCurrentIndex(index)

        # Select the material
        if material_name:
            mat_idx = self.form.cbMaterial.findText(material_name)
            if mat_idx >= 0:
                self.form.cbMaterial.setCurrentIndex(mat_idx)

        self._update_run_button_state()

        if auto_run:
            # Defer so the dialog is shown before the analysis starts
            QtCore.QTimer.singleShot(200, self.on_run_clicked)

    def on_run_clicked(self):
        self.picking_mode = None
        self._reset_picking_ui()

        missing = self._missing_requirements()
        if missing:
            App.Console.PrintError(f"Cannot run analysis. Missing: {', '.join(missing)}\n")
            return
        self._start_analysis()

    def on_abort_clicked(self):
        """The old in-panel Abort button; the progress panel has its own now."""
        pass

    # -- Running ---------------------------------------------------------------

    def snapshot(self) -> dict:
        """The settings, to bring the panel back as it was (after an Abort)."""
        handlers = {}
        for req, handler in self.handlers.items():
            if isinstance(handler, SideActionsRequirement):
                handlers[req] = ("sides", handler.enabled,
                                 [(i.value, i.pnt, i.ref, i.flipped) for i in handler.items])
            else:
                handlers[req] = ("plain", {a: getattr(handler, a)
                                           for a in ("value", "pnt", "ref", "flipped")
                                           if hasattr(handler, a)})
        return {
            "object": self.target_object,
            "process": self.form.cbManProcess.currentData(),
            "material": self.form.cbMaterial.currentText(),
            "handlers": handlers,
        }

    def restore(self, state: dict) -> None:
        """Set the panel back to a snapshot()."""
        obj = state.get("object")
        if obj is not None:
            try:
                self._set_target(obj)
            except Exception:
                pass  # deleted meanwhile: keep the auto-selected one
        index = self.form.cbManProcess.findData(state.get("process"))
        if index >= 0:
            self.form.cbManProcess.setCurrentIndex(index)
        material = state.get("material")
        if material:
            mat = self.form.cbMaterial.findText(material)
            if mat >= 0:
                self.form.cbMaterial.setCurrentIndex(mat)

        for req, entry in state.get("handlers", {}).items():
            handler = self.handlers.get(req)
            if handler is None:
                continue
            if entry[0] == "sides" and isinstance(handler, SideActionsRequirement):
                _kind, enabled, items = entry
                handler.remove_indicator()
                handler.items = []
                for value, pnt, ref, flipped in items:
                    handler.add()
                    item = handler.items[-1]
                    item.value, item.pnt, item.ref, item.flipped = value, pnt, ref, flipped
                handler.enabled = enabled
            elif entry[0] == "plain":
                for attr, value in entry[1].items():
                    setattr(handler, attr, value)

        # Rows and arrows for the restored values.
        self._update_options_visibility()
        for req in self._active_requirements():
            handler = self.handlers[req]
            if isinstance(handler, VectorRequirement) and handler.is_satisfied() \
                    and handler.pnt is not None:
                v = handler.value
                handler.indicator.show(handler.pnt, App.Vector(v.X(), v.Y(), v.Z()))
                handler.refresh_display()
        self._register_arrows()
        self._update_run_button_state()

    def _start_analysis(self):
        """Hand over to the progress panel, which runs the analysis."""
        assert self.target_shape is not None
        assert self.target_object is not None
        assert self.process is not None
        from .task_progress import RunRequest, TaskProgress

        kwargs = {}
        for req in self._active_requirements():
            kwargs[req.name] = self.handlers[req].value
        request = RunRequest(
            target_object=self.target_object,
            shape=self.target_shape,
            process=self.process,
            process_name=self.form.cbManProcess.currentData(),
            material_name=self.form.cbMaterial.currentText(),
            kwargs=kwargs,
            setup_state=self.snapshot(),
        )
        self.form.pbRunAnalysis.setEnabled(False)

        def switch():
            self._cleanup_preview()
            self._reset_picking_ui()
            self._remove_all_indicators()
            try:
                Gui.Selection.removeObserver(self)
            except Exception:
                pass
            Gui.Control.closeDialog()
            Gui.Control.showDialog(TaskProgress(request))

        # After this click handler returns: closing the panel deletes its buttons.
        QtCore.QTimer.singleShot(0, switch)

    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.StandardButton.Close

    def reject(self):
        if self.is_running:
            self.abort_requested = True
        self._cleanup_preview()
        self._reset_picking_ui()
        self._remove_all_indicators()
        try:
            Gui.Selection.removeObserver(self)
        except Exception:
            pass
        Gui.Control.closeDialog()

    def accept(self):
        self._cleanup_preview()
        self._reset_picking_ui()
        self._remove_all_indicators()
        try:
            Gui.Selection.removeObserver(self)
        except Exception:
            pass
        Gui.Control.closeDialog()


# =============================================================================


def open_results(target_object, process, process_name: str, material_name: str,
                 results: list[CheckResult], runner: AnalysisRunner, run_kwargs: dict):
    """Show the results panel for a finished analysis (the task view must be free)."""
    _resolve_geometry_refs(results)
    history_manager = HistoryManager(Path(App.getUserAppDataDir()))

    # Build run_settings for persistence and material switching
    run_settings = dict(run_kwargs)
    # Remove non-serialisable OCP objects before storing
    run_settings.pop(ProcessRequirement.PULL_DIRECTION.name, None)
    run_settings.pop(ProcessRequirement.SIDE_ACTIONS.name, None)
    run_settings.pop(ProcessRequirement.PRINT_ORIENTATION.name, None)
    run_settings.pop(ProcessRequirement.NEUTRAL_PLANE.name, None)
    run_settings.pop("prefs", None)

    report_model = DFMReportModel(results=results, process=process, material=material_name)
    view_bridge = DFMViewProvider(target_object)
    results_view = TaskResults()
    try:
        return TaskResultsPresenter(
            view=results_view,
            model=report_model,
            bridge=view_bridge,
            history_manager=history_manager,
            doc_name=App.ActiveDocument.Name,  # type: ignore
            shape_name=target_object.Label,  # type: ignore
            runner=runner,
            run_settings=run_settings,
        )
    except Exception as e:
        App.Console.PrintError(f"Failed to open results panel: {e}\n")
        import traceback

        App.Console.PrintError(traceback.format_exc())
        return None


def _resolve_geometry_refs(results: list[CheckResult]) -> None:
    for result in results:
        resolved = []
        for kind, index in result.failing_geometry:
            resolved.append(GeometryRef(type=kind, index=index - 1, label=f"{kind}{index}"))
        result.refs = resolved
        result.failing_geometry = []


# =============================================================================


class DfmAnalysisCommand:
    def GetResources(self):
        return {
            "Pixmap": ":/icons/dfm_analysis.svg",
            "MenuText": "DFM Analysis",
            "ToolTip": "Opens the DFM Analysis task panel.",
        }

    def Activated(self):
        Gui.Control.showDialog(TaskSetup())

    def IsActive(self):
        return True


if App.GuiUp:
    Gui.addCommand("DFM_SetupAnalysis", DfmAnalysisCommand())
