# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ..contour.panel import ContourTaskPanel
from ...contour.measures import ThicknessMeasure

_ICON = ":/icons/dfm_draft_contour.svg"


class ThicknessAnalysisCommand:
    def GetResources(self):
        return {
            # "Pixmap": _ICON,
            "MenuText": "Thickness Analysis",
            "ToolTip": "Color the model by wall thickness on a uniform mesh.",
        }

    def Activated(self):
        Gui.Control.showDialog(ContourTaskPanel(ThicknessMeasure(), "Thickness Analysis", _ICON))

    def IsActive(self):
        return App.ActiveDocument is not None


if App.GuiUp:
    Gui.addCommand("DFM_ThicknessAnalysis", ThicknessAnalysisCommand())
