# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ..contour.zebra_panel import ICON, ZebraTaskPanel


class ZebraStripesCommand:
    def GetResources(self):
        return {
            "Pixmap": ICON,
            "MenuText": "Zebra Stripes",
            "ToolTip": "Show reflection stripes on the model to check how smoothly its faces "
            "join. Select faces first to stripe only those.",
        }

    def Activated(self):
        Gui.Control.showDialog(ZebraTaskPanel())

    def IsActive(self):
        return App.ActiveDocument is not None


if App.GuiUp:
    Gui.addCommand("DFM_ZebraStripes", ZebraStripesCommand())
