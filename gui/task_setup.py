#  ***************************************************************************
#  *   Copyright (c) 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>              *
#  *                                                                         *
#  *   This file is part of the FreeCAD CAx development system.              *
#  *                                                                         *
#  *   This library is free software; you can redistribute it and/or         *
#  *   modify it under the terms of the GNU Library General Public           *
#  *   License as published by the Free Software Foundation; either          *
#  *   version 2 of the License, or (at your option) any later version.      *
#  *                                                                         *
#  *   This library  is distributed in the hope that it will be useful,      *
#  *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
#  *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
#  *   GNU Library General Public License for more details.                  *
#  *                                                                         *
#  *   You should have received a copy of the GNU Library General Public     *
#  *   License along with this library; see the file COPYING.LIB. If not,    *
#  *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
#  *   Suite 330, Boston, MA  02111-1307, USA                                *
#  *                                                                         *
#  ***************************************************************************

import FreeCAD
import FreeCADGui
from PySide6 import QtCore, QtGui, QtWidgets

from . import DFM_rc
from registry import dfm_registry

from runner import main
from data_types import CheckType
from typing import List


# ==============================================================================
# THE TASK PANEL CLASS
# ==============================================================================
class DfmTaskPanel:
    """The user interface for the DFM Workbench, loaded from a Qt Resource."""

    def __init__(self):
        self.form = FreeCADGui.PySideUic.loadUi(":/ui/task_setup.ui")
        self.form.setWindowTitle("DFM Analysis Setup")
        self.hi = FreeCADGui.PySideUic.loadUi(":/ui/task_setup.ui")
        self.hi.setWindowTitle("DFM Analysis Results")

        self.draft_angle = 5
        all_processes = sorted(list(dfm_registry._processes.keys()))
        self.form.cbProcesses.addItems(["-- Select a Process --"] + all_processes)

        self.form.leTargetName.setText("shape001")
        self.form.lePullDir.setText("face001")
        # self.form.cbProcesses.setCurrentText("Injection Molding")
        self.form.dsbMinDraftAngle.setValue(self.draft_angle)
        self.form.btnSelectTarget.clicked.connect(self.select_target)
        self.form.btnSelectPullDir.clicked.connect(self.select_pull_direction)
        self.form.btnRunAnalysis.clicked.connect(self.run_analysis_clicked)
        self.populate_checks_list()

    def run_analysis_clicked(self):
        main()

    def select_target(self):
        print("select target clicked")

    def select_pull_direction(self):
        print("select pull direction clicked")

    def populate_checks_list(self):
        """
        Clears and repopulates the 'Checks to Run' QListWidget with checkboxes
        for all registered DFM checks.
        """
        list_widget = self.form.listChecksToRun
        if not list_widget:
            print("WARNING: 'listChecksToRun' widget not found in UI file.")
            return

        list_widget.clear()

        all_checks = dfm_registry._checks

        unique_check_instances = sorted(
            set(all_checks.values()), key=lambda x: x.handled_check_types[0].name
        )

        for check_instance in unique_check_instances:
            for check_type in check_instance.handled_check_types:
                list_item = QtWidgets.QListWidgetItem(list_widget)
                checkbox = QtWidgets.QCheckBox(check_type.name)
                checkbox.setProperty("check_type_enum", check_type)
                checkbox.setChecked(True)
                list_widget.setItemWidget(list_item, checkbox)

    def get_selected_checks(self) -> List[CheckType]:
        """
        A helper function to read the state of the checkboxes and return a
        list of the CheckType enums that the user has enabled.
        """
        selected_checks = []
        list_widget = self.form.listChecksToRun

        for i in range(list_widget.count()):
            list_item = list_widget.item(i)

            checkbox = list_widget.itemWidget(list_item)

            if checkbox and checkbox.isChecked():
                check_type = checkbox.property("check_type_enum")
                selected_checks.append(check_type)

        return selected_checks

    def reject(self):
        """Called when the user clicks Cancel or Esc."""
        return True

    def GetClassName(self):
        """A required method for FreeCAD task panels."""
        return "Gui::PythonTaskView"


# ==============================================================================
# THE COMMAND CLASS (No changes needed here)
# ==============================================================================
class DfmAnalysisCommand:
    def GetResources(self):
        return {
            "Pixmap": ":/icons/fem_postpipeline_from_result.svg",
            "MenuText": "Run DFM Analysis",
            "ToolTip": "Opens the DFM Analysis task panel.",
        }

    def Activated(self):
        FreeCADGui.Control.showDialog(DfmTaskPanel())

    def IsActive(self):
        return True


# ==============================================================================
# REGISTRATION (No changes needed here)
# ==============================================================================
if FreeCAD.GuiUp:
    FreeCADGui.addCommand("DFM_RunAnalysis", DfmAnalysisCommand())
