# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

_ICON = ":/icons/dfm_symmetry.svg"


class SymmetryCheckCommand:
    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Symmetry Check",
            "ToolTip": "See whether the part is symmetric about a plane or an axis, and what isn’t.",
        }

    def Activated(self):
        from ..inspection.symmetry_panel import SymmetryPanel

        Gui.Control.showDialog(SymmetryPanel())

    def IsActive(self):
        return App.ActiveDocument is not None


if App.GuiUp:
    Gui.addCommand("DFM_SymmetryCheck", SymmetryCheckCommand())
