# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Scene shots: the workbench in FreeCAD, with a part in the 3D view.

Run inside FreeCAD by freecad_runner.py. Each function gets a Context and
returns a QImage. The tools are driven the way a user drives them (the same
panels and methods their buttons call), so the pictures show the real thing.

Demo parts come from resources/examples. The enclosure is the injection
molding example; the holder is the 3D printing one.
"""

from __future__ import annotations

import math
import os
import time

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore
from PySide6 import QtCore, QtGui, QtWidgets

from . import shot

REPO = os.environ.get("DFM_REPO", "")
ENCLOSURE = "resources/examples/lastic enclosure 2.STEP"
HOLDER = "resources/examples/Holder.stp"
LEGO = "resources/examples/LEGO Brick v1.step"

PIM = "Plastic Injection Molding"


class Context:
    """What the shots share: the main window, parts, the view and grabbing."""

    def __init__(self, mw):
        self.mw = mw
        self.presenter = None

    # -- set-up ---------------------------------------------------------------

    def pump(self, seconds: float = 0.3) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.01)

    def open(self, rel_path: str, label: str):
        """A new document holding the part at *rel_path*, named *label*."""
        import Import  # type: ignore

        doc = App.newDocument("Demo")
        Import.insert(os.path.join(REPO, rel_path), doc.Name)
        doc.recompute()
        obj = next(o for o in doc.Objects if getattr(o, "Shape", None) and o.Shape.Solids)
        obj.Label = label
        Gui.Selection.clearSelection()
        self.iso()
        return obj

    def iso(self) -> None:
        """Isometric view, the part fitted into what the task panel leaves free."""
        view = Gui.ActiveDocument.ActiveView
        view.setAnimationEnabled(False)
        view.viewIsometric()
        self.fit()

    def fit(self) -> None:
        """Fit the part into the 3D view (beside the task panel, which is docked)."""
        self.task_panel(required=False)
        Gui.ActiveDocument.ActiveView.fitAll()
        self.pump(0.5)

    def select(self, obj) -> None:
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(obj.Document.Name, obj.Name)
        self.pump(0.1)

    def reset(self) -> None:
        """Close whatever the last shot opened."""
        try:
            Gui.Control.closeDialog()
        except Exception:
            pass
        self.presenter = None
        for name in list(App.listDocuments()):
            try:
                App.closeDocument(name)
            except Exception:
                pass
        Gui.Selection.clearSelection()
        self.pump(0.3)

    # -- running the workbench ----------------------------------------------------

    def analyse(self, obj, process: str, material: str, **requirements):
        """Run a DFM analysis on *obj* and open the results panel, as Run does."""
        from freecad.DFM.app.analysis_runner import AnalysisRunner
        from freecad.DFM.core.registries.process_registry import ProcessRegistry
        from freecad.DFM.gui.task_setup import open_results

        runner = AnalysisRunner()
        results = runner.run_analysis(process, material, obj.Shape, **requirements)
        proc = ProcessRegistry.get_instance().get_process_by_name(process)
        self.presenter = open_results(obj, proc, process, material, results, runner, requirements)
        self.pump(1.0)
        return results

    def results_panel(self):
        return self.presenter.view if self.presenter is not None else None

    # -- grabbing --------------------------------------------------------------

    def window(self) -> QtGui.QImage:
        self.task_panel(required=False)
        self.pump(0.5)
        return self.mw.grab().toImage()

    def task_panel(self, width: int = 500, required: bool = True):
        """The Tasks dock, docked beside the 3D view and *width* wide.

        FreeCAD 1.1 starts with the task panel in "overlay" mode, laid over the
        right of the 3D view, where it covers the part and can be cut off. Like a
        user would, this takes it out of the overlay: Toggle Overlay (F3) with the
        cursor over the panel. The panel stays docked for the rest of the run.
        """
        dock = self.mw.findChild(QtWidgets.QDockWidget, "Tasks")
        if dock is None or not dock.isVisible():
            if required:
                raise RuntimeError("no task panel is showing")
            return None
        if dock.parentWidget() is not self.mw:  # still in an overlay panel
            centre = dock.mapToGlobal(dock.rect().center())
            QtGui.QCursor.setPos(centre)
            self.pump(0.2)
            Gui.runCommand("Std_DockOverlayToggle")
            self.pump(1.0)
            if dock.parentWidget() is not self.mw:
                Gui.runCommand("Std_DockOverlayAll")
                self.pump(1.0)
            QtGui.QCursor.setPos(0, 0)
        if dock.parentWidget() is self.mw and abs(dock.width() - width) > 4:
            self.mw.resizeDocks([dock], [width], QtCore.Qt.Orientation.Horizontal)
            self.pump(0.5)
        return dock

    def task_dock(self) -> QtWidgets.QWidget:
        """The task panel (for cropping)."""
        return self.task_panel()

    def crop(self, *widgets, margin: int = 0) -> QtGui.QImage:
        """The part of the window covering *widgets*."""
        self.pump(0.5)
        rect = QtCore.QRect()
        for w in widgets:
            top_left = w.mapTo(self.mw, QtCore.QPoint(0, 0))
            rect = rect.united(QtCore.QRect(top_left, w.size()))
        rect = rect.adjusted(-margin, -margin, margin, margin).intersected(self.mw.rect())
        return self.mw.grab(rect).toImage()

    def view_and_task(self) -> QtGui.QImage:
        """The 3D view and the task panel beside it, without the model tree."""
        view = Gui.ActiveDocument.ActiveView.graphicsView()
        return self.crop(view, self.task_dock())

    def toolbar(self, title: str) -> QtWidgets.QToolBar:
        for bar in self.mw.findChildren(QtWidgets.QToolBar):
            if bar.windowTitle() == title and bar.isVisible():
                return bar
        raise RuntimeError(f"no toolbar {title!r}")


def _pull_up():
    from OCP.gp import gp_Dir

    return gp_Dir(0, 0, 1)


def _pim_requirements():
    return {"PULL_DIRECTION": _pull_up(), "SIDE_ACTIONS": [], "NOMINAL_WALL_THICKNESS": {"mode": "auto"}}


# -- getting started ------------------------------------------------------------


@shot("toolbar", "scene", "The DFM Analysis toolbar")
def toolbar(ctx: Context):
    ctx.open(ENCLOSURE, "Enclosure")
    bar = ctx.toolbar("DFM Analysis")
    ctx.pump(0.3)
    image = bar.grab().toImage()
    width = bar.childrenRect().right() + 6  # the buttons, not the empty bar after them
    return image.copy(0, 0, min(width, image.width()), image.height())


@shot("setup-panel", "scene",
      "The DFM Analysis setup: the part, the process and material, and the pull direction arrow on the model")
def setup_panel(ctx: Context):
    obj = ctx.open(ENCLOSURE, "Enclosure")
    from freecad.DFM.gui.task_setup import TaskSetup

    ctx.select(obj)
    panel = TaskSetup()  # what the DFM Analysis command opens
    Gui.Control.showDialog(panel)
    ctx.pump(1.0)
    _choose(panel.form.cbManProcess, PIM)
    ctx.pump(0.5)
    _choose(panel.form.cbMaterial, "ABS")
    ctx.pump(0.5)
    ctx.iso()
    return ctx.view_and_task()


@shot("results-window", "scene",
      "Results of an injection molding analysis: findings highlighted on the model, and listed in the results panel")
def results_window(ctx: Context):
    obj = ctx.open(ENCLOSURE, "Enclosure")
    ctx.analyse(obj, PIM, "ABS", **_pim_requirements())
    ctx.iso()
    return ctx.window()


@shot("results-panel", "scene", "The results panel: findings grouped by criticality, with filters above them")
def results_panel(ctx: Context):
    obj = ctx.open(ENCLOSURE, "Enclosure")
    ctx.analyse(obj, PIM, "ABS", **_pim_requirements())
    panel = ctx.results_panel()
    panel._expand_all()
    ctx.pump(0.3)
    return ctx.crop(ctx.task_dock())


@shot("results-finding", "scene",
      "A finding selected in the results panel: its faces highlighted and the camera turned to face them")
def results_finding(ctx: Context):
    obj = ctx.open(ENCLOSURE, "Enclosure")
    results = ctx.analyse(obj, PIM, "ABS", **_pim_requirements())
    panel = ctx.results_panel()
    panel._expand_all()
    finding = _first(results, ("MIN_DRAFT_ANGLE", "MAX_WALL_THICKNESS", "SHARP_INTERNAL_CORNERS"))
    if finding is not None:
        panel.select_finding(finding)
        ctx.pump(1.5)
    return ctx.view_and_task()


@shot("results-export", "scene", "The Export tab: a PDF report from a preset, or the findings as a CSV file")
def results_export(ctx: Context):
    obj = ctx.open(ENCLOSURE, "Enclosure")
    ctx.analyse(obj, PIM, "ABS", **_pim_requirements())
    panel = ctx.results_panel()
    panel.form.tabWidget.setCurrentIndex(2)
    return ctx.crop(ctx.task_dock())


@shot("results-history", "scene",
      "The History tab after two runs: what was fixed, what regressed, and the trend")
def results_history(ctx: Context):
    import datetime as _dt
    from freecad.DFM.app import history

    class _Clock(_dt.datetime):
        """Fixed run times, so the picture is the same every time it is taken."""
        times = [_dt.datetime(2026, 3, 2, 9, 30), _dt.datetime(2026, 3, 2, 10, 15)]

        @classmethod
        def now(cls, tz=None):
            return cls.times.pop(0) if len(cls.times) > 1 else cls.times[0]

    history.datetime = _Clock
    try:
        obj = ctx.open(ENCLOSURE, "Enclosure")
        # Each run is saved, as the panel's Save button does; the History tab reads
        # the saved runs when the panel opens, so the last run is opened after them.
        # Run 1 asks for thicker walls than run 2, so run 2 shows findings resolved.
        for material, save in (("Long-Fiber Reinforced Plastics", True), ("ABS", True), ("ABS", False)):
            Gui.Control.closeDialog()
            ctx.pump(0.3)
            ctx.analyse(obj, PIM, material, **_pim_requirements())
            if save:
                _save_run(ctx)
    finally:
        history.datetime = _dt.datetime
    panel = ctx.results_panel()
    panel.form.tabWidget.setCurrentIndex(1)
    ctx.pump(0.5)
    return ctx.crop(ctx.task_dock())


# -- 3D printing -------------------------------------------------------------------


@shot("fdm-results", "scene", "Results of an FDM analysis of a printed holder, printed upright")
def fdm_results(ctx: Context):
    from OCP.gp import gp_Dir

    obj = ctx.open(HOLDER, "Holder")
    ctx.analyse(obj, "FDM", "PLA", PRINT_ORIENTATION=gp_Dir(0, 1, 0), NOZZLE_DIAMETER=0.4, LAYER_HEIGHT=0.2)
    ctx.iso()
    return ctx.view_and_task()


# -- contours and inspection ----------------------------------------------------------


def _contour(ctx: Context, measure_name: str, title: str, part=ENCLOSURE, label="Enclosure", value_range=None):
    from freecad.DFM.app.contour import measures
    from freecad.DFM.gui.contour.panel import ContourTaskPanel

    obj = ctx.open(part, label)
    panel = ContourTaskPanel(getattr(measures, measure_name)(), title)
    Gui.Control.showDialog(panel)
    ctx.pump(0.5)
    panel._apply_object(obj)
    panel._on_generate()
    ctx.pump(1.0)
    if value_range is not None:  # as a user types into the Range fields
        panel.sb_range_lo.setValue(value_range[0])
        panel.sb_range_hi.setValue(value_range[1])
        ctx.pump(1.0)
    ctx.iso()
    return ctx.view_and_task()


@shot("contour-thickness", "scene", "Thickness Analysis: the model colored by wall thickness")
def contour_thickness(ctx: Context):
    return _contour(ctx, "ThicknessMeasure", "Thickness Analysis", value_range=(0.0, 4.0))


@shot("contour-draft", "scene", "Draft Analysis: the model colored by draft angle to the pull direction")
def contour_draft(ctx: Context):
    return _contour(ctx, "DraftMeasure", "Draft Analysis")


@shot("contour-undercut", "scene",
      "Undercut Analysis: each face colored by the direction it releases from the mold")
def contour_undercut(ctx: Context):
    return _contour(ctx, "UndercutMeasure", "Undercut Analysis")


@shot("contour-curvature", "scene", "Curvature Analysis: the model colored by its tightest radius")
def contour_curvature(ctx: Context):
    return _contour(ctx, "CurvatureMeasure", "Curvature Analysis", HOLDER, "Holder")


@shot("symmetry-check", "scene", "Symmetry Check: whether the part is symmetric, and what isn't")
def symmetry_check(ctx: Context):
    from freecad.DFM.gui.inspection.symmetry_panel import SymmetryPanel

    obj = ctx.open(ENCLOSURE, "Enclosure")
    ctx.select(obj)
    panel = SymmetryPanel()
    Gui.Control.showDialog(panel)
    ctx.pump(0.5)
    if hasattr(panel, "_apply_model_from_selection"):
        panel._apply_model_from_selection()
    ctx.pump(0.3)
    panel.run()
    ctx.pump(1.0)
    ctx.iso()
    return ctx.view_and_task()


@shot("compare-bodies", "scene", "Compare Bodies: what changed between two versions of a part, and where")
def compare_bodies(ctx: Context):
    import Part  # type: ignore
    from freecad.DFM.gui.inspection.compare_panel import ComparePanel

    a = ctx.open(ENCLOSURE, "Enclosure")
    bb = a.Shape.BoundBox
    hole = Part.makeCylinder(4.0, bb.ZLength * 3, App.Vector(bb.Center.x + bb.XLength * 0.2,
                                                              bb.Center.y, bb.ZMin - bb.ZLength))
    b = a.Document.addObject("Part::Feature", "EnclosureB")
    b.Shape = a.Shape.cut(hole)
    b.Label = "Enclosure (new)"
    a.Document.recompute()
    a.ViewObject.Visibility = False
    ctx.pump(0.2)
    panel = ComparePanel()
    Gui.Control.showDialog(panel)
    ctx.pump(0.5)
    ctx.select(a)
    panel._apply_from_selection("a")
    ctx.select(b)
    panel._apply_from_selection("b")
    Gui.Selection.clearSelection()
    panel.run()
    ctx.pump(1.0)
    ctx.iso()
    return ctx.view_and_task()


# -- helpers ----------------------------------------------------------------------


def _choose(combo: QtWidgets.QComboBox, text: str) -> None:
    for i in range(combo.count()):
        if combo.itemText(i) == text or combo.itemData(i) == text:
            combo.setCurrentIndex(i)
            return


def _first(results, rule_names):
    for name in rule_names:
        for r in results:
            if r.rule_id.name == name:
                return r
    return results[0] if results else None


def _save_run(ctx: Context) -> None:
    """Save, as the results panel's Save button does (it also adds the run to History)."""
    ctx.presenter.handle_save()
    ctx.pump(0.5)
