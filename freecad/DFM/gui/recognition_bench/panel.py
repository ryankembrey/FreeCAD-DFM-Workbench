# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The feature recognition test bench (developer tool).

Laid out like the workbench's other panels: the model at the top with a
pick button, one primary button, a banner with the outcome, then tabs.

* **Features**: the results panel's tree, toolbar and camera buttons, for
  what was found. Selecting a row draws just that (a feature, a kind, the
  faces in no feature) in its colour on a neutral model, with the rest
  pale or gray. Clicking a face in the 3D view selects its feature. The
  details below the tree list parameters, faces and the evidence; face
  numbers in them are links that show those faces. The review is done in
  the tree as well: C marks a feature correct, W wrong, U unreviewed
  (or right-click).
* **Review**: progress, features the engine missed (Ctrl+click their
  faces in the 3D view, then add them), and saving the part with its
  review to the corpus, where the headless suite checks it from then on.
* **Suite**: the test suite (fixtures in every variant, plus the corpus),
  as the headless runner runs it. Double-click a case to open and
  inspect it.

The banner also checks that FreeCAD's Face<n> is the face the engine calls
n (area and centre of mass of every face), and says so if it isn't.

Shown only when Preferences > DFM > General > Developer Options > "Feature
recognition test bench" is on.
"""

from __future__ import annotations

import html
import json
import os
import tempfile
import traceback
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtGui import QStandardItemModel

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ...app.recognition_service import corpus_dir, read_options
from ...core.recognition import FeatureKind
from ..results.row_actions import MOVES, RowCameraButtons
from ..results.tabs.common import C_ACCENT, C_ERROR, C_PASS, C_WARNING, muted_label, section_header
from ..rule_cards import COLLAPSE_ALL_SVG, EXPAND_ALL_SVG, GROUP_SVG, apply_svg_icon
from ..utils.direction_row import PICK_SVG, icon_button, style_primary_button
from . import tree as T
from .picking import FacePicker
from .scene import (PICKED_COLOR, UNOWNED_COLOR, BenchScene, Focus, Style, edge_colors,
                    face_colors, kind_color)
from .session import BenchSession, Missed

_ICON = ":/icons/dfm_analysis.svg"
_DIRECTIONS = {"None": None, "+X": (1, 0, 0), "−X": (-1, 0, 0), "+Y": (0, 1, 0), "−Y": (0, -1, 0),
               "+Z": (0, 0, 1), "−Z": (0, 0, -1)}
_TITLE = "Recognition bench"

# A palette: colour every feature by its kind.
PALETTE_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    b'<path d="M12 3a9 9 0 1 0 0 18c1.1 0 1.6-.9 1.2-1.8-.5-1-.2-2.2 1.1-2.2H17a4 4 0 0 0 4-4'
    b'c0-5.5-4-10-9-10z"/><circle cx="7.5" cy="11" r="1.2"/><circle cx="10.5" cy="7" r="1.2"/>'
    b'<circle cx="15.5" cy="8" r="1.2"/></svg>'
)
# A cube's edges, one drawn bold: colour edges by convexity.
EDGES_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
    b'<path d="M4 8l8-4 8 4v8l-8 4-8-4z"/><path d="M4 8l8 4 8-4"/><path d="M12 12v8" stroke-width="3"/></svg>'
)
# A dashed square with a question mark: faces in no feature.
UNOWNED_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    b'<rect x="3.5" y="3.5" width="17" height="17" rx="2" stroke-dasharray="3 3"/>'
    b'<path d="M9.8 9.5a2.3 2.3 0 1 1 3.2 2.1c-.7.3-1 .8-1 1.5v.4"/><circle cx="12" cy="16.6" r=".6"/></svg>'
)
# Two sheets: copy.
COPY_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    b'<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10'
    b'a1 1 0 0 0 1 1h3"/></svg>'
)

_TOOL_CSS = (
    "QToolButton { border: none; border-radius: 3px; padding: 2px; background: transparent; }"
    "QToolButton:hover { background: rgba(127, 127, 127, 45); }"
    "QToolButton:pressed, QToolButton:checked { background: rgba(127, 127, 127, 80); }"
    "QToolButton::menu-indicator { image: none; width: 0px; }"
)
_CARD_CSS = (
    "QFrame#dfmBenchCard { background: rgba(127, 127, 127, 26);"
    " border: 1px solid rgba(127, 127, 127, 90); border-radius: 4px; }"
    " QFrame#dfmBenchDivider { background: rgba(127, 127, 127, 70); border: none; }"
)


def _ocp_from_object(obj):
    from ...core.utils.conversion import freecad_to_ocp
    return freecad_to_ocp(obj.Shape)


def _freecad_faces(obj) -> list:
    """[(area, centre of mass)] of *obj*'s faces in FreeCAD's Face<n> order."""
    out = []
    for face in obj.Shape.Faces:
        c = face.CenterOfMass
        out.append((face.Area, (c.x, c.y, c.z)))
    return out


def _show_ocp_shape(shape_occ, name: str):
    """Add an OCP shape to the active document (a new one if none) as a
    Part::Feature; returns the object."""
    import Part  # type: ignore
    from ...core.recognition.testing import shapes as B

    fd, path = tempfile.mkstemp(suffix=".brep")
    os.close(fd)
    try:
        B.write_brep(shape_occ, path)
        shape = Part.Shape()
        shape.read(path)
    finally:
        os.remove(path)
    doc = App.ActiveDocument or App.newDocument("RecognitionBench")
    obj = doc.addObject("Part::Feature", "".join(c if c.isalnum() else "_" for c in name)[:40] or "Case")
    obj.Label = name
    obj.Shape = shape
    doc.recompute()
    try:
        Gui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass
    return obj


def _tool_button(svg: bytes, tooltip: str, checkable: bool = False, checked: bool = False):
    btn = QtWidgets.QToolButton()
    btn.setFixedSize(26, 24)
    btn.setToolTip(tooltip)
    btn.setAccessibleName(tooltip)
    btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(_TOOL_CSS)
    btn.setCheckable(checkable)
    btn.setChecked(checked)
    apply_svg_icon(btn, svg, 18)
    return btn


def _separator() -> QtWidgets.QWidget:
    sep = QtWidgets.QFrame()
    sep.setFixedSize(1, 20)
    sep.setStyleSheet("QFrame { background: rgba(127, 127, 127, 110); border: none; }")
    return sep


class Banner(QtWidgets.QFrame):
    """The results panel's verdict banner: a coloured bar, a bold word, detail."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dfmBenchBanner")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(8, 6, 10, 6)
        row.setSpacing(8)
        self.word = QtWidgets.QLabel()
        font = self.word.font()
        font.setBold(True)
        self.word.setFont(font)
        row.addWidget(self.word)
        self.detail = QtWidgets.QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(QtCore.Qt.TextFormat.RichText)
        row.addWidget(self.detail, 1)
        self.set("", "", "#888888")

    def set(self, word: str, detail: str, color: str) -> None:
        self.word.setText(f'<span style="color: {color}">{html.escape(word)}</span>')
        self.detail.setText(detail)
        c = QtGui.QColor(color)
        self.setStyleSheet(
            "QFrame#dfmBenchBanner {"
            f" background: rgba({c.red()}, {c.green()}, {c.blue()}, 40);"
            f" border: none; border-left: 4px solid {color}; border-radius: 3px; }}"
            " QFrame#dfmBenchBanner QLabel { background: transparent; }")
        self.setVisible(bool(word or detail))

    def text(self) -> str:
        return QtGui.QTextDocumentFragment.fromHtml(self.word.text() + " " + self.detail.text()).toPlainText()


