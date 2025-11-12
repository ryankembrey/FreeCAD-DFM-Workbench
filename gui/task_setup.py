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
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face
import Part

from . import DFM_rc

from app.runner import run_draft, run_thickness
from .task_results import TaskResults


process_data = {
    "Additive Manufacturing": [
        "-- Select a process --",
        "Fused Deposition Modeling (FDM)",
        "Stereolithography (SLA)",
        "Selective Laser Sintering (SLS)",
        "Multi Jet Fusion (MJF)",
        "Direct Metal Laser Sintering (DMLS)",
    ],
    "Injection Molding": [
        "-- Select a process --",
        "Plastic Injection Molding",
        "Metal Injection Molding (MIM)",
        "Liquid Silicone Rubber (LSR) Molding",
        "Insert Molding",
        "Overmolding",
    ],
    "Machining": [
        "-- Select a process --",
        "3-Axis CNC Milling",
        "5-Axis CNC Milling",
        "CNC Turning (Lathe)",
        "Electrical Discharge Machining (EDM)",
    ],
    "Sheet Metal": ["-- Select a process --", "Bending / Forming", "Laser Cutting", "Punching"],
}


class TaskSetup:
    def __init__(self):
        self.form = Gui.PySideUic.loadUi(":/ui/task_setup.ui")
        self.form.setWindowTitle("DFM Analysis")

        self.form.leSelectModel.setReadOnly(True)

        self.list_of_categories = [
            "-- Select a process --",
            "Injection molding",
            "Machining",
            "Additive manufacturing",
        ]

        self.list_of_injection_molding = ["Plastic injection molding", "Metal injection moulding"]
        self.list_of_machining = ["CNC machining"]

        if len(Gui.Selection.getSelection()) > 0:
            try:
                doc_object = Gui.Selection.getSelection()[0]
                self.target_object = doc_object
                self.target_shape = doc_object.Shape
                self.form.leSelectModel.setText(doc_object.Label)
            except Exception as e:
                FreeCAD.Console.PrintError(f"{e}\n")

        self.form.pbRunAnalysis.clicked.connect(self.on_run_analysis)
        self.form.pbSelectModel.clicked.connect(self.on_select_shape)
        self.form.cbManCategory.addItems(["-- Select a category --"] + list(process_data.keys()))

        self.form.cbManProcess.addItems(["-- Select a process --"])
        self.form.cbManProcess.setEnabled(False)

        self.form.cbManCategory.currentIndexChanged.connect(self.on_category_changed)

        self.form.pbRunAnalysis.clicked.connect(self.on_run_analysis)

        self.form.cbMaterial.addItems(["-- Select a material --", "PLA", "ABS"])

    def on_category_changed(self):
        selected_category = self.form.cbManCategory.currentText()

        self.form.cbManProcess.clear()

        processes_to_show = process_data.get(selected_category, [])

        if processes_to_show:
            self.form.cbManProcess.addItems(processes_to_show)
            self.form.cbManProcess.setEnabled(True)
        else:
            self.form.cbManProcess.addItems(["-- Select a process"])
            self.form.cbManProcess.setEnabled(False)

    def on_run_analysis(self):
        if not self.target_shape:
            FreeCAD.Console.PrintError("No model selected to analyze\n")
            return

        if "-- Select a process --" in self.form.cbManProcess.currentText():
            FreeCAD.Console.PrintError("Select a manufacturing process\n")
            return

        if "-- Select a material --" in self.form.cbMaterial.currentText():
            FreeCAD.Console.PrintError("Select a material\n")
            return

        process: str = self.form.cbManProcess.currentText()
        Gui.Control.closeDialog()

        try:
            results = run_draft(self.target_shape)
            TaskResults(results, self.target_object, process)
        except Exception as e:
            FreeCAD.Console.PrintError(f"{e}\n")

    def on_select_shape(self):
        print("Trying to select a model")
        try:
            doc_object = Gui.Selection.getSelection()[0]
            self.target_object = doc_object
            self.target_shape = doc_object.Shape
            self.form.leSelectModel.setText(doc_object.Label)
        except IndexError:
            FreeCAD.Console.PrintUserError("Select a shape in the Tree or 3D view first\n")
            return
        except Exception as e:
            FreeCAD.Console.PrintError(f"Could not get a valid shape to analyze. {e}\n")
            return


class DfmAnalysisCommand:
    def GetResources(self):
        return {
            "Pixmap": "",
            "MenuText": "Run Analysis",
            "ToolTip": "Opens the DFM Analysis Setup task panel.",
        }

    def Activated(self):
        Gui.Control.showDialog(TaskSetup())

    def IsActive(self):
        return True


if FreeCAD.GuiUp:
    Gui.addCommand("DFM_SetupAnalysis", DfmAnalysisCommand())
