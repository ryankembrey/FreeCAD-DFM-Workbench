# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The Symmetry Check task panel.

Model, then Settings:

* Symmetry: Mirror or Rotational.
* Mirror plane: Auto (best fit), YZ, XZ, XY, the midplane of two parallel
  faces, or a picked flat face or datum plane. The pick button takes a face
  or plane, or two faces for their midplane, from the selection or from
  clicks in the 3D view.
* Through (for Auto and the main planes): where the plane passes.
* Offset: moves the plane along its normal.
* Axis and Copies, for rotational symmetry.
* Tolerance.

The plane or axis previews in the 3D view as you set it. After a check:
the verdict, the differences (right-click to ignore one), and in the 3D
view each difference in amber with the info icon on it, a ghost where its
partner would be, and a key whose arrows step through them.
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6 import QtCore, QtWidgets

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ...app.contour.measures import Category
from ...core.inspection import geometry as G
from ...core.inspection import models as M
from ...core.inspection import symmetry as SY
from ...core.inspection.naming import fmt_mm, fmt_volume
from ..results.tabs.common import MutedLabel
from ..utils.body_select import auto_select_body
from ..utils.direction_row import PICK_SVG, icon_button
from . import document as D
from .panel_base import (InspectPanel, ModelRow, combo, datum_plane, grid, has_shape, ocp_shape,
                         point_and_normal, recognition_for, row_widget, selected_subelements, spin)
from .widgets import SYMMETRY_HEX, Group, hex_rgb

THROUGH = [
    (SY.BEST, "Best fit", "Where the part matches its mirror image best. The centre of mass is pulled "
                          "sideways by whatever isn’t symmetric, so this is usually the one you want."),
    (SY.CENTRE, "Centre of mass", "Through the part’s centre of mass."),
    (SY.BOX_CENTRE, "Bounding box centre", "Through the middle of the part’s bounding box."),
    (SY.ORIGIN, "Origin", "Through the document origin."),
]

AUTO, MIDPLANE, PICKED = "auto", "midplane", "pick"
PLANES = [
    (AUTO, "Auto (best fit)", "The plane the part matches its mirror image best: YZ, XZ, XY or one of "
                              "the part’s own main planes"),
    ("YZ", "YZ", "The YZ plane: the part’s left and right match"),
    ("XZ", "XZ", "The XZ plane: front and back match"),
    ("XY", "XY", "The XY plane: top and bottom match"),
    (None, "", ""),
    (MIDPLANE, "Midplane of two faces…", "Half way between two parallel flat faces you pick"),
    (PICKED, "Pick a face or plane…", "A flat face of the part, or a datum or origin plane"),
]
AXES = [
    (AUTO, "Auto (best fit)", "The axis the part repeats round best: X, Y, Z or one of the part’s own axes"),
    ("X", "X", "Round the X axis"),
    ("Y", "Y", "Round the Y axis"),
    ("Z", "Z", "Round the Z axis"),
    (None, "", ""),
    (PICKED, "Pick a round face or edge…", "The axis of a hole, a boss or a round edge"),
]
# What a pick option reads once something has been picked.
PICKED_TEXT = {MIDPLANE: "Midplane", PICKED: "Picked plane"}
PICKED_AXIS_TEXT = "Picked axis"

ONE_SIDE, MISALIGNED, SYMMETRIC = 1, 2, 0
STRONG_HEX = "#C77F0A"
GHOST_HEX = "#B57400"


def _fill(box: QtWidgets.QComboBox, options) -> None:
    for key, text, tip in options:
        if key is None:
            box.insertSeparator(box.count())
            continue
        box.addItem(text, key)
        box.setItemData(box.count() - 1, tip, QtCore.Qt.ItemDataRole.ToolTipRole)


def _set_key(box: QtWidgets.QComboBox, key: str) -> None:
    box.blockSignals(True)
    box.setCurrentIndex(max(box.findData(key), 0))
    box.blockSignals(False)