class RecognitionBenchPanel:
    def __init__(self, target_object=None):
        self.session = BenchSession()
        self.scene = BenchScene()
        self.picker = FacePicker(self._on_pick)
        self.target_object = None
        self._shape = None
        self._source = ""
        self._focus = Focus()
        self._suite_report = None
        self._stop = False
        self._observing = False
        self._picking_model = False
        self._camera = None
        self._fixed_shape = None

        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Feature Recognition Bench")
        self.form.setWindowIcon(QtGui.QIcon(_ICON))
        root = QtWidgets.QVBoxLayout(self.form)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)
        self._build_header(root)
        self.tabs = QtWidgets.QTabWidget()
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_features(), "Features")
        self.tabs.addTab(self._build_review(), "Review")
        self.tabs.addTab(self._build_suite(), "Suite")

        try:
            Gui.Selection.addObserver(self)
            self._observing = True
        except Exception:
            pass
        if target_object is None:
            target_object = self._default_target()
        if target_object is not None:
            self.set_target(target_object)

    # =====================================================================
    # Header: model, recognise, banner
    # =====================================================================

    def _build_header(self, root: QtWidgets.QVBoxLayout) -> None:
        box = QtWidgets.QGroupBox("Model")
        grid = QtWidgets.QGridLayout(box)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(6)
        self.le_object = QtWidgets.QLineEdit()
        self.le_object.setReadOnly(True)
        self.le_object.setPlaceholderText("No model selected")
        self.pb_object = icon_button(PICK_SVG, "Pick the model in the 3D view, or select it first", checkable=True)
        self.pb_object.clicked.connect(self._on_pick_object)
        grid.addWidget(self.le_object, 0, 0, 1, 2)
        grid.addWidget(self.pb_object, 0, 2)
        self.use_hints = QtWidgets.QCheckBox("Use the model's history")
        self.use_hints.setToolTip("Feed PartDesign features (holes, fillets, pockets, ...) in as hints. "
                                  "Turn off to see what the geometry alone gives.")
        self.use_hints.setChecked(read_options().use_history_hints)
        grid.addWidget(self.use_hints, 1, 0)
        self.run_button = QtWidgets.QPushButton()
        style_primary_button(self.run_button, "Recognise")
        self.run_button.setToolTip("Recognise the model as it is now (re-reads it after any change).")
        self.run_button.clicked.connect(self.recognise)
        grid.addWidget(self.run_button, 1, 1, 1, 2)
        grid.setColumnStretch(0, 1)
        root.addWidget(box)
        self.banner = Banner()
        root.addWidget(self.banner)

    def _default_target(self):
        try:
            from ..utils.body_select import find_default_body
            return find_default_body()
        except Exception:
            return None

    def _on_pick_object(self) -> None:
        sel = []
        try:
            sel = Gui.Selection.getSelection()
        except Exception:
            pass
        if sel:
            self.pb_object.setChecked(False)
            self._picking_model = False
            self.set_target(sel[0])
            return
        self._picking_model = self.pb_object.isChecked()
        if self._picking_model:
            self.scene.plain()
            self.le_object.setPlaceholderText("Click the model in the 3D view")

    def set_target(self, obj, shape_occ=None, source: str = "") -> None:
        """Inspect *obj*. *shape_occ* is used as given (a fixture or corpus
        case); otherwise the model is read afresh at every Recognise."""
        self.scene.remove()
        self.picker.detach()
        self.target_object = obj
        self._fixed_shape = shape_occ
        self._source = source
        self._camera = None
        self.le_object.setText(obj.Label if obj is not None else "")
        self.le_object.setToolTip(f"{obj.Label} ({obj.TypeId})" if obj is not None else "")
        self.session = BenchSession()
        self._focus = Focus()
        self._fill_tree()
        self.details.clear()
        self.banner.set("", "", "#888888")
        self._refresh_review()

    # =====================================================================
    # Recognise
    # =====================================================================

    def recognise(self) -> None:
        if self.target_object is None:
            self.banner.set("No model", "Pick a body or part first.", C_WARNING)
            return
        options = read_options().with_(use_history_hints=self.use_hints.isChecked())
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            # Read the model now: it may have changed since the last run.
            shape = self._fixed_shape if self._fixed_shape is not None else _ocp_from_object(self.target_object)
            self._shape = shape
            hints = []
            if options.use_history_hints:
                try:
                    from ...app.recognition_hints import hints_for
                    hints = hints_for(self.target_object)
                except Exception as exc:
                    App.Console.PrintWarning(f"DFM bench: no history hints: {exc}\n")

            def progress(stage, fraction):
                self.banner.set("Recognising", html.escape(f"{stage}..."), C_ACCENT)
                QtWidgets.QApplication.processEvents()

            self.session.run(shape, self.target_object.Label, options, hints, self.target_object, progress,
                             source=self._source or self.target_object.Label)
            try:
                self.session.check_numbering(_freecad_faces(self.target_object))
            except Exception:
                self.session.numbering_errors = []
        except Exception as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            App.Console.PrintError(traceback.format_exc())
            self.banner.set("Failed", html.escape(str(exc)), C_ERROR)
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        self.scene.attach(self.target_object, rebuild=True)
        self.picker.attach(self.target_object, shape)
        self._focus = Focus()
        self._update_banner()
        self._fill_tree()
        self._refresh_review()
        self.details.setHtml(self._overview_html())
        self.redraw()

    def _update_banner(self) -> None:
        r = self.session.result
        if r is None:
            return
        n_un = len(r.unowned_faces())
        bits = [f"{r.face_count} faces", f"{n_un} in no feature", f"{self.session.seconds * 1000:.0f} ms"]
        if self.session.hints:
            bits.append(f"{len(self.session.hints)} history hint(s)")
        form = getattr(r, "form", None) or {}
        if form.get("form"):
            bits.append({"2.5d": "2.5D part", "extrusion": "extrusion", "revolve": "revolve",
                         "mixed": "mixed form"}.get(form["form"], form["form"]))
        detail = " · ".join(bits)
        bad = self.session.numbering_errors
        if bad:
            faces = ", ".join(f"Face{i}" for i in bad[:6]) + ("..." if len(bad) > 6 else "")
            self.banner.set("Face numbers differ from FreeCAD's",
                            html.escape(f"{len(bad)} faces ({faces}): colours and labels can't be trusted. "
                                        "Recognise again; if it persists, save this case and report it."), C_ERROR)
            return
        if r.warnings:
            detail += f"<br>{html.escape(r.warnings[0])}" + (f" (+{len(r.warnings) - 1} more)" if len(r.warnings) > 1
                                                           else "")
        word = f"{len(r.features)} features"
        self.banner.set(word, detail, C_WARNING if n_un or r.warnings else C_PASS)

    def annotate(self) -> None:
        if self.session.result is None:
            self.recognise()
            if self.session.result is None:
                return
        side = _DIRECTIONS[self.side.currentText()]
        try:
            self.session.annotate(pull=_DIRECTIONS[self.pull.currentText()],
                                  print_direction=_DIRECTIONS[self.print_dir.currentText()],
                                  side_actions=[side] if side else [], nozzle=self.nozzle.value(),
                                  options=read_options())
        except Exception as exc:
            self.banner.set("Annotation failed", html.escape(str(exc)), C_ERROR)
            return
        self._fill_tree()
        self.redraw()

    # =====================================================================
    # Features tab
    # =====================================================================

    def _build_features(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        card = QtWidgets.QFrame()
        card.setObjectName("dfmBenchCard")
        card.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setStyleSheet(_CARD_CSS)
        cv = QtWidgets.QVBoxLayout(card)
        cv.setContentsMargins(2, 2, 2, 2)
        cv.setSpacing(0)

        bar = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(4)
        self.btn_group = _tool_button(GROUP_SVG, "Grouping: By kind")
        self.btn_group.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QtWidgets.QMenu(self.btn_group)
        self._group_actions = QtGui.QActionGroup(menu)
        for i, (mode, label) in enumerate(T.GROUP_MODES):
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(i == 0)
            act.setData(mode)
            self._group_actions.addAction(act)
        self._group_actions.triggered.connect(self._on_group_changed)
        self.btn_group.setMenu(menu)
        row.addWidget(self.btn_group)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter: kind, name or Face12")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._fill_tree(keep=True))
        row.addWidget(self.search, 1)
        row.addWidget(_separator())
        self.btn_colour = _tool_button(PALETTE_SVG, "Colour every feature by kind (pale while one is selected)",
                                       checkable=True, checked=True)
        self.btn_unowned = _tool_button(UNOWNED_SVG, "Show faces in no feature (amber)", checkable=True,
                                        checked=True)
        self.btn_edges = _tool_button(EDGES_SVG, "Colour edges by convexity: red convex, blue concave, "
                                                 "green smooth, orange variable", checkable=True)
        for b in (self.btn_colour, self.btn_unowned, self.btn_edges):
            row.addWidget(b)
        self.btn_colour.toggled.connect(lambda _on: self.redraw())
        self.btn_edges.toggled.connect(lambda _on: self.redraw())
        self.btn_unowned.toggled.connect(self._on_unowned_toggled)
        row.addWidget(_separator())
        self.btn_copy = _tool_button(COPY_SVG, "Copy the whole result as JSON")
        self.btn_copy.clicked.connect(self._copy_json)
        row.addWidget(self.btn_copy)
        self.btn_expand = _tool_button(EXPAND_ALL_SVG, "Expand all")
        self.btn_collapse = _tool_button(COLLAPSE_ALL_SVG, "Collapse all")
        row.addWidget(self.btn_expand)
        row.addWidget(self.btn_collapse)
        cv.addWidget(bar)
        div = QtWidgets.QFrame()
        div.setObjectName("dfmBenchDivider")
        div.setFixedHeight(1)
        cv.addWidget(div)
        cv.addSpacing(2)

        self.model = QStandardItemModel()
        self.tree = QtWidgets.QTreeView()
        self.tree.setObjectName("dfmBenchTree")
        self.tree.setStyleSheet("QTreeView#dfmBenchTree { background: transparent; border: none; }")
        self.tree.viewport().setAutoFillBackground(False)
        self.tree.setModel(self.model)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMouseTracking(True)
        self.delegate = T.FeatureTreeDelegate(self.tree)
        self.tree.setItemDelegate(self.delegate)
        self.tree.setMinimumHeight(220)
        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.selectionModel().currentChanged.connect(self._on_current_changed)
        self.tree.doubleClicked.connect(lambda _i: self._camera_move("zoom_face"))
        self._keys = _KeyFilter(self)
        self.tree.installEventFilter(self._keys)
        self.btn_expand.clicked.connect(self.tree.expandAll)
        self.btn_collapse.clicked.connect(self.tree.collapseAll)
        cv.addWidget(self.tree, 1)
        v.addWidget(card, 3)

        self.row_actions = RowCameraButtons(self.tree.viewport())
        self.row_actions.triggered.connect(self._camera_move)
        later = lambda *_a: QtCore.QTimer.singleShot(0, self._place_row_actions)  # noqa: E731
        self.tree.selectionModel().currentChanged.connect(later)
        self.tree.verticalScrollBar().valueChanged.connect(later)
        self.tree.expanded.connect(later)
        self.tree.collapsed.connect(later)
        self.model.modelReset.connect(later)
        self.model.rowsInserted.connect(later)

        self.count_label = muted_label("", small=True)
        hint = muted_label("Click a face in the 3D view to find its feature.  C / W / U: mark correct, "
                           "wrong, unreviewed.  Z / F: zoom, face.", small=True)
        hint.setWordWrap(True)
        v.addWidget(self.count_label)

        self.details = QtWidgets.QTextBrowser()
        self.details.setOpenLinks(False)
        self.details.anchorClicked.connect(self._on_link)
        self.details.setMinimumHeight(150)
        self.details.setStyleSheet("QTextBrowser { border: 1px solid rgba(127, 127, 127, 90); border-radius: 4px; }")
        v.addWidget(self.details, 2)
        v.addWidget(hint)

        box = QtWidgets.QGroupBox("Directions (draft, undercuts, 3D printing)")
        grid = QtWidgets.QGridLayout(box)
        grid.setContentsMargins(8, 8, 8, 8)
        self.pull = QtWidgets.QComboBox()
        self.side = QtWidgets.QComboBox()
        self.print_dir = QtWidgets.QComboBox()
        for combo, default in ((self.pull, "+Z"), (self.side, "None"), (self.print_dir, "None")):
            combo.addItems(list(_DIRECTIONS))
            combo.setCurrentText(default)
        grid.addWidget(QtWidgets.QLabel("Pull"), 0, 0)
        grid.addWidget(self.pull, 0, 1)
        grid.addWidget(QtWidgets.QLabel("Side action"), 0, 2)
        grid.addWidget(self.side, 0, 3)
        grid.addWidget(QtWidgets.QLabel("Print"), 1, 0)
        grid.addWidget(self.print_dir, 1, 1)
        grid.addWidget(QtWidgets.QLabel("Nozzle"), 1, 2)
        self.nozzle = QtWidgets.QDoubleSpinBox()
        self.nozzle.setRange(0.05, 5.0)
        self.nozzle.setDecimals(2)
        self.nozzle.setValue(0.4)
        self.nozzle.setSuffix(" mm")
        grid.addWidget(self.nozzle, 1, 3)
        ann = QtWidgets.QPushButton("Annotate")
        ann.setToolTip("Add draft, undercut and 3D printing annotations for these directions.")
        ann.clicked.connect(self.annotate)
        grid.addWidget(ann, 2, 0, 1, 4)
        v.addWidget(box)
        return w

    # -- Tree ----------------------------------------------------------------

    @property
    def group_mode(self) -> str:
        act = self._group_actions.checkedAction()
        return act.data() if act is not None else T.GROUP_BY_KIND

    def _on_group_changed(self, action) -> None:
        self.btn_group.setToolTip(f"Grouping: {action.text()}")
        self._fill_tree(keep=True)

    def _fill_tree(self, keep: bool = False) -> None:
        current = self._current_key() if keep else None
        expanded = self._expanded_keys() if keep else None
        self.tree.selectionModel().blockSignals(True)
        threshold = read_options().min_confidence
        shown = T.build(self.model, self.session, self.group_mode, threshold, self.btn_unowned.isChecked(),
                        self.search.text())
        self.tree.selectionModel().blockSignals(False)
        for row in range(self.model.rowCount()):
            idx = self.model.index(row, 0)
            key = self._key_of(idx)
            if expanded is None or key in expanded:
                if idx.data(T.R_TYPE) != "unowned" or expanded is not None:
                    self.tree.expand(idx)
        if current is not None:
            idx = self._find(current)
            if idx is not None:
                self.tree.selectionModel().blockSignals(True)
                self.tree.setCurrentIndex(idx)
                self.tree.selectionModel().blockSignals(False)
        r = self.session.result
        if r is not None:
            total = len(self.session.features())
            done, wrong, n = self.session.review_counts()
            self.count_label.setText(f"{shown} of {total} features shown · {done}/{n} reviewed"
                                     + (f" · {wrong} wrong" if wrong else ""))
        else:
            self.count_label.setText("")
        self._place_row_actions()

    @staticmethod
    def _key_of(idx: QtCore.QModelIndex):
        t = idx.data(T.R_TYPE)
        if t == "feature":
            return ("feature", idx.data(T.R_FID))
        if t == "face":
            return ("face", idx.data(T.R_FACE))
        return (t, idx.data(T.R_PRIMARY))

    def _current_key(self):
        idx = self.tree.currentIndex()
        return self._key_of(idx) if idx.isValid() else None

    def _expanded_keys(self) -> set:
        out = set()
        for row in range(self.model.rowCount()):
            idx = self.model.index(row, 0)
            if self.tree.isExpanded(idx):
                out.add(self._key_of(idx))
        return out

    def _find(self, key) -> Optional[QtCore.QModelIndex]:
        def walk(parent):
            for row in range(self.model.rowCount(parent)):
                idx = self.model.index(row, 0, parent)
                if self._key_of(idx) == key:
                    return idx
                hit = walk(idx)
                if hit is not None:
                    return hit
            return None
        return walk(QtCore.QModelIndex())

    def select_feature(self, fid: int) -> bool:
        idx = self._find(("feature", fid))
        if idx is None and self.search.text():
            self.search.clear()
            idx = self._find(("feature", fid))
        if idx is None:
            return False
        self.tree.scrollTo(idx)
        self.tree.setCurrentIndex(idx)
        return True

    def _on_current_changed(self, current, _previous=None) -> None:
        if not current.isValid():
            self._focus = Focus()
            self.details.setHtml(self._overview_html())
        else:
            t = current.data(T.R_TYPE)
            if t == "feature":
                fid = current.data(T.R_FID)
                self._focus = Focus.of_feature(fid)
                self.details.setHtml(self._feature_html(fid))
            elif t == "group" and current.data(T.R_KIND):
                kind = FeatureKind(current.data(T.R_KIND))
                self._focus = Focus.of_kind(kind)
                self.details.setHtml(self._kind_html(kind))
            elif t == "group":
                fids = [self.model.itemFromIndex(current).child(i).data(T.R_FID)
                        for i in range(self.model.itemFromIndex(current).rowCount())]
                faces = [face for fid in fids for face in (self.session.feature(fid).faces
                                                           if self.session.feature(fid) else ())]
                self._focus = Focus.of_faces(faces)
                self.details.setHtml(f"<b>{html.escape(current.data(T.R_PRIMARY))}</b>: {len(fids)} features")
            elif t == "unowned":
                self._focus = Focus("unowned")
                self.details.setHtml(self._unowned_html())
            elif t == "face":
                face = current.data(T.R_FACE)
                self._focus = Focus.of_faces([face], UNOWNED_COLOR)
                self.details.setHtml(self._face_html(face))
        self.redraw()

    def _context_menu(self, point) -> None:
        idx = self.tree.indexAt(point)
        if not idx.isValid():
            return
        self.tree.setCurrentIndex(idx)
        menu = QtWidgets.QMenu(self.tree)
        for move, label, shortcut, _svg in MOVES:
            act = menu.addAction(label)
            act.setShortcut(QtGui.QKeySequence(shortcut))
            act.setShortcutVisibleInContextMenu(True)
            act.triggered.connect(lambda _c=False, m=move: self._camera_move(m))
        whole = menu.addAction("Whole model")
        whole.triggered.connect(lambda _c=False: self._camera_move("model"))
        if idx.data(T.R_TYPE) == "feature":
            fid = idx.data(T.R_FID)
            menu.addSeparator()
            for verdict, label, key in (("correct", "Correct", "C"), ("wrong", "Wrong", "W"),
                                        ("unreviewed", "Unreviewed", "U")):
                act = menu.addAction(f"Mark {label.lower()}")
                act.setShortcut(QtGui.QKeySequence(key))
                act.setShortcutVisibleInContextMenu(True)
                act.setCheckable(True)
                act.setChecked(self.session.verdicts.get(fid) == verdict)
                act.triggered.connect(lambda _c=False, v=verdict: self.set_verdict(fid, v))
            really = menu.addMenu("Really a...")
            current = self.session.corrections.get(fid, {}).get("kind", "")
            for k in FeatureKind:
                act = really.addAction(k.value)
                act.setCheckable(True)
                act.setChecked(current == k.value)
                act.triggered.connect(lambda _c=False, kv=k.value: self.set_correction(fid, kv))
            if current:
                really.addSeparator()
                clear = really.addAction("Clear")
                clear.triggered.connect(lambda _c=False: self.set_correction(fid, ""))
            menu.addSeparator()
            sel = menu.addAction("Select its faces in FreeCAD")
            sel.triggered.connect(lambda _c=False: self._select_in_freecad(fid))
        elif idx.data(T.R_TYPE) in ("face", "unowned"):
            menu.addSeparator()
            faces = [idx.data(T.R_FACE)] if idx.data(T.R_TYPE) == "face" else \
                list(self.session.result.unowned_faces())
            pick = menu.addAction("Pick for a missed feature")
            pick.triggered.connect(lambda _c=False: self._add_picked(faces))
        menu.exec(self.tree.viewport().mapToGlobal(point))

    # -- Review in the tree -----------------------------------------------------

    def set_verdict(self, fid: int, verdict: str) -> None:
        if fid not in self.session.verdicts:
            return
        self.session.verdicts[fid] = verdict
        self._fill_tree(keep=True)
        self._refresh_review()

    def set_correction(self, fid: int, kind: str, subtype: Optional[str] = None) -> None:
        fix = self.session.corrections.setdefault(fid, {})
        fix["kind"] = kind
        if subtype is not None:
            fix["subtype"] = subtype
        if kind:
            self.session.verdicts[fid] = "wrong"
        self._fill_tree(keep=True)
        self._refresh_review()
        if self._focus.feature == fid:
            self.details.setHtml(self._feature_html(fid))

    def _current_fid(self) -> Optional[int]:
        idx = self.tree.currentIndex()
        return idx.data(T.R_FID) if idx.isValid() and idx.data(T.R_TYPE) == "feature" else None

    # -- Camera -------------------------------------------------------------------

    def _focus_faces(self) -> list[int]:
        f = self._focus
        if f.kind == "feature":
            feat = self.session.feature(f.feature)
            return list(feat.faces) if feat else []
        if f.kind == "kind":
            return [face for x in self.session.features() if x.kind == f.feature_kind for face in x.faces]
        if f.kind == "unowned" and self.session.result is not None:
            return list(self.session.result.unowned_faces())
        return list(f.faces)

    def _camera_move(self, move: str) -> None:
        if self.target_object is None:
            return
        try:
            if self._camera is None:
                from ..results.camera import ResultsCamera
                self._camera = ResultsCamera(self.target_object)
            refs = [("Face", face - 1) for face in self._focus_faces()]
            if move == "model" or not refs:
                self._camera.whole_model()
            elif move == "zoom":
                self._camera.zoom_to(refs)
            elif move == "face":
                self._camera.face(refs)
            else:
                self._camera.zoom_and_face(refs)
        except Exception as exc:
            App.Console.PrintLog(f"DFM bench: camera: {exc}\n")

    def _place_row_actions(self) -> None:
        try:
            viewport = self.tree.viewport()
        except RuntimeError:
            return
        index = self.tree.currentIndex()
        selected = index.isValid() and self.tree.selectionModel().isSelected(index)
        rect = self.tree.visualRect(index) if selected else QtCore.QRect()
        show = selected and rect.isValid() and viewport.rect().intersects(rect) and bool(self._focus_faces())
        old = self.delegate.actions_index
        if show:
            self.row_actions.set_icon_color(self.tree.palette().color(QtGui.QPalette.ColorRole.HighlightedText))
            self.row_actions.place_on(rect, viewport.width())
            self.delegate.actions_index = QtCore.QPersistentModelIndex(index)
            self.delegate.actions_width = self.row_actions.width() + 8
        else:
            self.row_actions.hide()
            self.delegate.actions_index = QtCore.QPersistentModelIndex()
            self.delegate.actions_width = 0
        if old != self.delegate.actions_index:
            viewport.update()

    # -- Drawing -------------------------------------------------------------------

    def _on_unowned_toggled(self, _on: bool) -> None:
        # the tree lists the faces in no feature only while they are shown
        self._fill_tree(keep=True)
        self.redraw()

    def redraw(self) -> None:
        if self.session.result is None or self.target_object is None or self._picking_model:
            return
        style = Style(colour_all=self.btn_colour.isChecked(), show_unowned=self.btn_unowned.isChecked(),
                      convexity=self.btn_edges.isChecked(), picked=list(self.session.picked))
        faces = face_colors(self.session.features(), self.session.result.unowned_faces(), self._focus, style)
        edges = edge_colors(self.session.result.edge_convexity) if style.convexity else {}
        self.scene.attach(self.target_object)
        self.scene.draw(faces, edges)
        self.picker.enabled = True

    # -- Picking in the 3D view ------------------------------------------------------

    def _on_pick(self, face: Optional[int], additive: bool) -> None:
        if self.session.result is None or self._picking_model:
            return
        if additive:
            if face is not None:
                self._add_picked([face], toggle=True)
            return
        if face is None:
            return
        self.show_face(face)

    def show_face(self, face: int) -> None:
        """Select what *face* belongs to; a face in no feature is selected itself."""
        owner = self.session.result.owner_of(face) if self.session.result else None
        if owner is not None and self.select_feature(owner.id):
            return
        idx = self._find(("face", face))
        if idx is not None:
            self.tree.scrollTo(idx)
            self.tree.setCurrentIndex(idx)
        else:
            self._focus = Focus.of_faces([face])
            self.details.setHtml(self._face_html(face))
            self.redraw()

    def _add_picked(self, faces, toggle: bool = False) -> None:
        picked = list(self.session.picked)
        for f in faces:
            if toggle and f in picked:
                picked.remove(f)
            elif f not in picked:
                picked.append(f)
        self.session.picked = sorted(picked)
        self._refresh_picked()
        self.redraw()

    # FreeCAD selection: picking the model, or a face while it is shown plainly.

    def addSelection(self, doc, obj, sub, pnt=None):  # noqa: N802 (FreeCAD API)
        if self._picking_model:
            o = None
            try:
                o = App.getDocument(doc).getObject(obj)
            except Exception:
                pass
            if o is not None:
                self._picking_model = False
                self.pb_object.setChecked(False)
                self.le_object.setPlaceholderText("No model selected")
                QtCore.QTimer.singleShot(0, lambda: self.set_target(o))
            return
        if self.target_object is None or obj != self.target_object.Name or not sub.startswith("Face"):
            return
        try:
            face = int(sub[4:])
        except ValueError:
            return
        if self.session.result is not None:
            self.show_face(face)

    def _select_in_freecad(self, fid: int) -> None:
        f = self.session.feature(fid)
        if f is None or self.target_object is None:
            return
        self.scene.plain()
        try:
            Gui.Selection.clearSelection()
            for face in f.faces:
                Gui.Selection.addSelection(self.target_object.Document.Name, self.target_object.Name, f"Face{face}")
        except Exception:
            pass

    # -- Details --------------------------------------------------------------------

    @staticmethod
    def _faces_links(faces, color: str = "") -> str:
        faces = list(faces)
        if not faces:
            return "none"
        links = ", ".join(f'<a href="face:{i}">Face{i}</a>' for i in faces[:40])
        more = f" and {len(faces) - 40} more" if len(faces) > 40 else ""
        all_link = f' &nbsp;<a href="faces:{",".join(map(str, faces))}">show</a>' if len(faces) > 1 else ""
        return links + more + all_link

    def _feature_html(self, fid: int) -> str:
        f = self.session.feature(fid)
        if f is None:
            return ""
        color = kind_color(f.kind)
        conf_c = T.confidence_color(f.confidence, read_options().min_confidence)
        verdict = self.session.verdicts.get(fid, "")
        fix = self.session.corrections.get(fid, {})
        out = [f'<p style="margin:0"><span style="color:{color}; font-size:15px">&#9679;</span> '
               f'<b style="font-size:14px">{html.escape(f.label)}</b> &nbsp;'
               f'<span style="background:{conf_c}; color:white">&nbsp;{f.confidence:.2f}&nbsp;</span>']
        if verdict in ("correct", "wrong"):
            vc = C_PASS if verdict == "correct" else C_ERROR
            out.append(f' &nbsp;<span style="color:{vc}"><b>{verdict}</b></span>')
        if fix.get("kind"):
            out.append(f' &nbsp;really a <b>{html.escape(fix["kind"])}</b>')
        out.append("</p>")
        if f.params.get("from_history"):
            out.append(f'<p style="color:{C_WARNING}">Made from the model\'s history only: the geometry '
                       "alone didn't give it. Untick \"Use the model's history\" to compare.</p>")
        rows = [("Faces", self._faces_links(f.faces))]
        if f.support_faces:
            rows.append(("Stands on", self._faces_links(f.support_faces)))
        if f.parent is not None:
            p = self.session.feature(f.parent)
            if p is not None:
                rows.append(("Part of", f'<a href="feature:{p.id}">{html.escape(p.label)}</a>'))
        if f.interacts_with:
            links = [f'<a href="feature:{i}">{html.escape(self.session.feature(i).label)}</a>'
                     for i in f.interacts_with if self.session.feature(i)]
            rows.append(("Interacts with", ", ".join(links)))
        out.append(self._table(rows))
        params = [(k, T._fmt(v)) for k, v in sorted(f.params.items())
                  if k not in ("from_history", "base_faces")]
        ann = self.session.annotations(fid)
        if params:
            out.append("<p style='margin:6px 0 2px 0'><b>Parameters</b></p>" + self._table(params, mono=True))
        if ann:
            out.append("<p style='margin:6px 0 2px 0'><b>For the chosen directions</b></p>"
                       + self._table([(k, T._fmt(v)) for k, v in sorted(ann.items())], mono=True))
        if f.evidence:
            items = []
            for e in f.evidence:
                if e.startswith("+ "):
                    items.append(f'<span style="color:{C_PASS}">&#10003;</span> {html.escape(e[2:])}')
                elif e.startswith("- "):
                    items.append(f'<span style="color:{C_WARNING}">&#9888;</span> {html.escape(e[2:])}')
                else:
                    items.append(html.escape(e))
            out.append("<p style='margin:6px 0 2px 0'><b>Why</b></p>" + "<br>".join(items))
        return "".join(out)

    @staticmethod
    def _table(rows, mono: bool = False) -> str:
        style = "font-family: monospace;" if mono else ""
        body = "".join(f'<tr><td style="padding-right:12px; color:gray">{html.escape(k)}</td>'
                       f'<td style="{style}">{v if not mono else html.escape(v)}</td></tr>' for k, v in rows)
        return f"<table cellspacing='0' cellpadding='1'>{body}</table>"

    def _kind_html(self, kind: FeatureKind) -> str:
        feats = [f for f in self.session.features() if f.kind == kind]
        rows = [(f.label, f'<a href="feature:{f.id}">{html.escape(T.summary(f)) or "show"}</a>') for f in feats]
        return (f'<p><span style="color:{kind_color(kind)}">&#9632;</span> <b>{len(feats)} '
                f'{html.escape(kind.value)} feature(s)</b></p>' + self._table(rows))

    def _unowned_html(self) -> str:
        r = self.session.result
        faces = r.unowned_faces() if r else []
        return (f'<p><span style="color:{UNOWNED_COLOR}">&#9632;</span> <b>{len(faces)} faces in no feature</b></p>'
                "<p>Faces of the stock, or of something the engine did not recognise. Ctrl+click faces in the "
                "3D view (or right-click here) to pick them, then add a missed feature on the Review tab.</p>"
                + self._faces_links(faces))

    def _face_html(self, face: int) -> str:
        r = self.session.result
        if r is None:
            return ""
        out = [f"<p><b>Face{face}</b></p>"]
        rows = []
        sig = r.face_signatures.get(face)
        if sig:
            c = ", ".join(f"{v:.3f}" for v in sig.centroid)
            rows.append(("Surface", f"{sig.kind}, area {sig.area:.4g} mm²"))
            rows.append(("Centroid", f"({c})"))
        owner = r.owner_of(face)
        rows.append(("Part of", f'<a href="feature:{owner.id}">{html.escape(owner.label)}</a>' if owner else "no feature"))
        users = [f for f in r.on_face(face) if owner is None or f.id != owner.id]
        if users:
            rows.append(("Supports", ", ".join(f'<a href="feature:{f.id}">{html.escape(f.label)}</a>' for f in users)))
        if self.session.directional and face in self.session.directional.face_draft:
            rows.append(("Draft", f"{self.session.directional.face_draft[face]:.3f}°"))
        out.append(self._table(rows))
        return "".join(out)

    def _overview_html(self) -> str:
        r = self.session.result
        if r is None:
            return ("<p>Pick a model and press <b>Recognise</b>.</p><p>Select a row to see that feature alone "
                    "on the model; click a face in the 3D view to find what it belongs to.</p>")
        counts: dict = {}
        for f in self.session.features():
            counts[f.kind] = counts.get(f.kind, 0) + 1
        legend = " &nbsp; ".join(f'<span style="color:{kind_color(k)}">&#9632;</span> {html.escape(k.value)} {n}'
                                 for k, n in counts.items())
        legend += f' &nbsp; <span style="color:{UNOWNED_COLOR}">&#9632;</span> no feature {len(r.unowned_faces())}'
        return f"<p>{legend}</p>{self._form_html()}<p>Select a row to see it alone on the model.</p>"

    def _form_html(self) -> str:
        """The part's form: how it could be modelled (base extrusion, ...)."""
        form = getattr(self.session.result, "form", None) or {}
        if not form:
            return ""
        names = {"extrusion": "an extrusion", "2.5d": "2.5D: an extrusion with extruded cuts and bosses",
                 "revolve": "a revolve (turned part)", "mixed": "mixed"}
        d = form.get("direction")
        axis = ""
        if d:
            big = max(range(3), key=lambda i: abs(d[i]))
            axis = (f" along {'+' if d[big] > 0 else chr(0x2212)}{'XYZ'[big]}" if abs(abs(d[big]) - 1) < 1e-6
                    else " along (" + ", ".join(f"{c:.3g}" for c in d) + ")")
        rows = [("Form", f"<b>{html.escape(names.get(form.get('form'), form.get('form', '')))}</b>{axis}")]
        if form.get("form") != "revolve":
            rows.append(("Covers", f"{100 * form.get('coverage', 0):.1f}% of the area, {form.get('levels', 0)} levels"
                         + (f", walls drafted up to {form['draft_deg']:.2g}°" if form.get("draft_deg") else "")))
        other = form.get("other_faces") or []
        if other:
            rows.append(("Not along it", self._faces_links(other)))
        return "<p style='margin:6px 0 2px 0'><b>Part</b></p>" + self._table(rows)

    def _on_link(self, url: QtCore.QUrl) -> None:
        text = url.toString()
        scheme, _, rest = text.partition(":")
        try:
            if scheme == "face":
                face = int(rest)
                self._focus = Focus.of_faces([face])
                self.redraw()
                self.details.setHtml(self._face_html(face))
            elif scheme == "faces":
                self._focus = Focus.of_faces(int(x) for x in rest.split(","))
                self.redraw()
            elif scheme == "feature":
                self.select_feature(int(rest))
        except ValueError:
            pass

    def _copy_json(self) -> None:
        if self.session.result is None:
            return
        d = self.session.result.to_dict()
        if self.session.directional is not None:
            d["directional"] = self.session.directional.to_dict()
        QtWidgets.QApplication.clipboard().setText(json.dumps(d, indent=1))

    # =====================================================================
    # Review tab
    # =====================================================================

    def _build_review(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        self.review_bar = QtWidgets.QProgressBar()
        self.review_bar.setTextVisible(True)
        v.addWidget(self.review_bar)
        self.review_state = muted_label("")
        self.review_state.setWordWrap(True)
        v.addWidget(self.review_state)
        tip = muted_label("Review in the Features tree: select a feature, then C (correct), W (wrong) or "
                          "U (unreviewed), or right-click for \"Really a...\".", small=True)
        tip.setWordWrap(True)
        v.addWidget(tip)
        row = QtWidgets.QHBoxLayout()
        rest = QtWidgets.QPushButton("Mark the rest correct")
        rest.setToolTip("Every feature still unreviewed becomes correct.")
        rest.clicked.connect(self._rest_correct)
        row.addWidget(rest)
        reset = QtWidgets.QPushButton("Reset")
        reset.clicked.connect(lambda: self._set_all("unreviewed"))
        row.addWidget(reset)
        row.addStretch()
        v.addLayout(row)

        v.addWidget(section_header("Missed features"))
        row = QtWidgets.QHBoxLayout()
        self.picked_label = QtWidgets.QLabel()
        self.picked_label.setWordWrap(True)
        self.picked_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        row.addWidget(self.picked_label, 1)
        clear = QtWidgets.QPushButton("Clear")
        clear.clicked.connect(lambda: self._clear_picked())
        row.addWidget(clear)
        v.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self.missed_kind = QtWidgets.QComboBox()
        self.missed_kind.addItems([k.value for k in FeatureKind])
        row.addWidget(self.missed_kind)
        self.missed_subtype = QtWidgets.QLineEdit()
        self.missed_subtype.setPlaceholderText("subtype (optional)")
        row.addWidget(self.missed_subtype, 1)
        add = QtWidgets.QPushButton("Add")
        add.setToolTip("Add a missed feature made of the picked faces.")
        add.clicked.connect(self._add_missed)
        row.addWidget(add)
        v.addLayout(row)
        self.missed_list = QtWidgets.QListWidget()
        self.missed_list.setMaximumHeight(90)
        self.missed_list.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.ActionsContextMenu)
        rem = QtGui.QAction("Remove", self.missed_list)
        rem.triggered.connect(self._remove_missed)
        self.missed_list.addAction(rem)
        self.missed_list.currentRowChanged.connect(self._missed_selected)
        v.addWidget(self.missed_list)

        v.addWidget(section_header("Save to the corpus"))
        form = QtWidgets.QFormLayout()
        self.case_name = QtWidgets.QLineEdit()
        self.case_name.setPlaceholderText("letters, digits and _")
        form.addRow("Case name", self.case_name)
        self.reviewer = QtWidgets.QLineEdit()
        form.addRow("Reviewed by", self.reviewer)
        self.notes = QtWidgets.QPlainTextEdit()
        self.notes.setMaximumHeight(54)
        form.addRow("Notes", self.notes)
        v.addLayout(form)
        row = QtWidgets.QHBoxLayout()
        self.save_state = muted_label("")
        self.save_state.setWordWrap(True)
        row.addWidget(self.save_state, 1)
        save = QtWidgets.QPushButton("Save to corpus")
        save.setToolTip("Write the part (BREP + STEP) and this review to the corpus folder. Approved cases are "
                        "checked by every suite run from then on.")
        save.clicked.connect(self._save_golden)
        row.addWidget(save)
        v.addLayout(row)

        v.addWidget(section_header("Corpus"))
        self.corpus_label = muted_label("", small=True)
        self.corpus_label.setWordWrap(True)
        v.addWidget(self.corpus_label)
        self.corpus_list = QtWidgets.QListWidget()
        self.corpus_list.setMaximumHeight(120)
        self.corpus_list.itemDoubleClicked.connect(self._open_golden)
        v.addWidget(self.corpus_list)
        v.addStretch(1)
        self._refresh_corpus()
        return w

    def _refresh_review(self) -> None:
        if not hasattr(self, "review_bar"):
            return
        done, wrong, n = self.session.review_counts()
        self.review_bar.setMaximum(max(n, 1))
        self.review_bar.setValue(done)
        self.review_bar.setFormat(f"{done} / {n} reviewed" if n else "Nothing to review")
        missed = len(self.session.missed)
        state = f"{wrong} wrong, {missed} missed."
        if n and done < n:
            state += " Saved as unreviewed until every feature is."
        self.review_state.setText(state if n else "Recognise a model first.")
        if self.session.result is not None and not self.case_name.text():
            self.case_name.setText("".join(c if c.isalnum() else "_" for c in self.session.label).strip("_"))
        self.missed_list.clear()
        for m in self.session.missed:
            item = QtWidgets.QListWidgetItem(f"{m.kind.value}{' (' + m.subtype + ')' if m.subtype else ''}: "
                                             + ", ".join(f"Face{i}" for i in m.faces))
            item.setForeground(QtGui.QBrush(QtGui.QColor(kind_color(m.kind))))
            self.missed_list.addItem(item)
        self._refresh_picked()

    def _refresh_picked(self) -> None:
        if not hasattr(self, "picked_label"):
            return
        p = self.session.picked
        if p:
            self.picked_label.setText(f'<span style="color:{PICKED_COLOR}">&#9632;</span> Picked: '
                                      + ", ".join(f"Face{i}" for i in p))
        else:
            self.picked_label.setText("Ctrl+click faces in the 3D view to pick a missed feature's faces.")

    def _clear_picked(self) -> None:
        self.session.picked = []
        self._refresh_picked()
        self.redraw()

    def _set_all(self, verdict: str) -> None:
        for fid in list(self.session.verdicts):
            self.session.verdicts[fid] = verdict
        self._fill_tree(keep=True)
        self._refresh_review()

    def _rest_correct(self) -> None:
        for fid, v in list(self.session.verdicts.items()):
            if v == "unreviewed":
                self.session.verdicts[fid] = "correct"
        self._fill_tree(keep=True)
        self._refresh_review()

    def _selected_faces(self) -> list[int]:
        """Picked faces, else faces selected in FreeCAD (model shown plainly)."""
        if self.session.picked:
            return list(self.session.picked)
        faces = []
        try:
            for s in Gui.Selection.getSelectionEx():
                if self.target_object is None or s.ObjectName != self.target_object.Name:
                    continue
                for name in s.SubElementNames:
                    if name.startswith("Face"):
                        try:
                            faces.append(int(name[4:]))
                        except ValueError:
                            pass
        except Exception:
            pass
        return sorted(set(faces))

    def _add_missed(self) -> None:
        faces = self._selected_faces()
        if not faces:
            self.save_state.setText("Pick the missed feature's faces first (Ctrl+click in the 3D view).")
            return
        self.session.missed.append(Missed(FeatureKind(self.missed_kind.currentText()), faces,
                                          self.missed_subtype.text().strip()))
        self.session.picked = []
        self.missed_subtype.clear()
        self._refresh_review()
        self.redraw()

    def _remove_missed(self) -> None:
        row = self.missed_list.currentRow()
        if 0 <= row < len(self.session.missed):
            del self.session.missed[row]
            self._refresh_review()

    def _missed_selected(self, row: int) -> None:
        if 0 <= row < len(self.session.missed):
            m = self.session.missed[row]
            self._focus = Focus.of_faces(m.faces, kind_color(m.kind))
            self.redraw()

    def _save_golden(self) -> None:
        if self.session.result is None:
            self.save_state.setText("Recognise a model first.")
            return
        name = "".join(c if c.isalnum() or c == "_" else "_" for c in self.case_name.text().strip())
        if not name:
            self.save_state.setText("Give the case a name.")
            return
        folder = corpus_dir()
        from ...core.recognition.testing.corpus import GOLDEN_SUFFIX
        target = os.path.join(folder, name + GOLDEN_SUFFIX)
        if os.path.exists(target):
            answer = QtWidgets.QMessageBox.question(self.form, _TITLE, f"{name} is already in the corpus. Replace it?")
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        try:
            path = self.session.save(folder, name, self.reviewer.text().strip(), self.notes.toPlainText().strip())
        except Exception as exc:
            self.save_state.setText(f"Could not save: {exc}")
            return
        state = "approved" if self.session.reviewed() else "unreviewed (finish the review to approve it)"
        self.save_state.setText(f"Saved {os.path.basename(path)}: {state}")
        self._refresh_corpus()

    def _refresh_corpus(self) -> None:
        from ...core.recognition.testing.corpus import GOLDEN_SUFFIX, list_goldens, load_golden

        folder = corpus_dir()
        self.corpus_label.setText(f"{folder}. Double-click a case to open it with its review.")
        self.corpus_list.clear()
        if not os.path.isdir(folder):
            return
        for path in list_goldens(folder):
            try:
                status = load_golden(path).get("status", "?")
            except Exception:
                status = "unreadable"
            item = QtWidgets.QListWidgetItem(f"{os.path.basename(path)[: -len(GOLDEN_SUFFIX)]}  ·  {status}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, path)
            if status != "approved":
                item.setForeground(QtGui.QBrush(QtGui.QColor(C_WARNING)))
            self.corpus_list.addItem(item)

    def _open_golden(self, item) -> None:
        from ...core.recognition.testing import shapes as B
        from ...core.recognition.testing.corpus import load_golden, shape_path

        path = item.data(QtCore.Qt.ItemDataRole.UserRole)
        try:
            golden = load_golden(path)
            shape = B.read_brep(shape_path(path))
            obj = _show_ocp_shape(shape, golden.get("name", "case"))
        except Exception as exc:
            self.save_state.setText(f"Could not open: {exc}")
            return
        self.set_target(obj, _ocp_from_object(obj), path)
        self.case_name.setText(golden.get("name", ""))
        self.reviewer.setText(golden.get("reviewed_by", ""))
        self.notes.setPlainText(golden.get("notes", ""))
        self.recognise()
        n = self.session.apply_golden(golden)
        self._fill_tree(keep=True)
        self._refresh_review()
        self.save_state.setText(f"Opened {os.path.basename(path)}: {n} verdicts carried over.")
        self.tabs.setCurrentIndex(0)

    # =====================================================================
    # Suite tab
    # =====================================================================

    def _build_suite(self) -> QtWidgets.QWidget:
        from ...core.recognition.testing import fixtures as FX
        from ...core.recognition.testing.run import VARIANTS

        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)
        box = QtWidgets.QGroupBox("Cases")
        grid = QtWidgets.QGridLayout(box)
        grid.setContentsMargins(8, 8, 8, 8)
        self.suite_filter = QtWidgets.QLineEdit()
        self.suite_filter.setPlaceholderText("name contains")
        self.suite_filter.setClearButtonEnabled(True)
        grid.addWidget(self.suite_filter, 0, 0, 1, 2)
        self.suite_tag = QtWidgets.QComboBox()
        self.suite_tag.addItem("All tags")
        self.suite_tag.addItems(sorted({t for c in FX.CASES for t in c.tags}))
        grid.addWidget(self.suite_tag, 0, 2)
        self.variant_boxes = {}
        vrow = QtWidgets.QHBoxLayout()
        for name in VARIANTS:
            b = QtWidgets.QCheckBox(name)
            b.setChecked(name == "base")
            b.setToolTip({"base": "As built", "moved": "Rotated and moved",
                          "nurbs": "Every surface converted to B-splines",
                          "step": "Written to STEP and read back"}.get(name, name))
            self.variant_boxes[name] = b
            vrow.addWidget(b)
        vrow.addStretch()
        grid.addLayout(vrow, 1, 0, 1, 3)
        self.include_corpus = QtWidgets.QCheckBox("Include the corpus")
        self.include_corpus.setChecked(True)
        grid.addWidget(self.include_corpus, 2, 0, 1, 3)
        grid.setColumnStretch(0, 1)
        v.addWidget(box)

        row = QtWidgets.QHBoxLayout()
        self.suite_run = QtWidgets.QPushButton()
        style_primary_button(self.suite_run, "Run suite")
        self.suite_run.clicked.connect(self.run_suite)
        row.addWidget(self.suite_run, 1)
        self.suite_stop = QtWidgets.QPushButton("Stop")
        self.suite_stop.setEnabled(False)
        self.suite_stop.clicked.connect(self._stop_suite)
        row.addWidget(self.suite_stop)
        export = QtWidgets.QPushButton("Export fixtures...")
        export.setToolTip("Write every fixture part (BREP and STEP) with its ground truth to a folder.")
        export.clicked.connect(self._export_fixtures)
        row.addWidget(export)
        v.addLayout(row)

        self.suite_progress = QtWidgets.QProgressBar()
        self.suite_progress.hide()
        v.addWidget(self.suite_progress)
        self.suite_banner = Banner()
        v.addWidget(self.suite_banner)

        self.suite_tree = QtWidgets.QTreeWidget()
        self.suite_tree.setHeaderLabels(["Case", "Variant", "ms"])
        self.suite_tree.setRootIsDecorated(True)
        self.suite_tree.setUniformRowHeights(True)
        self.suite_tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.suite_tree.header().setStretchLastSection(False)
        self.suite_tree.currentItemChanged.connect(self._suite_selected)
        self.suite_tree.itemDoubleClicked.connect(self._open_case)
        v.addWidget(self.suite_tree, 2)
        self.suite_details = QtWidgets.QTextBrowser()
        self.suite_details.setMaximumHeight(160)
        v.addWidget(self.suite_details, 1)
        self.kinds_table = QtWidgets.QTableWidget(0, 6)
        self.kinds_table.setHorizontalHeaderLabels(["Kind", "Found", "Wrong", "Missed", "Precision", "Recall"])
        self.kinds_table.verticalHeader().hide()
        self.kinds_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.kinds_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.kinds_table.setMaximumHeight(170)
        self.kinds_table.hide()
        v.addWidget(self.kinds_table)
        hint = muted_label("Double-click a case to open its part and inspect it.", small=True)
        v.addWidget(hint)
        return w

    def run_suite(self) -> None:
        from ...core.recognition.testing import fixtures as FX
        from ...core.recognition.testing.run import run_suite

        tag = self.suite_tag.currentText()
        cases = FX.select_cases(tags=None if tag == "All tags" else [tag], pattern=self.suite_filter.text().strip())
        variants = [n for n, b in self.variant_boxes.items() if b.isChecked()] or ["base"]
        self._stop = False
        self.suite_run.setEnabled(False)
        self.suite_stop.setEnabled(True)
        self.suite_tree.clear()
        self.suite_progress.show()

        def progress(name, i, n):
            self.suite_progress.setMaximum(max(1, n))
            self.suite_progress.setValue(i)
            self.suite_progress.setFormat(f"{name}  %v / %m")
            QtWidgets.QApplication.processEvents()

        try:
            rep = run_suite(cases, variants, corpus_dir=corpus_dir() if self.include_corpus.isChecked() else None,
                            progress=progress, keep_results=True, check_abort=lambda: self._stop)
        finally:
            self.suite_run.setEnabled(True)
            self.suite_stop.setEnabled(False)
            self.suite_progress.hide()
        self._suite_report = rep
        self._fill_suite(rep)

    def _stop_suite(self) -> None:
        self._stop = True

    def _fill_suite(self, rep) -> None:
        self.suite_tree.clear()
        failed = [(i, c) for i, c in enumerate(rep.cases) if not c.passed]
        passed = [(i, c) for i, c in enumerate(rep.cases) if c.passed]
        for label, members, color in (("Failed", failed, C_ERROR), ("Passed", passed, C_PASS)):
            if not members:
                continue
            g = QtWidgets.QTreeWidgetItem([f"{label} ({len(members)})", "", ""])
            g.setForeground(0, QtGui.QBrush(QtGui.QColor(color)))
            font = g.font(0)
            font.setBold(True)
            g.setFont(0, font)
            for i, c in members:
                it = QtWidgets.QTreeWidgetItem([c.name, c.variant, f"{c.seconds * 1000:.0f}"])
                it.setData(0, QtCore.Qt.ItemDataRole.UserRole, i)
                it.setTextAlignment(2, QtCore.Qt.AlignmentFlag.AlignRight)
                if not c.passed and c.failures:
                    it.setToolTip(0, "\n".join(c.failures[:6]))
                g.addChild(it)
            self.suite_tree.addTopLevelItem(g)
            g.setExpanded(label == "Failed" or not failed)
        n_pass = len(passed)
        n = len(rep.cases)
        self.suite_banner.set("Passed" if not failed else "Failed",
                              f"{n_pass} / {n} cases in {rep.seconds:.1f} s", C_PASS if not failed else C_ERROR)
        tot = rep.totals()
        self.kinds_table.setRowCount(len(tot))
        for row, (k, t) in enumerate(tot.items()):
            vals = [k, t["tp"], t["fp"], t["fn"], f"{t['precision']:.3f}", f"{t['recall']:.3f}"]
            for col, val in enumerate(vals):
                item = QtWidgets.QTableWidgetItem(str(val))
                if col >= 1:
                    item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
                if col == 0:
                    item.setForeground(QtGui.QBrush(QtGui.QColor(kind_color(_kind_or_none(k)))))
                if (col == 2 and t["fp"]) or (col == 3 and t["fn"]):
                    item.setForeground(QtGui.QBrush(QtGui.QColor(C_ERROR)))
                self.kinds_table.setItem(row, col, item)
        self.kinds_table.setVisible(bool(tot))

    def suite_rows(self) -> list[tuple[str, str, bool]]:
        """(case, variant, passed) for every row, in order (for tests)."""
        out = []
        for g in range(self.suite_tree.topLevelItemCount()):
            group = self.suite_tree.topLevelItem(g)
            for i in range(group.childCount()):
                c = self._suite_report.cases[group.child(i).data(0, QtCore.Qt.ItemDataRole.UserRole)]
                out.append((c.name, c.variant, c.passed))
        return out

    def _suite_selected(self, item, _previous=None) -> None:
        if self._suite_report is None or item is None:
            return
        i = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if i is None:
            return
        c = self._suite_report.cases[i]
        color = C_PASS if c.passed else C_ERROR
        text = [f"<b>{html.escape(c.name)}</b> [{html.escape(c.variant)}] "
                f"<span style='color:{color}'><b>{'pass' if c.passed else 'FAIL'}</b></span>"
                f" · {c.face_count} faces · {c.seconds * 1000:.0f} ms"]
        text += [f"<span style='color:{C_ERROR}'>{html.escape(f)}</span>" for f in c.failures]
        text += [html.escape(n) for n in c.notes if not n.startswith("Traceback")]
        self.suite_details.setHtml("<br>".join(text))

    def _open_case(self, item, _col=0) -> None:
        from ...core.recognition.testing import fixtures as FX
        from ...core.recognition.testing import shapes as B
        from ...core.recognition.testing.corpus import list_goldens, load_golden, shape_path
        from ...core.recognition.testing.run import make_variant

        if self._suite_report is None:
            return
        i = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if i is None:
            return
        c = self._suite_report.cases[i]
        path = None
        try:
            if c.variant == "corpus":
                path = next(p for p in list_goldens(corpus_dir()) if os.path.basename(p).startswith(c.name + "."))
                shape = B.read_brep(shape_path(path))
                source = path
            else:
                shape = make_variant(FX.by_name(c.name).build(), c.variant)
                source = f"fixture {c.name} [{c.variant}]"
            obj = _show_ocp_shape(shape, f"{c.name}_{c.variant}")
        except Exception as exc:
            self.suite_banner.set("Could not open the case", html.escape(str(exc)), C_ERROR)
            return
        self.set_target(obj, _ocp_from_object(obj), source)
        self.tabs.setCurrentIndex(0)
        self.recognise()
        if path is not None:
            self.session.apply_golden(load_golden(path))
            self._fill_tree(keep=True)
            self._refresh_review()

    def _export_fixtures(self) -> None:
        from ...core.recognition.testing.corpus import export_fixtures

        folder = QtWidgets.QFileDialog.getExistingDirectory(self.form, "Export fixtures to")
        if not folder:
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            n = export_fixtures(folder)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.suite_banner.set("Exported", html.escape(f"{n} fixtures to {folder}"), C_ACCENT)

    # =====================================================================
    # Task panel
    # =====================================================================

    def getStandardButtons(self):  # noqa: N802 (FreeCAD API)
        return QtWidgets.QDialogButtonBox.StandardButton.Close

    def _cleanup(self) -> None:
        if self._observing:
            try:
                Gui.Selection.removeObserver(self)
            except Exception:
                pass
            self._observing = False
        self.picker.detach()
        self.scene.remove()

    def reject(self):
        self._cleanup()
        try:
            Gui.Control.closeDialog()
        except Exception:
            pass
        return True

    def accept(self):
        return self.reject()


def _kind_or_none(name: str):
    try:
        return FeatureKind(name)
    except ValueError:
        return None


class _KeyFilter(QtCore.QObject):
    """C / W / U review the current feature; Z / F / Shift+F / Home move the camera."""

    def __init__(self, panel: RecognitionBenchPanel):
        super().__init__(panel.tree)
        self.panel = panel

    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        if event.type() != QtCore.QEvent.Type.KeyPress:
            return False
        mods = event.modifiers() & ~QtCore.Qt.KeyboardModifier.KeypadModifier
        plain = mods == QtCore.Qt.KeyboardModifier.NoModifier
        shift = mods == QtCore.Qt.KeyboardModifier.ShiftModifier
        key = event.key()
        p = self.panel
        fid = p._current_fid()
        if plain and fid is not None and key in (QtCore.Qt.Key.Key_C, QtCore.Qt.Key.Key_W, QtCore.Qt.Key.Key_U):
            verdict = {QtCore.Qt.Key.Key_C: "correct", QtCore.Qt.Key.Key_W: "wrong",
                       QtCore.Qt.Key.Key_U: "unreviewed"}[key]
            p.set_verdict(fid, verdict)
            if verdict != "unreviewed":
                self._next(p)
            return True
        if key == QtCore.Qt.Key.Key_Z and plain:
            p._camera_move("zoom")
            return True
        if key == QtCore.Qt.Key.Key_F and (plain or shift):
            p._camera_move("zoom_face" if shift else "face")
            return True
        if key == QtCore.Qt.Key.Key_Home and plain:
            p._camera_move("model")
            return True
        return False

    @staticmethod
    def _next(p: RecognitionBenchPanel) -> None:
        """Move to the next feature row, so a review is a run of key presses."""
        tree = p.tree
        idx = tree.indexBelow(tree.currentIndex())
        while idx.isValid() and idx.data(T.R_TYPE) != "feature":
            if idx.data(T.R_TYPE) in ("group",) and not tree.isExpanded(idx):
                tree.expand(idx)
            idx = tree.indexBelow(idx)
        if idx.isValid():
            tree.setCurrentIndex(idx)
