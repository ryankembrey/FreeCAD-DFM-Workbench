# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

_ICON = ":/icons/dfm_moldflow.svg"


class MoldFlowAnalysisCommand:
    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Mold Flow Analysis (Extremely Experimental)",
            "ToolTip": "EXTREMELY EXPERIMENTAL. Simulate the plastic filling the mold: "
            "fill time, pressure, weld lines, air traps and confidence of fill. "
            "Results are rough estimates, not for sign-off.",
        }

    def Activated(self):
        from ..moldflow.panel import MoldFlowTaskPanel

        Gui.Control.showDialog(MoldFlowTaskPanel())

    def IsActive(self):
        return App.ActiveDocument is not None


if App.GuiUp:
    Gui.addCommand("DFM_MoldFlowAnalysis", MoldFlowAnalysisCommand())
