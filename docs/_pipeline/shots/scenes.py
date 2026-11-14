# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Scene shots: the workbench in FreeCAD, with a part in the 3D view.

Run inside FreeCAD by freecad_runner.py. Each function gets a Context and
returns a QImage. The tools are driven the way a user drives them (the same
panels and methods their buttons call), so the pictures show the real thing.

The parts, and the process settings they are analysed with, come from
docs/models/models.yaml: ``MODELS.molded`` (injection molding) and
``MODELS.printed`` (FDM). Nothing here names a part.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import time

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore
from PySide6 import QtCore, QtGui, QtWidgets

from .. import models
from . import shot

MODELS, _problems = models.load()
if MODELS is None:  # the build checks the file first, so this is only a safety net
    raise RuntimeError("docs/models/models.yaml: " + "; ".join(_problems))
MOLDED, PRINTED = MODELS.molded, MODELS.printed
PIM = MOLDED.process


class Context:
    """What the shots share: the main window, parts, the view and grabbing."""

    def __init__(self, mw, files_dir: str = ""):
        self.mw = mw
        self.files_dir = files_dir
        self.presenter = None

    def file(self, rel: str) -> str:
        """Where a shot writes its file *rel* (a path under docs/content; see ``shot(files=...)``)."""
        path = os.path.join(self.files_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # -- set-up ---------------------------------------------------------------

    def pump(self, seconds: float = 0.3) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.01)

    def open(self, part: models.Part):
        """A document holding *part* (from models.yaml), named as it says."""
        if part.path.suffix.lower() == ".fcstd":
            doc = App.openDocument(str(part.path))
            matches = [o for o in doc.Objects if part.object in (o.Name, o.Label)]
            if not matches:
                raise RuntimeError(f"{part.path.name} has no object {part.object!r}")
            obj = matches[0]
            for o in doc.Objects:  # only the part shows
                if o is not obj and getattr(o, "ViewObject", None) is not None:
                    o.ViewObject.Visibility = False
            obj.ViewObject.Visibility = True
        else:
            import Import  # type: ignore

            doc = App.newDocument(re.sub(r"\W", "_", part.label) or "Part")
            Import.insert(str(part.path), doc.Name)
            doc.recompute()
            obj = next(o for o in doc.Objects if getattr(o, "Shape", None) and o.Shape.Solids)
        obj.Label = part.label
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


def _dir(xyz):
    from OCP.gp import gp_Dir

    return gp_Dir(*xyz)


def requirements(part: models.Part) -> dict:
    """What the setup panel would pass to the analysis for *part*."""
    if part.key == "molded":
        return {"PULL_DIRECTION": _dir(part.direction("pull_direction")), "SIDE_ACTIONS": [],
                "NOMINAL_WALL_THICKNESS": {"mode": "auto"}}
    return {"PRINT_ORIENTATION": _dir(part.direction("print_direction")),
            "NOZZLE_DIAMETER": float(part.options["nozzle_diameter"]),
            "LAYER_HEIGHT": float(part.options["layer_height"])}


def analyse(ctx: Context, part: models.Part, material: str | None = None):
    """Open *part* and analyse it with its process, as Run does. Returns (object, results)."""
    obj = ctx.open(part)
    return obj, ctx.analyse(obj, part.process, material or part.material, **requirements(part))


# -- getting started ------------------------------------------------------------


@shot("toolbar", "scene", "The DFM Analysis toolbar")
def toolbar(ctx: Context):
    ctx.open(MOLDED)
    bar = ctx.toolbar("DFM Analysis")
    ctx.pump(0.3)
    image = bar.grab().toImage()
    width = bar.childrenRect().right() + 6  # the buttons, not the empty bar after them
    return image.copy(0, 0, min(width, image.width()), image.height())


@shot("setup-panel", "scene",
      "The DFM Analysis setup: the part, the process and material, and the pull direction arrow on the model")
