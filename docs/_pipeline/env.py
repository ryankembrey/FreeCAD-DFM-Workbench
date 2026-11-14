# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Paths, and the workbench made importable without FreeCAD.

Import this before anything from ``freecad.DFM``. It puts the repository on
``sys.path``, installs the FreeCAD stand-ins the GUI tests use
(tests/recognition_support/freecad_stubs.py) and starts an offscreen Qt
application for the diagram colours.
"""

import os
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent
REPO = DOCS.parent
CONTENT = DOCS / "content"
DATA = DOCS / "_data"
AUTHORED = DOCS / "_authored"
CONFIG = DOCS / "zensical.toml"
PROCESSES = REPO / "freecad" / "DFM" / "core" / "processes"
LOGO = REPO / "resources" / "icons" / "logo.svg"

_ready = False


def setup() -> None:
    """Make ``freecad.DFM`` importable with FreeCAD stand-ins (once)."""
    global _ready
    if _ready:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    for path in (str(REPO), str(REPO / "tests" / "recognition_support")):
        if path not in sys.path:
            sys.path.insert(0, path)
    # Always the stand-ins, even where FreeCAD is installed: the docs must not
    # depend on anyone's FreeCAD settings or saved processes.
    for name in ("FreeCAD", "FreeCADGui", "Part"):
        sys.modules.pop(name, None)
    import freecad_stubs  # noqa: F401

    # FreeCAD's conda package has a regular ``freecad`` package, which would
    # hide this repository's ``freecad/DFM``: claim the name for the repo.
    import types

    namespace = types.ModuleType("freecad")
    namespace.__path__ = [str(REPO / "freecad")]
    sys.modules["freecad"] = namespace

    from PySide6 import QtGui

    if QtGui.QGuiApplication.instance() is None:
        setup.app = QtGui.QGuiApplication([])  # kept alive for the run
    _ready = True
