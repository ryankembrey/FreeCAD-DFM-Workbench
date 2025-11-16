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
import FreeCADGui as Gui
from PySide6 import QtCore, QtGui, QtWidgets
import Part

from dfm.registries.process_registry import ProcessRegistry
from app.analysis_runner import AnalysisRunner
from dfm.models import CheckResult

from . import DFM_rc
from .task_results import TaskResults


class TaskSetup:
    def __init__(self):
        self.form = Gui.PySideUic.loadUi(":/ui/task_setup.ui")  # type: ignore
        if not self.form:
            raise RuntimeError("Failed to load task_setup.ui from resources.")
        self.form.setWindowTitle("DFM Analysis")
        self.form.leSelectModel.setReadOnly(True)

        self.registry = ProcessRegistry.get_instance()

        self.target_object = None
        self.target_shape = None

        self.populate_categories()
        self.setup_initial_state()
        self.connect_signals()
        self.auto_select_model()

    def populate_categories(self):
        """Populates the category dropdown from the registry."""
        self.form.cbManCategory.addItems(
            ["-- Select a category --"] + self.registry.get_categories()
        )

    def setup_initial_state(self):
        """Sets the default state for conditional dropdowns."""
        self.form.cbManProcess.addItems(["-- Select a category first --"])
        self.form.cbManProcess.setEnabled(False)
        self.form.cbMaterial.addItems(["-- Select a process first --"])
        self.form.cbMaterial.setEnabled(False)

    def connect_signals(self):
        """Connects all widget signals to their handler methods."""
        self.form.pbRunAnalysis.clicked.connect(self.on_run_analysis)
        self.form.pbSelectModel.clicked.connect(self.on_select_shape)
        self.form.cbManCategory.currentIndexChanged.connect(self.on_category_changed)
        self.form.cbManProcess.currentIndexChanged.connect(self.on_process_changed)

    def auto_select_model(self):
        """Automatically selects the active object if one exists."""
        if len(Gui.Selection.getSelection()) > 0:
            self.on_select_shape()

    def on_category_changed(self):
        """Handles changes in the Category dropdown to update the Process dropdown."""
        selected_category = self.form.cbManCategory.currentText()
        self.form.cbManProcess.clear()

        processes = self.registry.get_processes_for_category(selected_category)

        if processes:
            self.form.cbManProcess.addItem("-- Select a process --")
            for process in processes:
                self.form.cbManProcess.addItem(process.name, userData=process.id)
            self.form.cbManProcess.setEnabled(True)
        else:
            self.form.cbManProcess.addItem("-- Select a category first --")
            self.form.cbManProcess.setEnabled(False)

        self.on_process_changed()

    def on_process_changed(self):
        """Handles changes in the Process dropdown to update the Material dropdown."""
        self.form.cbMaterial.clear()

        process_id = self.form.cbManProcess.currentData()
        if not process_id:
            self.form.cbMaterial.addItem("-- Select a process first --")
            self.form.cbMaterial.setEnabled(False)
            return

        process = self.registry.get_process_by_id(process_id)
        materials = list(process.materials.keys())

        if materials:
            self.form.cbMaterial.addItem("-- Select a material --")
            self.form.cbMaterial.addItems(materials)
            self.form.cbMaterial.setEnabled(True)
        else:
            self.form.cbMaterial.addItem("No materials defined")
            self.form.cbMaterial.setEnabled(False)

    def on_run_analysis(self):
        """Validates inputs and starts the analysis."""
        if not self.target_shape:
            FreeCAD.Console.PrintError("No model selected to analyze.\n")
            return

        process_id = self.form.cbManProcess.currentData()
        if not process_id:
            FreeCAD.Console.PrintError("Select a manufacturing process.\n")
            return

        material_name = self.form.cbMaterial.currentText()
        if "--" in material_name:
            FreeCAD.Console.PrintError("Select a material.\n")
            return

        Gui.Control.closeDialog()

        try:
            runner = AnalysisRunner()
            results: list[CheckResult] = runner.run_analysis(
                process_id=process_id, material_name=material_name, shape=self.target_shape
            )

            TaskResults(results, self.target_object, process_id, material=material_name)
        except Exception as e:
            FreeCAD.Console.PrintError(f"A critical error occurred during analysis: {e}\n")

    def on_select_shape(self):
        """Updates the selected shape from the user's selection in the document."""
        try:
            self.target_object = Gui.Selection.getSelection()[0]
            self.target_shape = self.target_object.Shape
            self.form.leSelectModel.setText(self.target_object.Label)
        except IndexError:
            FreeCAD.Console.PrintUserError("Select a shape in the Tree or 3D view first.\n")
        except Exception as e:
            FreeCAD.Console.PrintError(f"Could not use the selected object. {e}\n")
        finally:
            Gui.Selection.clearSelection()


class DfmAnalysisCommand:
    def GetResources(self):
        return {
            "Pixmap": "",
            "MenuText": "DFM Analysis",
            "ToolTip": "Opens the DFM Analysis task panel.",
        }

    def Activated(self):
        Gui.Control.showDialog(TaskSetup())

    def IsActive(self):
        return True


if FreeCAD.GuiUp:
    Gui.addCommand("DFM_SetupAnalysis", DfmAnalysisCommand())
