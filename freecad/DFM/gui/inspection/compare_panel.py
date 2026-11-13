# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The Compare Bodies task panel.

Bodies: Reference (A) and Compared (B), each with a pick button, and a
swap button on B's row. Select two bodies before opening the tool and they
fill in, the first as A. Settings: how B is lined up with A, and a
tolerance.

After comparing: the verdict, then the changes grouped as Added in B,
Removed from A and Changed (right-click to ignore one), and Properties
(volume, faces and size of each). In the 3D view B shows with what was
added in orange, what was removed as a blue ghost and A as a faint ghost,
the info icon on each change, and a key whose arrows step through them.
"""

from __future__ import annotations

import math

from PySide6 import QtCore, QtWidgets

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ...app.contour.measures import Category
from ...core.inspection import compare as C
from ...core.inspection import models as M
from ...core.inspection.naming import fmt_mm, fmt_volume
from ..utils.widgets import make_flip_button
from . import document as D
from .panel_base import InspectPanel, ModelRow, combo, grid, has_shape, ocp_shape, recognition_for, spin
from .widgets import ADDED_HEX, CHANGED_HEX, NEUTRAL_HEX, REMOVED_HEX, Group, hex_rgb

ALIGNMENTS = [
    (C.PLACED, "As placed", "Compare the bodies where they are: right for two revisions of one part."),
    (C.CENTRES, "Centres of mass",
     "Move B so the centres of mass meet: for the same part placed elsewhere. A revised part’s centre "
     "of mass moves with its changes, so don’t use this for revisions."),
    (C.BEST_FIT, "Best fit (slower)",
     "Turn and move B to fit A as closely as it can, ignoring the parts that changed: for a copy placed "
     "and turned anywhere."),
]
KINDS = [(M.ADDED, "Added in B", ADDED_HEX), (M.REMOVED, "Removed from A", REMOVED_HEX),
         (M.CHANGED, "Changed", CHANGED_HEX)]
CODES = {M.ADDED: 1, M.REMOVED: 2, M.CHANGED: 3}
UNCHANGED = 0
GHOST_HEX = "#5D6F86"
STRONG = {M.ADDED: "#C4661A", M.REMOVED: "#1D5EA3"}


class ComparePanel(InspectPanel):
    TOOL = D.COMPARE
    TITLE = "Compare Bodies"
    ICON = ":/icons/dfm_compare.svg"
    RUN_TEXT = "Compare"
    LIST_TITLE = "Changes"
    LEGEND_TITLE = "Compared with A"
    MAP_TITLE = "Deviation of B from A"
    MAP_NO_VALUE = "Unchanged"
    MAP_COLORMAP = "RdBu"
    MAP_MODE_NAME = "Deviation map"

    def __init__(self, saved=None):
        self.a = None
        self.b = None
        self._shapes = {}
        self._a_visible = None
        self._reference_in_b = None
        super().__init__(saved)

    # -- inputs -------------------------------------------------------------------------------

    def build_inputs(self) -> None:
        self._pick_handlers = {"a": lambda: self._apply_from_selection("a"),
                               "b": lambda: self._apply_from_selection("b")}
        box = QtWidgets.QGroupBox("Bodies")
        g = grid(box)
        spacer = QtWidgets.QWidget()  # lines A's row up with B's, which has the swap button
        spacer.setFixedSize(28, 26)
        self.swap = make_flip_button(lambda _c=False: self._on_swap(), tooltip="Swap A and B", size=(28, 26))
        self.row_a = ModelRow("A, the reference. Pick it in the 3D view, or select two bodies before "
                              "opening the tool: the first is A.", spacer)
        self.row_b = ModelRow("B, the body compared with A. Pick it in the 3D view.", self.swap)
        self.row_a.pick_clicked.connect(lambda: self.start_pick("a"))
        self.row_b.pick_clicked.connect(lambda: self.start_pick("b"))
        g.addWidget(QtWidgets.QLabel("Reference (A)"), 0, 0)
        g.addWidget(self.row_a, 0, 1)
        g.addWidget(QtWidgets.QLabel("Compared (B)"), 1, 0)
        g.addWidget(self.row_b, 1, 1)
        self.root.addWidget(box)

        settings = QtWidgets.QGroupBox("Settings")
        sg = grid(settings)
        sg.addWidget(QtWidgets.QLabel("Alignment"), 0, 0)
        self.alignment = combo()
        for key, text, tip in ALIGNMENTS:
            self.alignment.addItem(text, key)
            self.alignment.setItemData(self.alignment.count() - 1, tip, QtCore.Qt.ItemDataRole.ToolTipRole)
        self.alignment.setToolTip(ALIGNMENTS[0][2])
        self.alignment.currentIndexChanged.connect(self._on_alignment)
        sg.addWidget(self.alignment, 0, 1)
        sg.addWidget(QtWidgets.QLabel("Tolerance"), 1, 0)
        self.tolerance = spin(0.01, lo=0.0001, hi=10.0, step=0.01, decimals=3)
        self.tolerance.setToolTip("Differences smaller than this don’t count")
        self.tolerance.installEventFilter(self._enter_filter)
        self.tolerance.valueChanged.connect(lambda _v: self.settings_changed())
        sg.addWidget(self.tolerance, 1, 1)
        self.settings_box = settings
        self.root.addWidget(settings)

    def display_checks(self):
        return [("show_a", "Show A as a ghost", "Show A, faint, where it is relative to B", False),
                ("show_rest", "Show unchanged material",
                 "Show the rest of B; off, only the changes show clearly", False)]

    def _on_alignment(self, _i: int) -> None:
        self.alignment.setToolTip(ALIGNMENTS[max(self.alignment.currentIndex(), 0)][2])
        self.settings_changed()

    # -- bodies --------------------------------------------------------------------------------

    def auto_select(self) -> None:
        try:
            sel = [o for o in Gui.Selection.getSelection() if has_shape(o)]
        except Exception:
            sel = []
        if sel:
            self.set_body("a", sel[0])
        if len(sel) >= 2:
            self.set_body("b", sel[1])
        if sel:
            Gui.Selection.clearSelection()

    def set_body(self, which: str, obj) -> bool:
        if not has_shape(obj):
            App.Console.PrintWarning("DFM Compare Bodies: pick a solid body.\n")
            return False
        other = self.b if which == "a" else self.a
        if obj is other:
            App.Console.PrintWarning("DFM Compare Bodies: A and B must be different bodies.\n")
            return False
        if self.result is not None:
            self.clear()
        self._restore_a()
        setattr(self, which, obj)
        self._shapes.pop(which, None)
        (self.row_a if which == "a" else self.row_b).set_object(obj)
        self.refresh_run_state()
        return True

    def _apply_from_selection(self, which: str) -> bool:
        sel = Gui.Selection.getSelection()
        return bool(sel) and self.set_body(which, sel[0])

    def _on_swap(self) -> None:
        if self.result is not None:
            self.clear()
        self._restore_a()
        self.a, self.b = self.b, self.a
        self._shapes = {("b" if k == "a" else "a"): v for k, v in self._shapes.items()}
        self.row_a.set_object(self.a)
        self.row_b.set_object(self.b)
        self.refresh_run_state()

    def shape_of(self, which: str):
        if which not in self._shapes:
            obj = self.a if which == "a" else self.b
            self._shapes[which] = ocp_shape(obj)
        return self._shapes[which]

    def ready(self) -> bool:
        return self.a is not None and self.b is not None

    def not_ready_reason(self) -> str:
        return "Pick A and B first." if not self.ready() else ""

    def on_pick_started(self, mode: str) -> None:
        (self.row_a if mode == "a" else self.row_b).set_picking(True)

    def on_pick_ended(self, mode: str, done: bool) -> None:
        self.row_a.set_picking(False, self.a)
        self.row_b.set_picking(False, self.b)

    # -- running -----------------------------------------------------------------------------

    def compute(self, progress):
        a, b = self.shape_of("a"), self.shape_of("b")
        rec_a = recognition_for(a, self.a)
        rec_b = recognition_for(b, self.b)
        self._reference_in_b = None
        return C.compare(a, b, self.tolerance.value(), self.alignment.currentData() or C.PLACED,
                         rec_a, rec_b, sorted(self._ignored), progress)

    def verdict(self):
        r: M.CompareResult = self.result
        ignored = sum(1 for d in r.differences if d.ignored)
        if r.identical:
            detail = f"within ±{fmt_mm(r.tolerance)} mm · "
            detail += (f"{ignored} change{'s' if ignored != 1 else ''} ignored" if ignored
                       else "same volume and faces")
            return "pass", "Identical", detail
        n = len(r.counted)
        net = r.net
        pct = 100.0 * abs(net) / r.a.volume if r.a and r.a.volume else 0.0
        if abs(net) < 1e-6:
            weight = "the same volume"
        else:
            weight = f"B is {fmt_volume(abs(net))} ({pct:.1f} %) {'heavier' if net > 0 else 'lighter'}"
        detail = f"{n} change{'s' if n != 1 else ''} · {weight}"
        return "warn", "Different", detail + (f" · {ignored} ignored" if ignored else "")

    def list_groups(self):
        r: M.CompareResult = self.result
        groups = []
        for kind, title, color in KINDS:
            rows = []
            for d in r.differences:
                if d.kind == kind:
                    what = "ignored" if d.ignored else d.detail
                    rows.append((d, f"{what} · {fmt_volume(d.volume, signed=True)}"))
            groups.append(Group(title, color, rows))
        groups.append(Group("Properties", NEUTRAL_HEX, self._property_rows(), counts=False))
        return groups

    def _property_rows(self):
        a, b = self.result.a, self.result.b
        if a is None or b is None:
            return []

        def size(p):
            return " × ".join(fmt_mm(v) for v in p.size)

        dv = b.volume - a.volume
        volume = f"{fmt_volume(a.volume, unit=False)} → {fmt_volume(b.volume)}"
        if abs(dv) > 1e-6:
            volume += f" ({fmt_volume(dv, signed=True, unit=False)})"
        df = b.faces - a.faces
        faces = f"{a.faces} → {b.faces}" + (f" ({df:+d})" if df else "")
        box = f"{size(a)} mm, same" if size(a) == size(b) else f"{size(a)} → {size(b)} mm"
        return [(None, "Volume", volume), (None, "Faces", faces), (None, "Bounding box", box)]

    def after_result(self) -> None:
        r = self.result
        if r.alignment == C.BEST_FIT and r.fit_rms is not None:
            self.banner.setToolTip(f"B was fitted onto A: {r.fit_rms:.3f} mm RMS on the unchanged surfaces.")
        else:
            self.banner.setToolTip("")

    def after_clear(self) -> None:
        self._restore_a()
        self._reference_in_b = None

    # -- the key ------------------------------------------------------------------------------------

    def categories(self):
        cats = [Category(CODES[kind], title, hex_rgb(color), navigable=True) for kind, title, color in KINDS]
        return cats + [Category(UNCHANGED, "Unchanged", hex_rgb("#C2C4C9"))]

    def category_of(self, difference) -> int:
        return CODES.get(difference.kind, CODES[M.CHANGED])

    def shares(self):
        r = self.result
        if r is None or r.a is None or r.a.volume <= 0:
            return {}
        out = {code: 0.0 for code in CODES.values()}
        for d in r.counted:
            out[self.category_of(d)] += abs(d.volume) / r.a.volume
        out[UNCHANGED] = max(0.0, 1.0 - sum(out.values()))
        return out

    # -- drawing -------------------------------------------------------------------------------

    def shown_object(self):
        return self.b

    def shown_shape(self):
        return self.shape_of("b")

    def reference_in_b(self):
        if self._reference_in_b is None and self.result is not None:
            self._reference_in_b = C.reference_in_b(self.shape_of("a"), self.result)
        return self._reference_in_b

    def _hide_a(self) -> None:
        if self.a is None:
            return
        try:
            vobj = self.a.ViewObject
            if self._a_visible is None:
                self._a_visible = bool(vobj.Visibility)
            vobj.Visibility = False
        except Exception:
            pass

    def _restore_a(self) -> None:
        if self._a_visible is None or self.a is None:
            self._a_visible = None
            return
        try:
            self.a.ViewObject.Visibility = self._a_visible
        except Exception:
            pass
        self._a_visible = None

    def draw(self) -> None:
        r = self.result
        scene = self.scene()
        if scene is None or r is None:
            return
        self._hide_a()
        solids, ghosts = [], []
        for d in self.shown_differences():
            strong = self.emphasised(d)
            added = STRONG[M.ADDED] if strong else ADDED_HEX
            removed = STRONG[M.REMOVED] if strong else REMOVED_HEX
            # A change is material added (solid) and material removed (ghost), as are the others.
            solids.extend((s, added, strong) for s in d.solids)
            ghosts.extend((g, removed, strong) for g in d.ghosts)
        scene.show_model({}, None if self.show_rest.isChecked() else 0.88)
        scene.set_solids(solids)
        if self.show_a.isChecked():
            ghosts.insert(0, (self.reference_in_b(), GHOST_HEX, False))
        scene.set_ghosts(ghosts)

    def map_values(self, points, progress):
        from ...core.inspection.deviation import deviation_values

        boxes = [d.box for d in self.result.differences if d.box is not None]
        return deviation_values(points, self.reference_in_b(), boxes, self.result.tolerance, progress)

    def map_format(self, value: float) -> str:
        return f"{value:+.2f} mm".replace("-", "−")

    def map_range(self, values):
        finite = [abs(v) for v in values if math.isfinite(v)]
        m = max(finite) if finite else 1.0
        return (-m, m)

    # -- saving --------------------------------------------------------------------------------

    def saved_objects(self):
        return (self.b, self.a)

    def settings(self) -> dict:
        return {"alignment": self.alignment.currentData(), "tolerance": self.tolerance.value(),
                "show_a": self.show_a.isChecked(), "show_rest": self.show_rest.isChecked()}

    def load_settings(self, s: dict) -> None:
        self.alignment.blockSignals(True)
        self.tolerance.blockSignals(True)
        try:
            self.alignment.setCurrentIndex(max(self.alignment.findData(s.get("alignment", C.PLACED)), 0))
            self.tolerance.setValue(float(s.get("tolerance", 0.01)))
        finally:
            self.alignment.blockSignals(False)
            self.tolerance.blockSignals(False)
        self.show_a.setChecked(bool(s.get("show_a", True)))
        self.show_rest.setChecked(bool(s.get("show_rest", True)))
        self.load_display(s)

    def load_saved(self, saved) -> None:
        a, b = getattr(saved, "Reference", None), getattr(saved, "Source", None)
        if a is not None:
            self.set_body("a", a)
        if b is not None:
            self.set_body("b", b)
        if D.is_out_of_date(saved):
            self.mark_out_of_date()

    def after_close(self) -> None:
        self._restore_a()

    def _remove_view(self) -> None:
        super()._remove_view()
        self._restore_a()

    def _on_mode(self, in_map: bool) -> None:
        super()._on_mode(in_map)
        if in_map and self.result is not None:
            # A sits where B is: shown, it would cover B's map.
            self._hide_a()
