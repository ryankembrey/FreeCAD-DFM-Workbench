# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ..contour.panel import ContourTaskPanel
from ...app.contour.measures import UndercutMeasure

_ICON = ":/icons/dfm_undercut.svg"


class UndercutAnalysisCommand:
    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Undercut Analysis",
            "ToolTip": "Color the model by the direction each face releases from the mold, "
            "showing undercuts and the faces side actions free.",
        }

    def Activated(self):
        Gui.Control.showDialog(ContourTaskPanel(UndercutMeasure(), "Undercut Analysis", _ICON))

    def IsActive(self):
        return App.ActiveDocument is not None


if App.GuiUp:
    Gui.addCommand("DFM_UndercutAnalysis", UndercutAnalysisCommand())
