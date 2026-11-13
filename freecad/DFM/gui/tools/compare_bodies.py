# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

_ICON = ":/icons/dfm_compare.svg"


class CompareBodiesCommand:
    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Compare Bodies",
            "ToolTip": "See what changed between two bodies, and where, in the 3D view.",
        }

    def Activated(self):
        from ..inspection.compare_panel import ComparePanel

        Gui.Control.showDialog(ComparePanel())

    def IsActive(self):
        return App.ActiveDocument is not None


if App.GuiUp:
    Gui.addCommand("DFM_CompareBodies", CompareBodiesCommand())