def setup_panel(ctx: Context):
    obj = ctx.open(MOLDED)
    from freecad.DFM.gui.task_setup import TaskSetup

    ctx.select(obj)
    panel = TaskSetup()  # what the DFM Analysis command opens
    Gui.Control.showDialog(panel)
    ctx.pump(1.0)
    _choose(panel.form.cbManProcess, PIM)
    ctx.pump(0.5)
    _choose(panel.form.cbMaterial, MOLDED.material)
    ctx.pump(0.5)
    ctx.iso()
    return ctx.view_and_task()


@shot("results-window", "scene",
      "Results of an injection molding analysis: findings highlighted on the model, and listed in the results panel")
def results_window(ctx: Context):
    analyse(ctx, MOLDED)
    ctx.iso()
    return ctx.window()


@shot("results-panel", "scene", "The results panel: findings grouped by criticality, with filters above them")
def results_panel(ctx: Context):
    analyse(ctx, MOLDED)
    panel = ctx.results_panel()
    panel._expand_all()
    ctx.pump(0.3)
    return ctx.crop(ctx.task_dock())


@shot("results-finding", "scene",
      "A finding selected in the results panel: its faces highlighted and the camera turned to face them")
def results_finding(ctx: Context):
    _obj, results = analyse(ctx, MOLDED)
    panel = ctx.results_panel()
    panel._expand_all()
    finding = _first(results, ("MIN_DRAFT_ANGLE", "MAX_WALL_THICKNESS", "SHARP_INTERNAL_CORNERS"))
    if finding is not None:
        panel.select_finding(finding)
        ctx.pump(1.5)
    return ctx.view_and_task()


@shot("results-export", "scene", "The Export tab: a PDF report from a preset, or the findings as a CSV file")
def results_export(ctx: Context):
    analyse(ctx, MOLDED)
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
        obj = ctx.open(MOLDED)
        # Each run is saved, as the panel's Save button does; the History tab reads
        # the saved runs when the panel opens, so the last run is opened after them.
        # Run 1 asks for thicker walls than run 2, so run 2 shows findings resolved.
        first = MOLDED.options.get("history_first_material") or MOLDED.material
        for material, save in ((first, True), (MOLDED.material, True), (MOLDED.material, False)):
            Gui.Control.closeDialog()
            ctx.pump(0.3)
            ctx.analyse(obj, PIM, material, **requirements(MOLDED))
            if save:
                _save_run(ctx)
    finally:
        history.datetime = _dt.datetime
    panel = ctx.results_panel()
    panel.form.tabWidget.setCurrentIndex(1)
    ctx.pump(0.5)
    return ctx.crop(ctx.task_dock())


# -- 3D printing -------------------------------------------------------------------


@shot("fdm-results", "scene", "Results of an FDM analysis of a 3D printed part")
def fdm_results(ctx: Context):
    analyse(ctx, PRINTED)
    ctx.iso()
    return ctx.view_and_task()


# -- contours and inspection ----------------------------------------------------------


def _contour(ctx: Context, measure_name: str, title: str, part: models.Part = MOLDED, value_range=None):
    from freecad.DFM.app.contour import measures
    from freecad.DFM.gui.contour.panel import ContourTaskPanel

    obj = ctx.open(part)
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
    return _contour(ctx, "ThicknessMeasure", "Thickness Analysis",
                    value_range=MOLDED.options.get("thickness_range"))


@shot("contour-draft", "scene", "Draft Analysis: the model colored by draft angle to the pull direction")
def contour_draft(ctx: Context):
    return _contour(ctx, "DraftMeasure", "Draft Analysis")


@shot("contour-undercut", "scene",
      "Undercut Analysis: each face colored by the direction it releases from the mold")
def contour_undercut(ctx: Context):
    return _contour(ctx, "UndercutMeasure", "Undercut Analysis")


