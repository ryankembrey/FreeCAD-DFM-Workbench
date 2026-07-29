# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import FreeCADGui as Gui  # type: ignore

from .gui import task_setup, task_show_normals, process_library
from .gui import preferences
from .gui.preferences import DFMPreferencesGeneral, DFMPreferencesAnalyzers
from .gui import tools as _dfm_contour_tools


class DFMWorkbench(Gui.Workbench):
    MenuText = "DFM"
    ToolTip = "Design for manufacturing workbench"
    Icon = ":/icons/dfm_analysis.svg"

    def Initialize(self):
        """This function is executed when the workbench is first activated.
        It is executed once in a FreeCAD session followed by the Activated function.
        """

        self.list = [
            "DFM_SetupAnalysis",
            "DFM_ProcessLibrary",
            "DFM_DraftContour",
            "DFM_ClearContour",
        ]
        self.appendToolbar("DFM Analysis", self.list)
        self.appendMenu("DFM", self.list)

    def Activated(self):
        """This function is executed whenever the workbench is activated"""
        return

    def Deactivated(self):
        """This function is executed whenever the workbench is deactivated"""
        return

    def ContextMenu(self, recipient):
        """This function is executed whenever the user right-clicks on screen"""
        # "recipient" will be either "view" or "tree"
        # self.appendContextMenu("DFM_RunAnalysis", self.list)  # add commands to the context menu

    def GetClassName(self):
        # This function is mandatory if this is a full Python workbench
        # This is not a template, the returned string should be exactly "Gui::PythonWorkbench"
        return "Gui::PythonWorkbench"


Gui.addWorkbench(DFMWorkbench())
Gui.addPreferencePage(DFMPreferencesGeneral, "DFM")
Gui.addPreferencePage(DFMPreferencesAnalyzers, "DFM")
