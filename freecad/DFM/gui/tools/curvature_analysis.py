# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ..contour.panel import ContourTaskPanel
from ...app.contour.measures import CurvatureMeasure

_ICON = ":/icons/dfm_curvature.svg"


class CurvatureAnalysisCommand:
    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Curvature Analysis",
            "ToolTip": "Color the model by its tightest radius of curvature, "
            "taken from the exact CAD surfaces.",
        }

    def Activated(self):
        Gui.Control.showDialog(ContourTaskPanel(CurvatureMeasure(), "Curvature Analysis", _ICON))

    def IsActive(self):
        return App.ActiveDocument is not None


if App.GuiUp:
    Gui.addCommand("DFM_CurvatureAnalysis", CurvatureAnalysisCommand())
