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

from PySide6 import QtGui, QtWidgets, QtCore

import FreeCAD  # type: ignore
import FreeCADGui as Gui  # type: ignore
import Part  # type: ignore

from OCC.Core.gp import gp_Dir

from dfm.registries.analyzers_registry import get_analyzer_class
from dfm.registries.checks_registry import get_check_class
from dfm.registries.process_registry import ProcessRegistry
from app.analysis_runner import AnalysisRunner
from dfm.models import CheckResult, ProcessRequirement
from dfm.rules import Rulebook

from gui.visuals import DirectionIndicator
from . import DFM_rc
from .task_results import (
    TaskResults,
    DFMReportModel,
    DFMViewProvider,
    TaskResultsPresenter,
)


class TaskSetup:
    def __init__(self):
        self.form = Gui.PySideUic.loadUi(":/ui/task_setup.ui")  # type: ignore
        if not self.form:
            raise RuntimeError("Failed to load task_setup.ui from resources.")
        self.form.setWindowTitle("DFM Analysis")
        icon = QtGui.QIcon(":/icons/dfm_analysis.svg")
        self.form.setWindowIcon(icon)

        self.registry = ProcessRegistry.get_instance()

        self.requirement_widgets = {
            ProcessRequirement.PULL_DIRECTION: [self.form.pbPullDir, self.form.lePullDir],
            ProcessRequirement.NEUTRAL_PLANE: [self.form.pbNPlane, self.form.leNPlane],
        }

        self.target_object = None
        self.target_shape = None

        self.pull_dir = None
        self.indicator = DirectionIndicator()

        self.is_running = False
        self.abort_requested = False

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
        self.form.gbOptions.hide()
        self.form.leSelectModel.setReadOnly(True)

        self.form.lProgress.hide()
        self.form.progressBar.hide()

    def connect_signals(self):
        """Connects all widget signals to their handler methods."""
        self.form.pbRunAnalysis.clicked.connect(self.on_run_analysis)
        self.form.pbSelectModel.clicked.connect(self.on_select_shape)
        self.form.cbManCategory.currentIndexChanged.connect(self.on_category_changed)
        self.form.cbManProcess.currentIndexChanged.connect(self.on_process_changed)
        self.form.pbPullDir.clicked.connect(self.on_select_pull_dir)
        self.form.pbNPlane.clicked.connect(self.on_select_neutral_plane)

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
                self.form.cbManProcess.addItem(process.name, userData=process.name)
            self.form.cbManProcess.setEnabled(True)
            self.form.gbOptions.hide()
        else:
            self.form.cbManProcess.addItem("-- Select a category first --")
            self.form.cbManProcess.setEnabled(False)
            self.form.gbOptions.hide()

        self.on_process_changed()

    def on_process_changed(self):
        """Manages the material and parameter selection based on the selected process."""
        self.form.cbMaterial.clear()

        process_id = self.form.cbManProcess.currentData()
        if not process_id:
            self.form.cbMaterial.addItem("-- Select a process first --")
            self.form.cbMaterial.setEnabled(False)
            self.form.gbOptions.hide()
            return

        self.process = self.registry.get_process_by_id(process_id)
        materials = list(self.process.materials.keys())

        if materials:
            self.form.cbMaterial.addItem("-- Select a material --")
            self.form.cbMaterial.addItems(materials)
            self.form.cbMaterial.setEnabled(True)
        else:
            self.form.cbMaterial.addItem("No materials defined")
            self.form.cbMaterial.setEnabled(False)

        requirements = self.get_active_requirements()

        has_any_reqs = any(req in self.requirement_widgets for req in requirements)
        self.form.gbOptions.setVisible(has_any_reqs)

        for req, widgets in self.requirement_widgets.items():
            is_needed = req in requirements
            for widget in widgets:
                widget.setVisible(is_needed)

    def on_select_pull_dir(self):
        try:
            sel = Gui.Selection.getSelectionEx()
            if not sel or not sel[0].SubObjects:
                FreeCAD.Console.PrintError("Please select a face in the 3D view.\n")
                return

            face = sel[0].SubObjects[0]
            if not isinstance(face, Part.Face):
                return

            u0, u1, v0, v1 = face.ParameterRange
            u, v = (u0 + u1) * 0.5, (v0 + v1) * 0.5
            pnt = face.valueAt(u, v)
            normal = face.normalAt(u, v).normalize()

            self.pull_dir = gp_Dir(normal.x, normal.y, normal.z)
            self.form.lePullDir.setText(f"[{normal.x:.2f}] [{normal.y:.2f}] [{normal.z:.2f}]")

            self.indicator.show(pnt, normal)
            Gui.Selection.clearSelection()

        except Exception as e:
            FreeCAD.Console.PrintError(f"Visualizing pull direction failed: {e}\n")

    def on_select_neutral_plane(self):
        """
        Selects and stores the neutral plane.
        """
        try:
            sel = Gui.Selection.getSelectionEx()
            if not sel:
                FreeCAD.Console.PrintError("Nothing selected. Please select a face on the model.\n")
                return

            if not sel[0].SubElementNames:
                FreeCAD.Console.PrintError("Please select a specific face, not the whole object.\n")
                return

            face_name = sel[0].SubElementNames[0]
            face_obj = sel[0].SubObjects[0]

            if not isinstance(face_obj, Part.Face):
                FreeCAD.Console.PrintError(f"Selected element '{face_name}' is not a face.\n")
                return

            self.neutral_plane_face = Part.__toPythonOCC__(face_obj)
            self.form.leNPlane.setText(face_name)
            self.form.leNPlane.setReadOnly(True)

        except Exception as e:
            FreeCAD.Console.PrintError(f"Selection error: {e}\n")

    def get_active_requirements(self) -> set[ProcessRequirement]:
        """
        Returns the set of active process requirements.
        """
        requirements = set()

        if not hasattr(self, "process") or not self.process:
            return requirements

        for rule in self.process.active_rules:
            try:
                check_cls = get_check_class(rule)
                if not check_cls:
                    continue

                analyzer_id = check_cls().required_analyzer_id

                analyzer_cls = get_analyzer_class(analyzer_id)
                if analyzer_cls:
                    requirements.update(analyzer_cls().requirements)

            except KeyError:
                FreeCAD.Console.PrintWarning(f"Rule '{rule.name}' not found in Rulebook.\n")
                continue

        return requirements

    def on_run_analysis(self):
        """Validates inputs and starts the analysis."""

        if self.is_running:
            self.abort_requested = True
            self.form.pbRunAnalysis.setText("Aborting…")
            self.form.pbRunAnalysis.setEnabled(False)
            return

        if not self.target_shape:
            FreeCAD.Console.PrintError("No model selected to analyze.\n")
            return

        process_name = self.form.cbManProcess.currentData()
        if not process_name:
            FreeCAD.Console.PrintError("Select a manufacturing process.\n")
            return

        material_name = self.form.cbMaterial.currentText()
        if "--" in material_name:
            FreeCAD.Console.PrintError("Select a material.\n")
            return

        active_reqs = self.get_active_requirements()

        kwargs = {}

        if ProcessRequirement.PULL_DIRECTION in active_reqs:
            if not self.pull_dir:
                FreeCAD.Console.PrintError("Pull direction is required.\n")
                return
            kwargs[ProcessRequirement.PULL_DIRECTION.name] = self.pull_dir

        if ProcessRequirement.NEUTRAL_PLANE in active_reqs:
            if not hasattr(self, "neutral_plane_face"):
                FreeCAD.Console.PrintError("Neutral plane face is required.\n")
                return
            kwargs[ProcessRequirement.NEUTRAL_PLANE.name] = self.neutral_plane_face

        self.is_running = True
        self.abort_requested = False
        self.form.pbRunAnalysis.setText("Abort Analysis")
        self.form.lProgress.setText("Starting analysis…")
        self.form.lProgress.show()
        self.form.progressBar.setValue(0)
        self.form.progressBar.show()

        def progress_callback(current: int, total: int, message: str = ""):
            if message:
                self.form.lProgress.setText(f"Running: {message}…")

            self.form.progressBar.setMaximum(total)
            self.form.progressBar.setValue(current)

            QtWidgets.QApplication.processEvents(
                QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                | QtCore.QEventLoop.ProcessEventsFlag.WaitForMoreEvents,
                5,
            )

        def check_abort() -> bool:
            QtWidgets.QApplication.processEvents()
            return self.abort_requested

        try:
            runner = AnalysisRunner()
            results: list[CheckResult] = runner.run_analysis(
                process_name=process_name,
                material_name=material_name,
                shape=self.target_shape,
                progress_cb=progress_callback,
                check_abort=check_abort,
                **kwargs,
            )

            if self.abort_requested:
                FreeCAD.Console.PrintMessage("DFM Analysis aborted by user.\n")
            else:
                self.indicator.remove()
                Gui.Control.closeDialog()
                report_model = DFMReportModel(
                    results=results, process=self.process, material=material_name
                )
                view_bridge = DFMViewProvider(self.target_object)
                results_view = TaskResults()
                self.results_presenter = TaskResultsPresenter(
                    view=results_view, model=report_model, bridge=view_bridge
                )

        except Exception as e:
            FreeCAD.Console.PrintError(f"A critical error occurred during analysis: {e}\n")
            import traceback

            FreeCAD.Console.PrintError(traceback.format_exc())

        finally:
            self.is_running = False
            self.abort_requested = False

            if self.form:
                try:
                    self.form.pbRunAnalysis.setText("Run Analysis")
                    self.form.pbRunAnalysis.setEnabled(True)
                    self.form.lProgress.hide()
                    self.form.lProgress.setText("")
                    self.form.progressBar.hide()
                except RuntimeError:
                    pass

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

    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.StandardButton.Close

    def reject(self):
        if self.is_running:
            self.abort_requested = True

        self.indicator.remove()
        Gui.Control.closeDialog()

    def accept(self):
        self.indicator.remove()
        Gui.Control.closeDialog()


class DfmAnalysisCommand:
    def GetResources(self):
        return {
            "Pixmap": ":/icons/dfm_analysis.svg",
            "MenuText": "DFM Analysis",
            "ToolTip": "Opens the DFM Analysis task panel.",
        }

    def Activated(self):
        Gui.Control.showDialog(TaskSetup())

    def IsActive(self):
        return True


if FreeCAD.GuiUp:
    Gui.addCommand("DFM_SetupAnalysis", DfmAnalysisCommand())
