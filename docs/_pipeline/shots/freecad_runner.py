# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Runs inside FreeCAD (``freecad freecad_runner.py``) to take screenshots.

Started by engine.py with:

    DFM_REPO        the repository
    DFM_SHOTS_OUT   folder to write <id>.png, results.json and files/<path> into
    DFM_SHOTS_IDS   comma-separated shot ids to take

and a throwaway FreeCAD user folder, so nothing of anyone's own FreeCAD
settings or saved processes shows. Writes results.json: {id: "ok" | error}.
"""

import json
import os
import sys
import traceback

REPO = os.environ["DFM_REPO"]
OUT = os.environ["DFM_SHOTS_OUT"]
IDS = [i for i in os.environ.get("DFM_SHOTS_IDS", "").split(",") if i]

sys.path.insert(0, os.path.join(REPO, "docs"))
sys.path.insert(0, REPO)
import freecad  # noqa: E402  FreeCAD's own namespace package: add the workbench to it

if os.path.join(REPO, "freecad") not in list(freecad.__path__):
    freecad.__path__.append(os.path.join(REPO, "freecad"))

import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from PySide6 import QtCore  # noqa: E402

WINDOW = (1600, 1000)


def prepare() -> None:
    """The same FreeCAD for every run: window size and position, DFM active.
    (Don't close the Start page from here: its timers then crash FreeCAD.)"""
    from PySide6 import QtGui, QtWidgets

    # One font everywhere: the environment's Ubuntu, whatever the machine has.
    QtWidgets.QApplication.setFont(QtGui.QFont("Ubuntu", 10))
    mw = Gui.getMainWindow()
    mw.showNormal()
    mw.resize(*WINDOW)
    mw.move(0, 0)
    import freecad.DFM.init_gui  # noqa: F401  registers the workbench and its commands

    Gui.activateWorkbench("DFMWorkbench")
    _no_message_boxes()


def _no_message_boxes() -> None:
    """Nobody is there to click OK: a message box would wait until the timeout.
    Print it instead and answer with the box's default."""
    from PySide6 import QtWidgets

    box = QtWidgets.QMessageBox

    def make(kind, answer):
        def show(_parent, title, text, *args, **kwargs):
            print(f"[{kind}] {title}: {text}", file=sys.stderr, flush=True)
            return answer
        return staticmethod(show)

    box.information = make("information", box.StandardButton.Ok)
    box.warning = make("warning", box.StandardButton.Ok)
    box.critical = make("critical", box.StandardButton.Ok)
    box.question = make("question", box.StandardButton.No)


def main() -> None:
    results = {}
    try:
        prepare()
        from _pipeline.shots import REGISTRY, panels, scenes  # noqa: F401  (registers the shots)

        ctx = scenes.Context(Gui.getMainWindow(), os.path.join(OUT, "files"))
        for shot_id in IDS:
            shot = REGISTRY.get(shot_id)
            if shot is None:
                results[shot_id] = "no such shot"
                continue
            try:
                image = shot.fn(ctx) if shot.engine == "scene" else shot.fn()
                for rel in shot.files:
                    if not os.path.isfile(ctx.file(rel)):
                        raise RuntimeError(f"the shot didn't write {rel}")
                if image is None or image.isNull():
                    raise RuntimeError("the shot returned no image")
                if not image.save(os.path.join(OUT, f"{shot_id}.png")):
                    raise RuntimeError("could not save the image")
                results[shot_id] = "ok"
            except Exception:
                results[shot_id] = traceback.format_exc()
            finally:
                ctx.reset()
    except Exception:
        results["_runner"] = traceback.format_exc()
    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    os._exit(0)


QtCore.QTimer.singleShot(1500, main)
