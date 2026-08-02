# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ..contour.panel import ContourTaskPanel
from ...app.contour.measures import DraftMeasure

_ICON = ":/icons/dfm_draft_contour.svg"


class DraftAnalysisCommand:
    def GetResources(self):
        return {
            # "Pixmap": _ICON,
            "MenuText": "Draft Analysis",
            "ToolTip": "Color the model by draft angle on a uniform mesh.",
        }

    def Activated(self):
        Gui.Control.showDialog(ContourTaskPanel(DraftMeasure(), "Draft Analysis", _ICON))

    def IsActive(self):
        return App.ActiveDocument is not None


if App.GuiUp:
    Gui.addCommand("DFM_DraftAnalysis", DraftAnalysisCommand())
