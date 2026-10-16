# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""GUI helpers shared by more than one panel or tool.

Import from the submodules directly (``from .utils.view import get_active_view``).
Nothing is re-exported here so importing the package stays free of FreeCAD/Qt side effects.
GUI code only: ``core`` and ``app`` must not import from here.
"""