def _set_text(box: QtWidgets.QComboBox, key: str, text: str) -> None:
    i = box.findData(key)
    if i >= 0:
        box.setItemText(i, text)


class SymmetryPanel(InspectPanel):
    TOOL = D.SYMMETRY
    TITLE = "Symmetry Check"
    ICON = ":/icons/dfm_symmetry.svg"
    RUN_TEXT = "Check symmetry"
    LIST_TITLE = "Differences"
    LEGEND_TITLE = "Symmetry"
    MAP_TITLE = "Asymmetry"
    MAP_NO_VALUE = "Symmetric"
    MAP_COLORMAP = "Turbo"
    MAP_MODE_NAME = "Asymmetry map"

    def __init__(self, saved=None):
        self.target = None
        self._shape = None
        # The committed plane and axis choices (a pick option counts once something is picked).
        self._plane_key = AUTO
        self._axis_key = AUTO
        # (point, normal, description) of the picked plane, the midplane, and the picked axis.
        self._picked_plane: Optional[tuple] = None
        self._midplane: Optional[tuple] = None
        self._picked_axis: Optional[tuple] = None
        # The first face of a midplane being picked: (point, normal, name).
        self._first_face: Optional[tuple] = None
        super().__init__(saved)

    # -- inputs ---------------------------------------------------------------------------

    def build_inputs(self) -> None:
        self._pick_handlers = {"model": self._apply_model_from_selection,
                               PICKED: self._apply_plane_from_selection,
                               MIDPLANE: self._apply_midplane_from_selection,
                               "axis": self._apply_axis_from_selection}
        model_box = QtWidgets.QGroupBox("Model")
        lay = QtWidgets.QVBoxLayout(model_box)
        lay.setContentsMargins(8, 8, 8, 8)
        self.model_row = ModelRow("Pick the model in the 3D view, or select it before opening the tool")
        self.model_row.pick_clicked.connect(lambda: self.start_pick("model"))
        lay.addWidget(self.model_row)
        self.root.addWidget(model_box)

        box = QtWidgets.QGroupBox("Settings")
        g = grid(box)
        g.addWidget(QtWidgets.QLabel("Symmetry"), 0, 0)
        self.kind = combo()
        self.kind.addItem("Mirror", SY.MIRROR)
        self.kind.setItemData(0, "The part is the same either side of a plane", QtCore.Qt.ItemDataRole.ToolTipRole)
        self.kind.addItem("Rotational", SY.ROTATIONAL)
        self.kind.setItemData(1, "The part repeats a number of times round an axis, like a bolt circle",
                              QtCore.Qt.ItemDataRole.ToolTipRole)
        self.kind.currentIndexChanged.connect(self._on_kind)
        g.addWidget(self.kind, 0, 1)

        self.lbl_dir = QtWidgets.QLabel("Mirror plane")
        g.addWidget(self.lbl_dir, 1, 0)
        self.plane = combo("The plane the part should be symmetric about")
        _fill(self.plane, PLANES)
        self.plane.currentIndexChanged.connect(self._on_plane)
        self.pick_plane = icon_button(PICK_SVG, "Pick a flat face or a datum plane, or two parallel faces "
                                                "for the plane half way between them", checkable=True)
        self.pick_plane.clicked.connect(lambda _c=False: self._pick_plane_button())
        self.plane_row = row_widget(self.plane, self.pick_plane)
        g.addWidget(self.plane_row, 1, 1)
        self.axis = combo("The axis the part should repeat round")
        _fill(self.axis, AXES)
        self.axis.currentIndexChanged.connect(self._on_axis)
        self.pick_axis = icon_button(PICK_SVG, "Pick a round face or edge for the axis", checkable=True)
        self.pick_axis.clicked.connect(lambda _c=False: self.start_pick("axis"))
        self.axis_row = row_widget(self.axis, self.pick_axis)
        g.addWidget(self.axis_row, 1, 1)

        self.dir_note = MutedLabel("")
        self.dir_note.setWordWrap(True)
        g.addWidget(self.dir_note, 2, 1)

        self.lbl_through = QtWidgets.QLabel("Through")
        self.through = combo("Where the plane passes")
        _fill(self.through, THROUGH)
        self.through.currentIndexChanged.connect(self._on_setting)
        g.addWidget(self.lbl_through, 3, 0)
        g.addWidget(self.through, 3, 1)

        self.lbl_offset = QtWidgets.QLabel("Offset")
        self.offset = spin(0.0, lo=-1e5, hi=1e5, step=0.1)
        self.offset.setToolTip("Move the plane along its normal")
        self.offset.installEventFilter(self._enter_filter)
        self.offset.valueChanged.connect(self._on_setting)
        g.addWidget(self.lbl_offset, 4, 0)
        g.addWidget(self.offset, 4, 1)

        self.lbl_copies = QtWidgets.QLabel("Copies")
        self.copies = QtWidgets.QSpinBox()
        self.copies.setRange(2, 360)
        self.copies.setValue(6)
        self.copies.setToolTip("How many times the part repeats in one turn")
        self.copies.installEventFilter(self._enter_filter)
        self.copies.valueChanged.connect(self._on_setting)
        g.addWidget(self.lbl_copies, 5, 0)
        g.addWidget(self.copies, 5, 1)

        g.addWidget(QtWidgets.QLabel("Tolerance"), 6, 0)
        self.tolerance = spin(0.01, lo=0.0001, hi=10.0, step=0.01, decimals=3)
        self.tolerance.setToolTip("Differences smaller than this don’t count")
        self.tolerance.installEventFilter(self._enter_filter)
        self.tolerance.valueChanged.connect(self._on_setting)
        g.addWidget(self.tolerance, 6, 1)
        self.settings_box = box
        self.root.addWidget(box)
        self._sync_widgets()

    def display_checks(self):
        return [("show_plane", "Show mirror plane", "Show the mirror plane or the axis", True),
                ("show_ghosts", "Show where partners would be",
                 "Show a see-through ghost where each difference’s partner should be", False)]

    # -- model ------------------------------------------------------------------------------

    def auto_select(self) -> None:
        auto_select_body(self.set_model)

    def set_model(self, obj) -> bool:
        if not has_shape(obj):
            App.Console.PrintWarning("DFM Symmetry Check: pick a solid model.\n")
            return False
        if obj is not self.target and self.result is not None:
            self.clear()
        self.target = obj
        self._shape = None
        self.model_row.set_object(obj)
        self.preview()
        self.refresh_run_state()
        return True

    def _apply_model_from_selection(self) -> bool:
        sel = Gui.Selection.getSelection()
        return bool(sel) and self.set_model(sel[0])

    def shape(self):
        if self._shape is None and self.target is not None:
            self._shape = ocp_shape(self.target)
        return self._shape

    def ready(self) -> bool:
        return self.target is not None

    # -- settings --------------------------------------------------------------------------------

    def is_rotational(self) -> bool:
        return self.kind.currentData() == SY.ROTATIONAL

    rotational = is_rotational

    def _sync_widgets(self) -> None:
        """Show the rows that apply to the kind of symmetry and the plane chosen."""
        rot = self.is_rotational()
        self.lbl_dir.setText("Axis" if rot else "Mirror plane")
        self.plane_row.setVisible(not rot)
        self.axis_row.setVisible(rot)
        through = not rot and self._plane_key not in (MIDPLANE, PICKED)
        for w in (self.lbl_through, self.through):
            w.setVisible(through)
        for w in (self.lbl_offset, self.offset):
            w.setVisible(not rot)
        for w in (self.lbl_copies, self.copies):
            w.setVisible(rot)
        self.copies.setSuffix(f"  (every {360.0 / self.copies.value():g}°)")
        if hasattr(self, "show_plane"):
            self.show_plane.setText("Show axis" if rot else "Show mirror plane")
        self._update_note()

    def _update_note(self, text: Optional[str] = None) -> None:
        if text is None:
            text = self._note_for_choice()
        self.dir_note.setText(text)
        self.dir_note.setVisible(bool(text))

    def _note_for_choice(self) -> str:
        if self.is_rotational():
            if self._axis_key == PICKED and self._picked_axis:
                return f"Along {self._picked_axis[2]}"
            if self._axis_key == AUTO:
                return "Auto picks the best fit of the part’s main axes when you check."
            return ""
        if self._plane_key == MIDPLANE and self._midplane:
            return f"Between {self._midplane[2]}"
        if self._plane_key == PICKED and self._picked_plane:
            return f"On {self._picked_plane[2]}"
        if self._plane_key == AUTO:
            if self.result is not None and self.result.note and not self._settings_changed:
                return self.result.note
            return "Auto picks the best fit of the part’s main planes when you check."
        return ""

    def _on_kind(self, _index: int) -> None:
        self.end_pick()
        self._sync_widgets()
        self._on_setting()

    def _on_plane(self, _index: int) -> None:
        key = self.plane.currentData()
        if key in (MIDPLANE, PICKED):
            self.start_pick(key)
            return
        self._plane_key = key  # before end_pick, which puts the combo back to the committed choice
        self.end_pick()
        self._sync_widgets()
        self._on_setting()

    def _on_axis(self, _index: int) -> None:
        key = self.axis.currentData()
        if key == PICKED:
            self.start_pick("axis")
            return
        self._axis_key = key
        self.end_pick()
        self._sync_widgets()
        self._on_setting()

    def _on_setting(self, *_args) -> None:
        self.copies.setSuffix(f"  (every {360.0 / self.copies.value():g}°)")
        self.preview()
        self.settings_changed()
        self._update_note()

    def spec(self) -> SY.SymmetrySpec:
        rot = self.is_rotational()
        direction, through, point = None, self.through.currentData() or SY.BEST, None
        if rot:
            through = SY.BEST
            if self._axis_key == PICKED and self._picked_axis:
                point, direction, _name = self._picked_axis
                through = SY.POINT
            elif self._axis_key in SY.AXES:
                direction = SY.AXES[self._axis_key]
        else:
            chosen = {MIDPLANE: self._midplane, PICKED: self._picked_plane}.get(self._plane_key)
            if chosen:
                point, direction, _name = chosen
                through = SY.POINT
            elif self._plane_key in SY.PLANES:
                direction = SY.AXES[SY.PLANES[self._plane_key]]
        return SY.SymmetrySpec(kind=SY.ROTATIONAL if rot else SY.MIRROR, direction=direction, through=through,
                               point=point, offset=0.0 if rot else self.offset.value(),
                               copies=self.copies.value(), tolerance=self.tolerance.value())

    # -- picking a plane, a midplane or an axis ------------------------------------------------------

    def _pick_plane_button(self) -> None:
        """The pick button takes two faces for a midplane, else one face or plane."""
        if self.picking in (PICKED, MIDPLANE):
            self.end_pick()
            return
        faces = [s for s in selected_subelements() if point_and_normal(s[0]) is not None]
        self.start_pick(MIDPLANE if len(faces) >= 2 else PICKED)

    def on_pick_started(self, mode: str) -> None:
        if mode == "model":
            self.model_row.set_picking(True)
        elif mode == "axis":
            self.pick_axis.setChecked(True)
            self._update_note("Click a round face or edge in the 3D view.")
        else:
            self.pick_plane.setChecked(True)
            _set_key(self.plane, mode)
            if mode == MIDPLANE:
                first = self._first_face
                self._update_note(f"Now click the face opposite {first[2]}." if first
                                  else "Click two parallel flat faces in the 3D view.")
            else:
                self._update_note("Click a flat face or a datum plane in the 3D view.")

    def on_pick_ended(self, mode: str, done: bool) -> None:
        self.model_row.set_picking(False, self.target)
        self.pick_plane.setChecked(False)
        self.pick_axis.setChecked(False)
        self._first_face = None
        if not done:
            # Cancelled: back to what was chosen before.
            _set_key(self.plane, self._plane_key)
            _set_key(self.axis, self._axis_key)
        self._sync_widgets()

    def _commit_plane(self, key: str, picked: tuple) -> None:
        if key == MIDPLANE:
            self._midplane = picked
        else:
            self._picked_plane = picked
        self._plane_key = key
        for k, text in PICKED_TEXT.items():
            _set_text(self.plane, k, text if k == key else dict((p[0], p[1]) for p in PLANES)[k])
        _set_key(self.plane, key)
        self._sync_widgets()
        self._on_setting()

    def _apply_plane_from_selection(self) -> bool:
        picked = None
        try:
            subs = selected_subelements()
            if subs:
                found = point_and_normal(subs[0][0])
                if found is not None:
                    picked = (found[0], found[1], subs[0][1])
            if picked is None:
                sel = Gui.Selection.getSelection()
                if sel:
                    found = datum_plane(sel[0])
                    if found is not None:
                        picked = (found[0], found[1], sel[0].Label)
        except Exception as exc:
            App.Console.PrintWarning(f"DFM Symmetry Check: could not use that. {exc}\n")
            return False
        if picked is None:
            return False
        self._commit_plane(PICKED, picked)
        return True

    def _apply_midplane_from_selection(self) -> bool:
        """Two parallel faces: picked together, or one click after another."""
        faces = []
        for sub, name in selected_subelements():
            found = point_and_normal(sub)
            if found is not None:
                faces.append((found[0], found[1], name))
        if self._first_face is not None:
            faces = [self._first_face] + [f for f in faces if f[2] != self._first_face[2]]
        if len(faces) < 2:
            if faces and self._first_face is None:
                self._first_face = faces[0]
                if self.picking == MIDPLANE:
                    self._update_note(f"Now click the face opposite {faces[0][2]}.")
            return False
        first, second = faces[0], faces[1]
        plane = SY.midplane((first[0], first[1]), (second[0], second[1]))
        if plane is None:
            self._update_note(f"{first[2]} and {second[2]} aren’t parallel. Click a face opposite {first[2]}.")
            self._first_face = first
            return False
        self._commit_plane(MIDPLANE, (plane[0], plane[1], f"{first[2]} and {second[2]}"))
        return True

    def _apply_axis_from_selection(self) -> bool:
        subs = selected_subelements()
        if not subs:
            return False
        sub, name = subs[0]
        try:
            from ..utils.pull_direction import compute_direction

            found = compute_direction(sub)
        except Exception as exc:
            App.Console.PrintWarning(f"DFM Symmetry Check: could not use that. {exc}\n")
            return False
        if found is None:
            return False
        p, d = found
        self._picked_axis = ((p.x, p.y, p.z), G.vunit((d.x, d.y, d.z)), name)
        self._axis_key = PICKED
        _set_text(self.axis, PICKED, PICKED_AXIS_TEXT)
        _set_key(self.axis, PICKED)
        self._sync_widgets()
        self._on_setting()
        return True

    # -- preview ---------------------------------------------------------------------------------

    def preview(self) -> None:
        """Show the plane or axis as set, before checking (Auto shows none yet)."""
        if (self.result is not None and not self._settings_changed) or self._closed or self.in_map():
            return
        scene = self.scene()
        if scene is None or self.target is None:
            return
        try:
            spec = self.spec()
            if spec.direction is None:
                scene.set_visible("plane", False)
                return
            shape = self.shape()
            through = SY.BOX_CENTRE if spec.through == SY.BEST else spec.through
            origin = SY.through_point(shape, through, spec.point)
            if spec.kind == SY.MIRROR and spec.offset:
                origin = G.vadd(origin, G.vmul(G.vunit(spec.direction), spec.offset))
            box = G.bounding_box(shape, optimal=False)
            if spec.kind == SY.MIRROR:
                scene.set_plane(origin, spec.direction, box)
            else:
                scene.set_axis(origin, spec.direction, box)
            scene.set_visible("plane", True)
        except Exception as exc:
            App.Console.PrintLog(f"DFM Symmetry Check: no preview ({exc}).\n")

    # -- running ----------------------------------------------------------------------------------

    def compute(self, progress):
        shape = self.shape()
        recognition = recognition_for(shape, self.target)
        return SY.check(shape, self.spec(), recognition, sorted(self._ignored), progress)

    def _about(self) -> str:
        r: M.SymmetryResult = self.result
        if r.kind == SY.MIRROR:
            name = SY.plane_name(r.direction)
            return f"about {name}" if name else "about the plane"
        letter = SY.axis_letter(r.direction)
        return f"{r.copies} times round {letter}" if letter else f"{r.copies} times round the axis"

    def verdict(self):
        r: M.SymmetryResult = self.result
        ignored = sum(1 for d in r.differences if d.ignored)
        ignored_text = f" · {ignored} difference{'s' if ignored != 1 else ''} ignored" if ignored else ""
        if r.symmetric:
            return "pass", "Symmetric", f"{self._about()}, within ±{fmt_mm(r.tolerance)} mm{ignored_text}"
        n = len(r.counted)
        pct = 100.0 * r.difference_volume / r.volume if r.volume else 0.0
        detail = (f"{n} difference{'s' if n != 1 else ''} · {pct:.1f} % of the part · "
                  f"furthest {r.furthest:.1f} mm")
        return "warn", "Not symmetric", detail + (f" · {ignored} ignored" if ignored else "")

    def list_groups(self):
        rows = []
        for d in self.result.differences:
            what = "ignored" if d.ignored else d.detail
            rows.append((d, f"{what} · {fmt_volume(abs(d.volume))}"))
        return [Group(None, SYMMETRY_HEX, rows)]

    def after_result(self) -> None:
        self._update_note()

    # -- the key ------------------------------------------------------------------------------------

    def categories(self):
        rot = self.is_rotational() if self.result is None else self.result.kind == SY.ROTATIONAL
        rgb = hex_rgb(SYMMETRY_HEX)
        return [
            Category(ONE_SIDE, "No matching copy" if rot else "Only on one side", rgb,
                     "Material or a cut with no partner", navigable=True),
            Category(MISALIGNED, "Doesn’t line up", rgb, "On both sides, but not in the same place or size",
                     navigable=True),
            Category(SYMMETRIC, "Symmetric", hex_rgb("#C2C4C9")),
        ]

    def category_of(self, difference) -> int:
        return MISALIGNED if difference.kind == M.MISMATCH else ONE_SIDE

    def shares(self):
        r = self.result
        if r is None or r.volume <= 0:
            return {}
        out = {ONE_SIDE: 0.0, MISALIGNED: 0.0}
        for d in r.counted:
            out[self.category_of(d)] += abs(d.volume) / r.volume
        out[SYMMETRIC] = max(0.0, 1.0 - sum(out.values()))
        return out

    # -- drawing -----------------------------------------------------------------------------------

    def shown_object(self):
        return self.target

    def shown_shape(self):
        return self.shape()

    def draw(self) -> None:
        r = self.result
        scene = self.scene()
        if scene is None or r is None:
            return
        faces, solids, ghosts = {}, [], []
        for d in self.shown_differences():
            strong = self.emphasised(d)
            color = STRONG_HEX if strong else SYMMETRY_HEX
            for f in d.faces:
                faces[f] = color
            solids.extend((s, color, strong) for s in d.solids)
            if self.show_ghosts.isChecked():
                ghosts.extend((g, GHOST_HEX, strong) for g in d.ghosts)
        scene.show_model(faces)
        scene.set_solids(solids)
        scene.set_ghosts(ghosts)
        box = G.bounding_box(self.shape(), optimal=False)
        if r.kind == SY.MIRROR:
            scene.set_plane(r.origin, r.direction, box)
        else:
            scene.set_axis(r.origin, r.direction, box)
        scene.set_visible("plane", self.show_plane.isChecked())

    def map_values(self, points, progress):
        from ...core.inspection.deviation import asymmetry_values

        partner = SY.partner_shape(self.shape(), self.result)
        boxes = [d.box for d in self.result.differences if d.box is not None]
        return asymmetry_values(points, partner, boxes, self.result.tolerance, progress)

    def map_format(self, value: float) -> str:
        return f"{value:.2f} mm off"

    def map_range(self, values):
        finite = [v for v in values if math.isfinite(v)]
        return (0.0, max(max(finite), self.result.tolerance) if finite else 1.0)

    # -- saving -------------------------------------------------------------------------------------

    def saved_objects(self):
        return (self.target, None)

    def settings(self) -> dict:
        def picked(p):
            return [list(p[0]), list(p[1]), p[2]] if p else None

        return {
            "kind": "rotational" if self.is_rotational() else "mirror",
            "plane": self._plane_key,
            "axis": self._axis_key,
            "picked_plane": picked(self._picked_plane),
            "midplane": picked(self._midplane),
            "picked_axis": picked(self._picked_axis),
            "through": self.through.currentData(),
            "offset": self.offset.value(),
            "copies": self.copies.value(),
            "tolerance": self.tolerance.value(),
            "ghosts": self.show_ghosts.isChecked(),
            "show_plane": self.show_plane.isChecked(),
        }

    def load_settings(self, s: dict) -> None:
        def picked(value):
            return (tuple(value[0]), tuple(value[1]), value[2]) if value else None

        widgets = (self.kind, self.plane, self.axis, self.through, self.offset, self.copies, self.tolerance)
        for w in widgets:
            w.blockSignals(True)
        try:
            self.kind.setCurrentIndex(1 if s.get("kind") == "rotational" else 0)
            self._picked_plane = picked(s.get("picked_plane"))
            self._midplane = picked(s.get("midplane"))
            self._picked_axis = picked(s.get("picked_axis"))
            plane = s.get("plane", AUTO)
            if (plane == MIDPLANE and not self._midplane) or (plane == PICKED and not self._picked_plane):
                plane = AUTO
            axis = s.get("axis", AUTO)
            if axis == PICKED and not self._picked_axis:
                axis = AUTO
            self._plane_key, self._axis_key = plane, axis
            for key, text in PICKED_TEXT.items():
                if key == plane:
                    _set_text(self.plane, key, text)
            if axis == PICKED:
                _set_text(self.axis, PICKED, PICKED_AXIS_TEXT)
            self.plane.setCurrentIndex(max(self.plane.findData(plane), 0))
            self.axis.setCurrentIndex(max(self.axis.findData(axis), 0))
            self.through.setCurrentIndex(max(self.through.findData(s.get("through", SY.BEST)), 0))
            self.offset.setValue(float(s.get("offset", 0.0)))
            self.copies.setValue(int(s.get("copies", 6)))
            self.tolerance.setValue(float(s.get("tolerance", 0.01)))
        finally:
            for w in widgets:
                w.blockSignals(False)
        self.show_ghosts.setChecked(bool(s.get("ghosts", True)))
        self.show_plane.setChecked(bool(s.get("show_plane", True)))
        self._sync_widgets()
        self.load_display(s)

    def load_saved(self, saved) -> None:
        src = getattr(saved, "Source", None)
        if src is not None:
            self.set_model(src)
        if D.is_out_of_date(saved):
            self.mark_out_of_date()

    def after_clear(self) -> None:
        self._update_note()
        self.preview()