@shot("contour-curvature", "scene", "Curvature Analysis: the model colored by its tightest radius")
def contour_curvature(ctx: Context):
    return _contour(ctx, "CurvatureMeasure", "Curvature Analysis", PRINTED)


@shot("symmetry-check", "scene", "Symmetry Check: whether the part is symmetric, and what isn't")
def symmetry_check(ctx: Context):
    from freecad.DFM.gui.inspection.symmetry_panel import SymmetryPanel

    obj = ctx.open(MOLDED)
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

    a = ctx.open(MOLDED)
    bb = a.Shape.BoundBox
    hole = Part.makeCylinder(4.0, bb.ZLength * 3, App.Vector(bb.Center.x + bb.XLength * 0.2,
                                                              bb.Center.y, bb.ZMin - bb.ZLength))
    b = a.Document.addObject("Part::Feature", "PartB")
    b.Shape = a.Shape.cut(hole)
    b.Label = f"{MOLDED.label} (new)"
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


# -- the sample report -----------------------------------------------------------------

REPORT = "assets/reports/sample-dfm-report.pdf"


@shot("report-pages", "scene", "Two pages of the sample DFM report: the summary, and the findings of a rule",
      files=(REPORT,))
def report_pages(ctx: Context):
    """Writes the sample report with Export PDF report, as the results panel's
    Export button does (with the report settings in models.yaml), and returns
    its first pages, painted the way the report dialog previews them."""
    from freecad.DFM.gui.report.exporter import export_pdf
    from freecad.DFM.gui.report.layout import ReportLayout, page_thumbnail
    from freecad.DFM.gui.report.template import get_template

    report = MODELS.report
    analyse(ctx, MODELS.parts[report.part])
    presenter = ctx.presenter
    data = presenter._report_data()
    data.created = _dt.datetime.combine(report.date, _dt.time(9, 0))
    settings = presenter._report_settings().copy(**report.settings, open_after=False)
    template = get_template(settings.template)
    capture = _KeepImages(presenter._report_capture(settings))
    if not export_pdf(None, ctx.file(REPORT), data, settings, template, capture):
        raise RuntimeError("Export PDF report didn't write the report")
    layout = ReportLayout(data, settings, template)
    pages = layout.build()
    # The cover, and the first page of a rule with findings (the summary can run on).
    flagged = {rule.label for rule, found in data.grouped.items() if found}
    first_rule = next((p for p in pages[1:] if p.label in flagged), pages[min(1, len(pages) - 1)])
    return _side_by_side([page_thumbnail(p, template, layout.ts, 700, images=capture.images)
                          for p in (pages[0], first_rule)])


class _KeepImages:
    """A report capture that keeps the images it takes, for painting the pages again."""

    def __init__(self, inner):
        self._inner = inner
        self.images: dict = {}

    @property
    def available(self) -> bool:
        return getattr(self._inner, "available", True)

    def begin(self) -> None:
        self._inner.begin()

    def end(self) -> None:
        self._inner.end()

    def capture(self, job, w, h):
        image = self._inner.capture(job, w, h)
        if image is not None:
            self.images[job.key] = image
        return image


def _side_by_side(pages: list[QtGui.QImage], gap: int = 32, pad: int = 24) -> QtGui.QImage:
    """Pages next to each other on a light grey desk, with a thin edge."""
    w = sum(p.width() for p in pages) + gap * (len(pages) - 1) + 2 * pad
    h = max(p.height() for p in pages) + 2 * pad
    out = QtGui.QImage(w, h, QtGui.QImage.Format.Format_RGB32)
    out.fill(QtGui.QColor("#e4e6ea"))
    painter = QtGui.QPainter(out)
    x = pad
    for page in pages:
        painter.drawImage(x, pad, page)
        painter.setPen(QtGui.QColor("#c3c7ce"))
        painter.drawRect(x, pad, page.width() - 1, page.height() - 1)
        x += page.width() + gap
    painter.end()
    return out


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
