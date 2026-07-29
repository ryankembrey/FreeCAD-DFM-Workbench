# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ..contour.scene import clear_all

_ICON = ":/icons/dfm_clear_contour.svg"


class ClearContourCommand:
    def GetResources(self):
        return {
            # "Pixmap": _ICON,
            "MenuText": "Clear Contour",
            "ToolTip": "Remove any contour overlay from the view.",
        }

    def Activated(self):
        clear_all()

    def IsActive(self):
        return App.ActiveDocument is not None


if App.GuiUp:
    Gui.addCommand("DFM_ClearContour", ClearContourCommand())
