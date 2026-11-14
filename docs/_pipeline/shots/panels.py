# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Panel shots: workbench windows and panels that need no model.

Run inside FreeCAD by freecad_runner.py, like the scene shots, so they carry
FreeCAD's own style. Each function returns a QImage.
"""

from __future__ import annotations

import time

from . import shot

# -- helpers ----------------------------------------------------------------


def _app():
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance()


def _settle(seconds: float = 0.2) -> None:
    app = _app()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()


def _grab(widget):
    _settle()
    return widget.grab().toImage()


def _library(process: str, material: str, layout: str = "list", size=(1200, 800)):
    from freecad.DFM.gui import process_library as PL

    lib = PL.ProcessLibrary()
    lib.resize(*size)
    lib.show()
    lib.material_edit_view.set_layout_mode(layout)
    _settle()
    lib.tree_view.select_material(process, material)
    _settle()
    return lib


# -- shots -------------------------------------------------------------------


@shot("process-library", "panel",
      "The Process Library: processes and their materials on the left, the selected material's rules on the right")
def process_library():
    lib = _library("Plastic Injection Molding", "ABS")
    image = _grab(lib)
    lib.close()
    return image


@shot("process-library-grid", "panel",
      "The Process Library in its grid layout, with each rule's diagram")
def process_library_grid():
    lib = _library("Plastic Injection Molding", "ABS", layout="grid", size=(1200, 860))
    image = _grab(lib)
    lib.material_edit_view.set_layout_mode("list")  # leave the saved preference as it was
    lib.close()
    return image


@shot("process-library-fdm", "panel",
      "The Process Library showing the FDM process and its PLA material")
def process_library_fdm():
    lib = _library("FDM", "PLA")
    image = _grab(lib)
    lib.close()
    return image


@shot("edit-feedback", "panel",
      "Editing a rule's messages: placeholders you can click to insert, and the warning and error text")
def edit_feedback():
    from freecad.DFM.core.processes.process import RuleFeedback
    from freecad.DFM.core.registries.process_registry import ProcessRegistry
    from freecad.DFM.core.rules import Rulebook as R
    from freecad.DFM.gui import process_library as PL

    process = ProcessRegistry.get_instance().get_process_by_name("Plastic Injection Molding")
    rule = R.MAX_RIB_THICKNESS_RATIO
    feedback = process.rule_feedback.get(rule) or RuleFeedback()
    default = process.materials["Default"].rule_limits.get(rule)
    values = PL.RuleValueContext(default, default, True, "Default")
    dialog = PL.EditFeedbackDialog(process.name, rule, feedback, process.get_criticality(rule), values)
    dialog.resize(720, 640)
    dialog.show()
    image = _grab(dialog)
    dialog.close()
    return image


@shot("progress-panel", "panel",
      "The progress panel while an analysis runs: each rule, how far it has got, and the time left")
def progress_panel():
    from PySide6 import QtCore
    from freecad.DFM.core.rules import Rulebook as R
    from freecad.DFM.gui import task_progress as TP

    class _Obj:
        Label = "Enclosure"

    class _Request:
        target_object = _Obj()
        process_name = "Plastic Injection Molding"
        material_name = "ABS"

    real = QtCore.QTimer.singleShot
    QtCore.QTimer.singleShot = lambda *a, **k: None  # don't start an analysis
    try:
        panel = TP.TaskProgress(_Request())
    finally:
        QtCore.QTimer.singleShot = real
    rules = [R.MIN_DRAFT_ANGLE, R.NO_UNDERCUTS, R.MIN_WALL_THICKNESS, R.MAX_WALL_THICKNESS,
             R.SHARP_INTERNAL_CORNERS, R.MAX_RIB_THICKNESS_RATIO, R.MAX_BOSS_WALL_RATIO]
    analyzers = ["DRAFT", "UNDERCUT", "RAY", "SPHERE", "CORNER", "FEATURE", "FEATURE"]
    panel.running = True
    panel._on_event("plan", {"total_steps": 700, "rules": [
        {"rule": r, "analyzer": a, "steps": 100} for r, a in zip(rules, analyzers)]})
    now = time.monotonic()
    panel._on_event("workers", {"workers": 8, "analyzers": {
        "DRAFT": {"total": 86, "done": 86, "started": now - 9, "finished": now - 6},
        "UNDERCUT": {"total": 86, "done": 86, "started": now - 9, "finished": now - 5},
        "RAY": {"total": 86, "done": 52, "started": now - 9, "busy": 3},
        "SPHERE": {"total": 86, "done": 20, "started": now - 9, "busy": 3},
    }})
    panel._on_event("check", {"rule": R.MIN_DRAFT_ANGLE, "findings": 4, "seconds": 0.1})
    panel._on_event("check", {"rule": R.NO_UNDERCUTS, "findings": 0, "seconds": 0.1})
    panel.form.resize(380, 470)
    panel.form.show()
    image = _grab(panel.form)
    panel.form.close()
    return image


def _neutral_paths(widget) -> None:
    """Replace the machine's own paths in *widget* with the usual ones, so the
    picture is the same everywhere and publishes nothing about this machine.
    (The General page names the worker Python; Reports, the template folder
    inside the throwaway FreeCAD user folder.)"""
    import os

    from PySide6 import QtWidgets

    home = os.environ.get("FREECAD_USER_HOME", "").rstrip("/")
    for label in widget.findChildren(QtWidgets.QLabel):
        # Names the worker Python once a worker has started, which depends on
        # the machine and on what ran before: always show the text before.
        if label.text().startswith(("Workers run on", "Workers start")):
            label.setText("Workers start with the first analysis.")
    for kind in (QtWidgets.QLabel, QtWidgets.QLineEdit):
        for w in widget.findChildren(kind):
            if home and home in w.text():
                w.setText(w.text().replace(home, "~/.local/share/FreeCAD"))
                if isinstance(w, QtWidgets.QLineEdit):
                    w.setCursorPosition(0)
            if isinstance(w, QtWidgets.QLineEdit) and home and home in w.placeholderText():
                w.setPlaceholderText(w.placeholderText().replace(home, "~/.local/share/FreeCAD"))


def _preference_page(cls_path: str):
    import importlib

    module, name = cls_path.rsplit(".", 1)
    page = getattr(importlib.import_module(module), name)()
    form = page.form
    if hasattr(page, "loadSettings"):
        page.loadSettings()
    from PySide6 import QtWidgets

    _neutral_paths(form)
    form.resize(640, max(form.sizeHint().height(), 300))
    form.show()
    image = _grab(form)
    form.close()
    return image


#: Preference pages, in the order FreeCAD lists them (init_gui.py).
PREFERENCE_PAGES = (
    ("prefs-general", "General", "freecad.DFM.gui.preferences.DFMPreferencesGeneral"),
    ("prefs-results", "Results", "freecad.DFM.gui.preferences.DFMPreferencesResults"),
    ("prefs-reports", "Reports", "freecad.DFM.gui.report.preferences_page.DFMPreferencesReports"),
    ("prefs-analyzers", "Analyzers", "freecad.DFM.gui.preferences.DFMPreferencesAnalyzers"),
    ("prefs-contour", "Contour", "freecad.DFM.gui.preferences.DFMPreferencesContour"),
    ("prefs-recognition", "Feature Recognition",
     "freecad.DFM.gui.recognition_bench.preferences_page.DFMPreferencesRecognition"),
)

for _id, _title, _cls in PREFERENCE_PAGES:
    shot(_id, "panel", f"Edit > Preferences > DFM > {_title}")(
        lambda _cls=_cls: _preference_page(_cls))
