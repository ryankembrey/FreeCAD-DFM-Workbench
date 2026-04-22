# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import FreeCAD as App  # type: ignore

try:
    import OCC
except ImportError:
    msg = (
        "The DFM Workbench requires pythonocc-core, which is not available in this FreeCAD installation.\n"
        "This is common when compiling FreeCAD from source without pixi.\n\n"
        "To fix this, try one of the following:\n"
        "  - Recompile using pixi (recommended): https://freecad.github.io/DevelopersHandbook/gettingstarted/#pixi\n"
        "  - Use a FreeCAD AppImage, Flatpak, or package from freecad.org\n"
        "  - On Windows/Mac, the standard installer includes pythonocc"
    )
    App.Console.PrintUserError(msg + "\n")
    raise ImportError(msg)
