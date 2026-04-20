# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath("../../"))


def mock_or(self, other):
    return self


MagicMock.__or__ = mock_or
MagicMock.__ror__ = mock_or

project = "FreeCAD DFM Workbench"
copyright = "2026, Ryan Kembrey"
author = "Ryan Kembrey"
release = "0.1-dev"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.githubpages",
]

autodoc_mock_imports = [
    "FreeCAD",
    "FreeCADGui",
    "Part",
    "Mesh",
    "PySide6",
    "OCC",
    "numpy",
    "pandas",
    "yaml",
    "picy",
]

html_theme = "furo"
# html_static_path = ["_static"]
