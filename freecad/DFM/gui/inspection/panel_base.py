# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""What the Symmetry Check and Compare Bodies panels have in common.

Both are laid out as the workbench's other task panels::

    verdict banner (after a check)
    Model, or Bodies                     } subclass
    Settings                             }
    Differences, or Changes: a tree like the results panel's
    Display: Differences | map, and what to show
    [▶ Run]  [Clear]
    progress bar (while running)

and both show differences the same way: colored on the model, the blue
info icon on each, a card on a leader line for the selected one, the
camera zooming to it, and a key in the 3D view whose arrows step through
them. Right-click a difference to ignore it: it greys out and stops
counting against the verdict. The map colors every point by how far off
it is.
"""

from __future__ import annotations

import math
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ...app.contour.colormap import COLORMAPS
from ...core.inspection import geometry as G
from ..results.tabs.common import MutedLabel
from ..utils import resources  # noqa: F401  (":/icons/...")
from ..utils.colormap_combo import populate_colormap_combo
from ..utils.direction_row import PICK_SVG, ViewSwitch, icon_button, style_primary_button
from ..utils.picking import CrossCursorOverride
from ..utils.qt_filters import EnterKeyFilter, EscapeFilter
from ..utils.view import get_view_widget
from . import document as D
from .card import DifferenceOverlay
from .map_view import MapView, build_mesh
from .scene import InspectScene, safe_remove
from .widgets import DifferenceTree, VerdictBanner

# Where the key in the 3D view was left, kept for the session.
_LEGEND_STATE: dict = {}


def ocp_shape(obj):
    """The OCP shape of a document object (patched by the tests)."""
    from ...core.utils.conversion import freecad_to_ocp

    return freecad_to_ocp(obj.Shape)


def recognition_for(shape_occ, obj):
    """Recognised features, to name differences; None if recognition fails."""
    try:
        from ...app.recognition_service import features_for

        return features_for(shape_occ, obj)
    except Exception as exc:
        App.Console.PrintLog(f"DFM: feature recognition skipped ({exc}).\n")
        return None


def has_shape(obj) -> bool:
    shape = getattr(obj, "Shape", None)
    try:
        return shape is not None and not shape.isNull() and bool(shape.Solids)
    except Exception:
        return False


def grid(box: QtWidgets.QGroupBox) -> QtWidgets.QGridLayout:
    """A group's grid: labels in the left half, controls in the right, as in the contour tools."""
    g = QtWidgets.QGridLayout(box)
    g.setContentsMargins(8, 8, 8, 8)
    g.setHorizontalSpacing(6)
    g.setVerticalSpacing(6)
    g.setColumnStretch(0, 1)
    g.setColumnStretch(1, 1)
    return g


def combo(tip: str = "") -> QtWidgets.QComboBox:
    c = QtWidgets.QComboBox()
    c.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(8)
    c.setToolTip(tip)
    return c


def spin(value: float, suffix: str = " mm", lo: float = 0.0, hi: float = 1e6, step: float = 0.01,
         decimals: int = 2) -> QtWidgets.QDoubleSpinBox:
    sb = QtWidgets.QDoubleSpinBox()
    sb.setRange(lo, hi)
    sb.setDecimals(decimals)
    sb.setSingleStep(step)
    sb.setSuffix(suffix)
    sb.setValue(value)
    sb.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
    return sb


def row_widget(*widgets, stretch_first: bool = True) -> QtWidgets.QWidget:
    """Widgets side by side in one grid cell: [combo][pick] or [name][pick][swap]."""
    w = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    for i, child in enumerate(widgets):
        row.addWidget(child, 1 if i == 0 and stretch_first else 0)
    return w


