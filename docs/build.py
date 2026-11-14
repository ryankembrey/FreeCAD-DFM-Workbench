# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Regenerate everything in docs/ that is made from the workbench's code.

    python docs/build.py            write what changed
    python docs/build.py --check    write nothing; exit 1 if anything is out of date

Usually run as ``pixi run docs`` (this, then the site build). It needs no
FreeCAD: the workbench is imported behind the test suite's FreeCAD stand-ins,
and the built-in process files are read directly, so nothing in your own
FreeCAD settings or Process Library changes the output.

What it writes, and from where, is listed in docs/README.md.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _pipeline.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