class ModelRow(QtWidgets.QWidget):
    """The model's name, read-only, and a button to pick it in the 3D view
    (and, for B, the swap button after it)."""

    pick_clicked = QtCore.Signal()

    def __init__(self, tip: str, extra: Optional[QtWidgets.QWidget] = None, parent=None):
        super().__init__(parent)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.edit = QtWidgets.QLineEdit()
        self.edit.setReadOnly(True)
        self.edit.setPlaceholderText("No model selected")
        self.edit.setToolTip(tip)
        self.button = icon_button(PICK_SVG, tip, checkable=True)
        self.button.clicked.connect(lambda _c=False: self.pick_clicked.emit())
        row.addWidget(self.edit, 1)
        row.addWidget(self.button)
        if extra is not None:
            row.addWidget(extra)

    def set_object(self, obj) -> None:
        self.edit.setText(obj.Label if obj is not None else "")
        self.edit.setCursorPosition(0)

    def set_picking(self, on: bool, obj=None) -> None:
        self.button.setChecked(on)
        if on:
            self.edit.setText("")
            self.edit.setPlaceholderText("Click a model in the 3D view")
        else:
            self.edit.setPlaceholderText("No model selected")
            self.set_object(obj)


class InspectPanel:
    """Base task panel. Subclasses set the class constants and fill in the hooks."""

    TOOL = ""
    TITLE = ""
    ICON = ""
    RUN_TEXT = "Run"
    LIST_TITLE = "Differences"
    LEGEND_TITLE = ""
    MAP_TITLE = "Deviation"
    MAP_NO_VALUE = "Unchanged"
    MAP_COLORMAP = "Turbo"
    MAP_MODE_NAME = "Map"

    def __init__(self, saved=None):
        self.saved = saved
        self.result = None
        self._selected = None
        self._hovered = None
        self._ignored: set[str] = set(getattr(saved, "Ignored", None) or [])
        self._settings_changed = False
        self._out_of_date = False
        self._scene: Optional[InspectScene] = None
        self._overlay = DifferenceOverlay()
        self._overlay.ignore_requested = lambda d: self.set_ignored([d], not d.ignored)
        self._map: Optional[MapView] = None
        self._map_cache = None
        self._legend = None
        self._muted: frozenset = frozenset()
        self._camera = None
        self.picking: Optional[str] = None
        self._pick_handlers: dict[str, Callable[[], bool]] = {}
        self._cursor = CrossCursorOverride()
        self._closed = False

        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(self.TITLE)
        self.form.setWindowIcon(QtGui.QIcon(self.ICON))
        self.root = QtWidgets.QVBoxLayout(self.form)
        self.root.setContentsMargins(6, 6, 6, 6)
        self.root.setSpacing(8)
        self._enter_filter = EnterKeyFilter(self.form)
        self.banner = VerdictBanner()
        self.banner.hide()
        self.root.addWidget(self.banner)
        self.build_inputs()
        self._build_list()
        self._build_display()
        self._build_run_row()
        self.root.addStretch(1)
        self._escape_filter = EscapeFilter(self._on_escape)
        self.form.installEventFilter(self._escape_filter)
        Gui.Selection.addObserver(self)
        if saved is not None:
            try:
                saved.Proxy._live_panel = self
            except Exception:
                pass
            self.load_settings(dict(getattr(saved, "Settings", None) or {}))
            self.load_saved(saved)
        else:
            self.auto_select()
        self.refresh_run_state()
        if saved is not None and self.ready() and not self._out_of_date:
            QtCore.QTimer.singleShot(0, self.run)

    # -- hooks ------------------------------------------------------------------

    def build_inputs(self) -> None:
        """Add the Model (or Bodies) and Settings groups to ``self.root``."""
        raise NotImplementedError

    def display_checks(self) -> list[tuple[str, str, str, bool]]:
        """Display check boxes: (attribute, text, tip, kept in map mode)."""
        return []

    def auto_select(self) -> None:
        pass

    def ready(self) -> bool:
        return False

    def not_ready_reason(self) -> str:
        return "Select a model first."

    def compute(self, progress):
        """Run the check; returns the result."""
        raise NotImplementedError

    def verdict(self) -> tuple[str, str, str]:
        """(kind, word, detail) for the banner."""
        raise NotImplementedError

    def list_groups(self) -> list:
        """The tree's :class:`Group` rows for the result."""
        raise NotImplementedError

    def categories(self) -> list:
        """The key's rows (contour Category objects)."""
        return []

    def category_of(self, difference) -> int:
        return 1

    def shares(self) -> dict[int, float]:
        return {}

    def after_result(self) -> None:
        """The result is in: anything else to update."""

    def draw(self) -> None:
        """Draw the current result in the 3D view (Differences mode)."""

    def shown_object(self):
        """The object the differences and the map are drawn on."""
        raise NotImplementedError

    def shown_shape(self):
        raise NotImplementedError

    def map_values(self, points, progress):
        raise NotImplementedError

    def map_format(self, value: float) -> str:
        return f"{value:.2f} mm"

    def map_range(self, values) -> tuple[float, float]:
        finite = [v for v in values if math.isfinite(v)]
        return (0.0, max(finite) if finite else 1.0)

    def settings(self) -> dict:
        return {}

    def load_settings(self, settings: dict) -> None:
        pass

    def load_saved(self, saved) -> None:
        pass

    def rotational(self) -> bool:
        return False

    def saved_objects(self) -> tuple:
        """(source, reference) for the saved check."""
        return (None, None)

    def failure_hint(self, exc: Exception) -> str:
        return "the boolean operation failed. Try a larger tolerance, such as 0.05 mm."

    def after_clear(self) -> None:
        pass

    def after_close(self) -> None:
        pass

    # -- layout -------------------------------------------------------------------

    def _build_list(self) -> None:
        self.list_box = QtWidgets.QGroupBox(self.LIST_TITLE)
        lay = QtWidgets.QVBoxLayout(self.list_box)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        self.tree = DifferenceTree()
        self.tree.selected.connect(self.select)
        self.tree.activated.connect(self.zoom_to)
        self.tree.zoom_requested.connect(self._select_and_zoom)
        self.tree.hovered.connect(self._on_hovered)
        self.tree.ignore_requested.connect(self.set_ignored)
        lay.addWidget(self.tree)
        self.hint = MutedLabel("Click one to zoom to it. Right-click to ignore it.")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        self.root.addWidget(self.list_box)
        self.list_box.hide()

    def _build_display(self) -> None:
        self.display_box = QtWidgets.QGroupBox("Display")
        g = grid(self.display_box)
        self.mode = ViewSwitch("Differences", self.MAP_MODE_NAME)
        self.mode.buttons[0].setToolTip("The differences on the model, with a card for the one you pick")
        self.mode.buttons[1].setToolTip("Color every point by how far it is from where it should be")
        self.mode.toggled.connect(self._on_mode)
        g.addWidget(self.mode, 0, 0, 1, 2)
        g.setRowMinimumHeight(1, 2)
        row = 2
        self._checks: list[tuple[QtWidgets.QCheckBox, bool]] = []
        for attr, text, tip, in_map in self.display_checks():
            cb = QtWidgets.QCheckBox(text)
            cb.setChecked(True)
            cb.setToolTip(tip)
            cb.toggled.connect(lambda _c: self.redraw())
            setattr(self, attr, cb)
            self._checks.append((cb, in_map))
            g.addWidget(cb, row, 1)
            row += 1
        self.lbl_cmap = QtWidgets.QLabel("Color map")
        self.cb_cmap = QtWidgets.QComboBox()
        populate_colormap_combo(self.cb_cmap, COLORMAPS.keys())
        self.cb_cmap.setCurrentText(self.MAP_COLORMAP)
        self.cb_cmap.currentIndexChanged.connect(self._on_map_style)
        g.addWidget(self.lbl_cmap, row, 0)
        g.addWidget(self.cb_cmap, row, 1)
        self.lbl_range = QtWidgets.QLabel("Range")
        self.sb_lo = spin(0.0, lo=-1e6)
        self.sb_hi = spin(1.0, lo=-1e6)
        for sb in (self.sb_lo, self.sb_hi):
            sb.installEventFilter(self._enter_filter)
            sb.valueChanged.connect(self._on_map_style)
        to = QtWidgets.QLabel("to")
        self.range_widget = row_widget(self.sb_lo, to, self.sb_hi, stretch_first=False)
        layout = self.range_widget.layout()
        layout.setStretch(0, 1)
        layout.setStretch(2, 1)
        g.addWidget(self.lbl_range, row + 1, 0)
        g.addWidget(self.range_widget, row + 1, 1)
        self.cb_smooth = QtWidgets.QCheckBox("Smooth shading")
        self.cb_smooth.setChecked(True)
        self.cb_smooth.toggled.connect(lambda _c: self._redraw_map())
        g.addWidget(self.cb_smooth, row + 2, 1)
        self._map_widgets = (self.lbl_cmap, self.cb_cmap, self.lbl_range, self.range_widget, self.cb_smooth)
        self.root.addWidget(self.display_box)
        self._update_display_widgets()

    def _build_run_row(self) -> None:
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        self.run_button = QtWidgets.QPushButton()
        style_primary_button(self.run_button, self.RUN_TEXT)
        self.run_button.clicked.connect(self.run)
        self.clear_button = QtWidgets.QPushButton("Clear")
        self.clear_button.setMinimumHeight(30)
        self.clear_button.setEnabled(False)
        self.clear_button.setToolTip("Clear the result from the panel and the 3D view")
        self.clear_button.clicked.connect(self.clear)
        row.addWidget(self.run_button, 1)
        row.addWidget(self.clear_button, 1)
        self.root.addLayout(row)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.progress.setRange(0, 1000)
        self.progress.hide()
        self.root.addWidget(self.progress)

    def in_map(self) -> bool:
        return self.mode.isChecked()

    def _update_display_widgets(self) -> None:
        in_map = self.in_map()
        for w in self._map_widgets:
            w.setVisible(in_map)
        for cb, keep in self._checks:
            cb.setVisible(not in_map or keep)

    # -- picking ---------------------------------------------------------------------

    def addSelection(self, *args):  # noqa: N802
        if self.picking:
            QtCore.QTimer.singleShot(30, self._process_pick)

    def removeSelection(self, *args):  # noqa: N802
        pass

    def clearSelection(self, *args):  # noqa: N802
        pass

    def setSelection(self, *args):  # noqa: N802
        pass

    def setPreselection(self, *args):  # noqa: N802
        pass

    def removePreselection(self, *args):  # noqa: N802
        pass

    def start_pick(self, mode: str) -> None:
        """Pick *mode* from the current selection if it has what's needed, else
        wait for clicks in the 3D view. Starting the same pick again cancels it."""
        if self.picking == mode:
            self.end_pick()
            return
        if self.picking is not None:
            self.end_pick()
        if self._pick_handlers[mode]():
            Gui.Selection.clearSelection()
            self.on_pick_ended(mode, True)
            self.refresh_run_state()
            return
        self.picking = mode
        self._cursor.push()
        self.on_pick_started(mode)
        self.refresh_run_state()

    def end_pick(self, done: bool = False) -> None:
        was = self.picking
        self.picking = None
        if was is not None:
            self._cursor.pop()
            self.on_pick_ended(was, done)
        self.refresh_run_state()

    def on_pick_started(self, mode: str) -> None:
        pass

    def on_pick_ended(self, mode: str, done: bool) -> None:
        pass

    def _process_pick(self) -> None:
        mode = self.picking
        if mode is None:
            return
        if self._pick_handlers[mode]():
            Gui.Selection.clearSelection()
            self.end_pick(done=True)

    def _on_escape(self) -> bool:
        if self.picking:
            self.end_pick()
            return True
        if self._selected is not None:
            self.select(None)
            return True
        return False

    # -- running -----------------------------------------------------------------------

    def refresh_run_state(self) -> None:
        ok = self.ready() and self.picking is None
        self.run_button.setEnabled(ok)
        self.run_button.setToolTip("" if ok else self.not_ready_reason())
        self.clear_button.setEnabled(self.result is not None)
        again = self.result is not None and self._settings_changed
        self.run_button.setText(f"{self.RUN_TEXT} again" if again else self.RUN_TEXT)

    def settings_changed(self) -> None:
        """A setting changed: the shown result no longer matches it."""
        if self.result is not None and not self._settings_changed:
            self._settings_changed = True
            self.banner.set_verdict("info", "Settings changed", f"{self.RUN_TEXT} to update the result.")
            self.banner.show()
        self.refresh_run_state()

    def _progress(self, message: str, fraction: float) -> None:
        self.progress.show()
        self.progress.setFormat(f"{message}  %p%")
        self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    def run(self) -> None:
        if not self.ready() or self._closed:
            return
        self.end_pick()
        self.select(None)
        self.run_button.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            self.result = self.compute(self._progress)
        except Exception as exc:
            self.result = None
            App.Console.PrintError(f"DFM {self.TITLE}: {exc}\n")
            self._remove_view()
            self.list_box.hide()
            self.banner.set_verdict("fail", "Couldn’t check", self.failure_hint(exc))
            self.banner.show()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.progress.hide()
        self._settings_changed = False
        self._out_of_date = False
        self._map_cache = None
        if self.result is not None:
            self.show_result()
            self.after_result()
            if self.in_map():
                self._show_map()
            else:
                self.redraw()
        self.refresh_run_state()

    def show_result(self) -> None:
        """The banner and the tree for the current result."""
        kind, word, detail = self.verdict()
        self.banner.set_verdict(kind, word, detail)
        self.banner.show()
        groups = self.list_groups()
        self.tree.set_groups(groups)
        rows = bool(self.tree.differences())
        self.list_box.setVisible(rows or any(g.rows for g in groups if not g.counts))
        self.hint.setVisible(rows)
        if self._legend is not None:
            self._legend.set_categories(self.categories(), self.shares())

    def mark_out_of_date(self) -> None:
        self._out_of_date = True
        self.banner.set_verdict("info", "Out of date",
                                f"the model changed after this check was saved. {self.RUN_TEXT} to update it.")
        self.banner.show()

    def clear(self) -> None:
        self.select(None)
        self.result = None
        self._settings_changed = False
        self._map_cache = None
        self._remove_view()
        self.banner.hide()
        self.list_box.hide()
        self.refresh_run_state()
        self.after_clear()

    # -- differences -------------------------------------------------------------------

    def set_ignored(self, differences, ignore: bool) -> None:
        """Ignore (or restore) *differences*: greyed, and not counted in the verdict."""
        for d in differences:
            d.ignored = bool(ignore)
            if ignore:
                self._ignored.add(d.key)
            else:
                self._ignored.discard(d.key)
        if self.saved is not None:
            try:
                self.saved.Ignored = sorted(self._ignored)
            except Exception:
                pass
        self.show_result()
        if self._selected is not None:
            self._show_card(self._selected)
        self.redraw()

    def select(self, difference) -> None:
        self._selected = difference
        self.tree.select(difference)
        if difference is None:
            self._overlay.remove()
        else:
            if not self.in_map():
                self._show_card(difference)
            self.zoom_to(difference)
            self._sync_region_position(difference)
        self.redraw(markers_only=True)

    def _select_and_zoom(self, difference) -> None:
        if difference is self._selected:
            self.zoom_to(difference)
        else:
            self.select(difference)

    def _on_hovered(self, difference) -> None:
        if difference is not self._hovered:
            self._hovered = difference
            self.redraw()

    def emphasised(self, difference) -> bool:
        return difference is self._selected or difference is self._hovered

    def shown_differences(self) -> list:
        """The differences drawn in the 3D view: not ignored, and not hidden in the key."""
        if self.result is None:
            return []
        return [d for d in self.result.differences if not d.ignored and self.category_of(d) not in self._muted]

    def _show_card(self, difference) -> None:
        self._overlay.show_difference(difference, self.rotational())

    def zoom_to(self, difference) -> None:
        if difference is None or difference.box is None:
            return
        obj = self.shown_object()
        if obj is None:
            return
        try:
            from ..results.camera import ResultsCamera

            if self._camera is None or self._camera.target_object is not obj:
                self._camera = ResultsCamera(obj)
            self._camera.zoom_to_points(difference.box.corners())
        except Exception as exc:
            App.Console.PrintWarning(f"DFM: could not zoom. {exc}\n")

    # -- drawing -----------------------------------------------------------------------

    def scene(self) -> Optional[InspectScene]:
        obj = self.shown_object()
        if obj is None:
            return None
        if self._scene is None or self._scene.target_object is not obj:
            safe_remove(self._scene)
            self._scene = InspectScene(obj)
        return self._scene

    def redraw(self, markers_only: bool = False) -> None:
        if self.result is None or self.in_map() or self._closed:
            return
        try:
            if markers_only:
                self.draw_markers()
            else:
                self.draw()
                self.draw_markers()
        except Exception as exc:
            App.Console.PrintWarning(f"DFM {self.TITLE}: could not draw the differences. {exc}\n")
        self._ensure_legend()

    def draw_markers(self) -> None:
        scene = self.scene()
        if scene is not None:
            scene.set_markers([(d.anchor, d is self._selected) for d in self.shown_differences()])

    def _remove_view(self) -> None:
        self._overlay.remove()
        if self._map is not None:
            self._map.remove()
            self._map = None
        safe_remove(self._scene)
        self._scene = None
        self._remove_legend()

    # -- the key in the 3D view ---------------------------------------------------------

    def _ensure_legend(self) -> None:
        cats = self.categories()
        if self._legend is not None or not cats:
            return
        widget = get_view_widget()
        if widget is None:
            return
        from ..contour.category_legend import CategoryLegend

        legend = CategoryLegend(widget)
        try:
            from ..contour.document import auto_legend_text_rgb

            legend.set_text_color(auto_legend_text_rgb())
        except Exception:
            pass
        legend.configure(self.LEGEND_TITLE)
        legend.set_categories(cats, self.shares())
        hidden = {c for c in self._muted}
        legend.set_muted_state(hidden, None)
        legend.mutedChanged.connect(self._on_muted)
        legend.regionRequested.connect(self._on_region_requested)
        self._legend = legend
        size, pos = _LEGEND_STATE.get("size"), _LEGEND_STATE.get("pos")
        if size is not None:
            legend.resize(size)
        if pos is None:
            pos = QtCore.QPoint(max(0, (widget.width() - legend.width()) // 2), 12)
        legend.move(pos)
        legend._clamp_into_parent()
        legend.show()
        legend.raise_()
        if self._selected is not None:
            self._sync_region_position(self._selected)

    def _remove_legend(self) -> None:
        if self._legend is None:
            return
        _LEGEND_STATE["size"] = self._legend.size()
        _LEGEND_STATE["pos"] = self._legend.pos()
        try:
            self._legend.cleanup()
        except Exception:
            pass
        self._legend.hide()
        self._legend.setParent(None)
        self._legend.deleteLater()
        self._legend = None

    def _on_muted(self, muted) -> None:
        self._muted = frozenset(muted or ())
        if self._selected is not None and self.category_of(self._selected) in self._muted:
            self.select(None)
        self.redraw()

    def _category_members(self, code: int) -> list:
        if self.result is None:
            return []
        return [d for d in self.result.differences if not d.ignored and self.category_of(d) == code]

    def _on_region_requested(self, code: int, step: int) -> None:
        """The key's arrows: select the next (or previous) difference of that kind."""
        members = self._category_members(code)
        if not members:
            return
        if self._selected in members:
            index = (members.index(self._selected) + step) % len(members)
        else:
            index = 0 if step > 0 else len(members) - 1
        self.select(members[index])

    def _sync_region_position(self, difference) -> None:
        if self._legend is None:
            return
        code = self.category_of(difference)
        members = self._category_members(code)
        if difference in members:
            self._legend.set_region_position(code, members.index(difference), len(members))

    # -- map ---------------------------------------------------------------------------

    def _on_mode(self, in_map: bool) -> None:
        self._update_display_widgets()
        if self.result is None:
            return
        if in_map:
            self._overlay.remove()
            self._remove_legend()
            safe_remove(self._scene)
            self._scene = None
            self._show_map()
        else:
            if self._map is not None:
                self._map.remove()
                self._map = None
            self.redraw()
            if self._selected is not None:
                self._show_card(self._selected)

    def _show_map(self) -> None:
        obj = self.shown_object()
        if obj is None or self.result is None:
            return
        if self._map_cache is None:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
            try:
                self._progress("Meshing the model", 0.0)
                vertices, triangles, normals = build_mesh(obj, self.shown_shape(),
                                                          lambda m: self._progress(m, 0.05))
                from ...core.inspection.deviation import centroids

                points = centroids(vertices, triangles)

                def prog(done, total):
                    self._progress("Measuring", 0.1 + 0.9 * done / max(total, 1))

                values = self.map_values(points, prog)
                self._map_cache = (vertices, triangles, values, normals)
                lo, hi = self.map_range(values)
                for sb, v in ((self.sb_lo, lo), (self.sb_hi, hi)):
                    sb.blockSignals(True)
                    sb.setValue(v)
                    sb.blockSignals(False)
            except Exception as exc:
                App.Console.PrintError(f"DFM {self.TITLE}: could not make the map. {exc}\n")
                self._map_cache = None
                return
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
                self.progress.hide()
        self._redraw_map()

    def _redraw_map(self) -> None:
        if self._map_cache is None or not self.in_map():
            return
        if self._map is not None:
            self._map.remove()
        self._map = MapView(self.shown_object(), self.MAP_TITLE, self.MAP_NO_VALUE, self.map_format)
        self._map.range_changed = self._on_legend_range
        vertices, triangles, values, normals = self._map_cache
        lo, hi = sorted((self.sb_lo.value(), self.sb_hi.value()))
        try:
            self._map.show(vertices, triangles, values, normals, lo, hi, self.cb_cmap.currentText(),
                           self.cb_smooth.isChecked())
        except Exception as exc:
            App.Console.PrintError(f"DFM {self.TITLE}: could not draw the map. {exc}\n")

    def _on_map_style(self, *_args) -> None:
        if self._map is not None and self._map.shown:
            lo, hi = sorted((self.sb_lo.value(), self.sb_hi.value()))
            self._map.recolor(lo, hi, self.cb_cmap.currentText())

    def _on_legend_range(self, lo: float, hi: float) -> None:
        for sb, v in ((self.sb_lo, lo), (self.sb_hi, hi)):
            sb.blockSignals(True)
            sb.setValue(v)
            sb.blockSignals(False)

    # -- closing -----------------------------------------------------------------------

    def getStandardButtons(self):  # noqa: N802
        return (QtWidgets.QDialogButtonBox.StandardButton.Save
                | QtWidgets.QDialogButtonBox.StandardButton.Close)

    def accept(self):
        self.save()
        self._teardown()

    def reject(self):
        self._teardown()

    def summary_text(self) -> str:
        return f"{self.banner.word}: {self.banner.detail}" if self.result is not None else ""

    def save(self):
        source, reference = self.saved_objects()
        if source is None:
            return None
        settings = self.settings()
        settings["display"] = 1 if self.in_map() else 0
        settings["colormap"] = self.cb_cmap.currentText()
        try:
            self.saved = D.save(self.saved, self.TOOL, source, settings, sorted(self._ignored),
                                self.summary_text(), reference)
        except Exception as exc:
            App.Console.PrintError(f"DFM {self.TITLE}: could not save. {exc}\n")
        return self.saved

    def load_display(self, s: dict) -> None:
        """The Display settings common to both panels."""
        if s.get("colormap"):
            self.cb_cmap.setCurrentText(s["colormap"])
        if s.get("display") == 1:
            self.mode.setChecked(True)

    def _teardown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.end_pick()
        self._remove_view()
        self.after_close()
        if self.saved is not None:
            try:
                if getattr(self.saved.Proxy, "_live_panel", None) is self:
                    self.saved.Proxy._live_panel = None
            except Exception:
                pass
        try:
            Gui.Selection.removeObserver(self)
        except Exception:
            pass
        Gui.Control.closeDialog()


def point_and_normal(sub):
    """(point, unit normal) of a planar face, else None."""
    try:
        import Part  # type: ignore

        if isinstance(sub, Part.Face) and isinstance(sub.Surface, Part.Plane):
            u0, u1, v0, v1 = sub.ParameterRange
            u, v = (u0 + u1) / 2, (v0 + v1) / 2
            p, n = sub.valueAt(u, v), sub.normalAt(u, v)
            return (p.x, p.y, p.z), G.vunit((n.x, n.y, n.z))
    except Exception:
        pass
    return None


def datum_plane(obj):
    """(point, normal) of a datum or origin plane object, else None."""
    type_id = getattr(obj, "TypeId", "")
    if "Plane" not in type_id:
        return None
    try:
        placement = obj.getGlobalPlacement() if hasattr(obj, "getGlobalPlacement") else obj.Placement
        n = placement.Rotation.multVec(App.Vector(0, 0, 1))
        b = placement.Base
        return (b.x, b.y, b.z), G.vunit((n.x, n.y, n.z))
    except Exception:
        return None


def selected_subelements() -> list[tuple[object, str]]:
    """Every selected sub-element as (sub shape, name), in the order picked."""
    out = []
    try:
        for entry in Gui.Selection.getSelectionEx():
            names = list(getattr(entry, "SubElementNames", None) or [])
            for i, sub in enumerate(getattr(entry, "SubObjects", None) or []):
                out.append((sub, names[i] if i < len(names) else "Selected"))
    except Exception:
        pass
    return out
