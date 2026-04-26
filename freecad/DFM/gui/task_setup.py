# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from pathlib import Path
from typing import Optional
from PySide6 import QtGui, QtWidgets, QtCore

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore
import Part  # type: ignore

from OCC.Core.gp import gp_Dir

from ..core.registries.analyzers_registry import get_analyzer_class
from ..core.registries.checks_registry import get_check_class
from ..core.registries.process_registry import ProcessRegistry
from ..app.analysis_runner import AnalysisRunner
from ..core.models import CheckResult, GeometryRef, ProcessRequirement

from ..app.history import HistoryManager

from ..gui import DFM_rc
from ..gui.results.bridge import DFMViewProvider
from ..gui.results.models import DFMReportModel
from ..gui.results.presenter import TaskResultsPresenter
from ..gui.task_results import TaskResults
from ..gui.visuals import DirectionIndicator


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
            ProcessRequirement.PULL_DIRECTION: [
                self.form.pbPullDir,
                self.form.lePullDir,
                self.form.pbFlipPullDir,
            ],
            ProcessRequirement.NEUTRAL_PLANE: [self.form.pbNPlane, self.form.leNPlane],
        }

        self.orig_btn_texts = {
            "model": self.form.pbSelectModel.text(),
            "pull_dir": self.form.pbPullDir.text(),
            "neutral_plane": self.form.pbNPlane.text(),
        }

        self.target_object = None
        self.target_shape = None

        self.pull_dir = None
        self.pull_dir_pnt = None
        self.indicator = DirectionIndicator()

        self.is_running = False
        self.abort_requested = False
        self.picking_mode: Optional[str] = None
        self.cursor_overridden = False

        Gui.Selection.addObserver(self)

        self.populate_categories()
        self.setup_initial_state()
        self.connect_signals()
        self.auto_select_model()

    def addSelection(self, *args):
        """Triggered by FreeCAD when the user makes a selection in the 3D view or Tree."""
        if not self.picking_mode:
            return

        QtCore.QTimer.singleShot(50, self._process_picked_selection)

    def _process_picked_selection(self):
        """Processes the selection based on the active picking mode."""
        if not self.picking_mode:
            return

        success = False
        if self.picking_mode == "model":
            success = self._apply_model_selection()
        elif self.picking_mode == "pull_dir":
            success = self._apply_pull_dir_selection()
        elif self.picking_mode == "neutral_plane":
            success = self._apply_neutral_plane_selection()

        if success:
            self._reset_picking_ui()
            self.picking_mode = None
            Gui.Selection.clearSelection()

    def _reset_picking_ui(self):
        """Restores the buttons to their default state."""
        self.form.pbSelectModel.setText(self.orig_btn_texts["model"])
        self.form.pbPullDir.setText(self.orig_btn_texts["pull_dir"])
        self.form.pbNPlane.setText(self.orig_btn_texts["neutral_plane"])

        self.form.pbSelectModel.setChecked(False)
        self.form.pbPullDir.setChecked(False)
        self.form.pbNPlane.setChecked(False)

        if self.cursor_overridden:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.cursor_overridden = False

    def _toggle_pick_mode(self, mode: str, button: QtWidgets.QPushButton):
        """Toggles pick mode on/off when a button is clicked."""
        if self.picking_mode == mode:
            self.picking_mode = None
            self._reset_picking_ui()
        else:
            self.picking_mode = mode
            self._reset_picking_ui()
            button.setText("Selecting...")

            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.CrossCursor)
            self.cursor_overridden = True

    def _apply_model_selection(self) -> bool:
        try:
            sel = Gui.Selection.getSelection()
            if not sel:
                return False

            self.target_object = sel[0]
            self.target_shape = self.target_object.Shape
            self.form.leSelectModel.setText(self.target_object.Label)
            return True
        except Exception as e:
            App.Console.PrintError(f"Could not use the selected object. {e}\n")
            return False

    def _apply_pull_dir_selection(self) -> bool:
        try:
            sel = Gui.Selection.getSelectionEx()
            if not sel or not sel[0].SubObjects:
                return False

            sub_obj = sel[0].SubObjects[0]

            if isinstance(sub_obj, Part.Face):
                u0, u1, v0, v1 = sub_obj.ParameterRange
                u, v = (u0 + u1) * 0.5, (v0 + v1) * 0.5
                pnt = sub_obj.valueAt(u, v)
                dir_vec = sub_obj.normalAt(u, v).normalize()

            elif isinstance(sub_obj, Part.Edge):
                p0, p1 = sub_obj.ParameterRange
                p_mid = (p0 + p1) * 0.5
                pnt = sub_obj.valueAt(p_mid)

                tangent = sub_obj.tangentAt(p_mid)
                if tangent.Length == 0:
                    App.Console.PrintError("Selected edge is degenerate.\n")
                    return False
                dir_vec = tangent.normalize()

            else:
                App.Console.PrintError("Selected element must be a Face or an Edge.\n")
                return False

            self.pull_dir_pnt = pnt
            self.pull_dir = gp_Dir(dir_vec.x, dir_vec.y, dir_vec.z)

            self.form.lePullDir.setText(f"[{dir_vec.x:.2f}] [{dir_vec.y:.2f}] [{dir_vec.z:.2f}]")
            self.indicator.show(pnt, dir_vec)

            return True

        except Exception as e:
            App.Console.PrintError(f"Visualizing pull direction failed: {e}\n")
            return False

    def _apply_neutral_plane_selection(self) -> bool:
        try:
            sel = Gui.Selection.getSelectionEx()
            if not sel or not sel[0].SubElementNames:
                return False

            face_name = sel[0].SubElementNames[0]
            face_obj = sel[0].SubObjects[0]

            if not isinstance(face_obj, Part.Face):
                App.Console.PrintError(f"Selected element '{face_name}' is not a face.\n")
                return False

            self.neutral_plane_face = Part.__toPythonOCC__(face_obj)
            self.form.leNPlane.setText(face_name)
            self.form.leNPlane.setReadOnly(True)
            return True
        except Exception as e:
            App.Console.PrintError(f"Selection error: {e}\n")
            return False

    def on_select_shape(self):
        """Attempts to use pre-selection, otherwise enters picking mode."""
        if self._apply_model_selection():
            Gui.Selection.clearSelection()
        else:
            self._toggle_pick_mode("model", self.form.pbSelectModel)

    def on_select_pull_dir(self):
        """Attempts to use pre-selection, otherwise enters picking mode."""
        if self._apply_pull_dir_selection():
            Gui.Selection.clearSelection()
        else:
            self._toggle_pick_mode("pull_dir", self.form.pbPullDir)

    def on_flip_pull_dir(self):
        """Flips the currently defined pull direction 180 degrees."""
        if not self.pull_dir or not self.pull_dir_pnt:
            return

        self.pull_dir.Reverse()

        dx, dy, dz = self.pull_dir.X(), self.pull_dir.Y(), self.pull_dir.Z()

        self.form.lePullDir.setText(f"[{dx:.2f}] [{dy:.2f}] [{dz:.2f}]")

        fc_vector = App.Vector(dx, dy, dz)
        self.indicator.show(self.pull_dir_pnt, fc_vector)

    def on_select_neutral_plane(self):
        """Attempts to use pre-selection, otherwise enters picking mode."""
        if self._apply_neutral_plane_selection():
            Gui.Selection.clearSelection()
        else:
            self._toggle_pick_mode("neutral_plane", self.form.pbNPlane)

    def auto_select_model(self):
        """Automatically selects the active object if one exists upon opening."""
        if len(Gui.Selection.getSelection()) > 0:
            self._apply_model_selection()
            Gui.Selection.clearSelection()

    def populate_categories(self):
        self.form.cbManCategory.addItems(
            ["-- Select a category --"] + self.registry.get_categories()
        )

    def setup_initial_state(self):
        self.form.cbManProcess.addItems(["-- Select a category first --"])
        self.form.cbManProcess.setEnabled(False)
        self.form.cbMaterial.addItems(["-- Select a process first --"])
        self.form.cbMaterial.setEnabled(False)
        self.form.gbOptions.hide()
        self.form.leSelectModel.setReadOnly(True)
        self.form.gbAnalysis.hide()
        w = self.form.pbPullDir.sizeHint().width()
        self.form.pbPullDir.setMinimumWidth(w)
        self.form.lePullDir.setReadOnly(True)
        flip_icon = QtGui.QIcon(":/icons/flip_direction.svg")
        self.form.pbFlipPullDir.setIcon(flip_icon)

    def connect_signals(self):
        self.form.pbRunAnalysis.clicked.connect(self.on_run_analysis)
        self.form.pbAbort.clicked.connect(self.on_run_analysis)
        self.form.pbSelectModel.clicked.connect(self.on_select_shape)
        self.form.cbManCategory.currentIndexChanged.connect(self.on_category_changed)
        self.form.cbManProcess.currentIndexChanged.connect(self.on_process_changed)
        self.form.pbPullDir.clicked.connect(self.on_select_pull_dir)
        self.form.pbNPlane.clicked.connect(self.on_select_neutral_plane)
        self.form.pbFlipPullDir.clicked.connect(self.on_flip_pull_dir)

    def on_category_changed(self):
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

    def get_active_requirements(self) -> set[ProcessRequirement]:
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
                App.Console.PrintWarning(f"Rule '{rule.name}' not found in Rulebook.\n")
                continue
        return requirements

    def on_run_analysis(self):
        self.picking_mode = None
        self._reset_picking_ui()

        if self.is_running:
            self.abort_requested = True
            self.form.pbAbort.setText("Aborting…")
            return

        if not self.target_shape:
            App.Console.PrintError("No model selected to analyze.\n")
            return

        process_name = self.form.cbManProcess.currentData()
        if not process_name:
            App.Console.PrintError("Select a manufacturing process.\n")
            return

        material_name = self.form.cbMaterial.currentText()
        if "--" in material_name:
            App.Console.PrintError("Select a material.\n")
            return

        active_reqs = self.get_active_requirements()

        kwargs = {}

        if ProcessRequirement.PULL_DIRECTION in active_reqs:
            if not self.pull_dir:
                App.Console.PrintError("Pull direction is required.\n")
                return
            kwargs[ProcessRequirement.PULL_DIRECTION.name] = self.pull_dir

        if ProcessRequirement.NEUTRAL_PLANE in active_reqs:
            if not hasattr(self, "neutral_plane_face"):
                App.Console.PrintError("Neutral plane face is required.\n")
                return
            kwargs[ProcessRequirement.NEUTRAL_PLANE.name] = self.neutral_plane_face

        self.is_running = True
        self.abort_requested = False
        self.form.pbRunAnalysis.hide()
        self.form.lProgress.setText("Starting analysis…")
        self.form.progressBar.setValue(0)
        self.form.gbAnalysis.show()

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
                App.Console.PrintMessage("DFM Analysis aborted by user.\n")
                self.form.pbRunAnalysis.show()
            else:
                self.form.lProgress.setText("Mapping results to 3D model…")
                QtWidgets.QApplication.processEvents()
                _resolve_geometry_refs(results, self.target_object)
                self.form.lProgress.setText("Preparing final report…")
                QtWidgets.QApplication.processEvents()
                history_manager = HistoryManager(Path(App.getUserAppDataDir()))
                verdict_text, _ = DFMReportModel(results, self.process, material_name).get_verdict()
                history_manager.save_run(
                    results=results,
                    doc_name=App.ActiveDocument.Name,  # type: ignore
                    shape_name=self.target_object.Label,  # type: ignore
                    process_name=process_name,
                    material=material_name,
                    verdict=verdict_text,
                )
                self.indicator.remove()

                Gui.Selection.removeObserver(self)
                Gui.Control.closeDialog()

                report_model = DFMReportModel(
                    results=results, process=self.process, material=material_name
                )
                view_bridge = DFMViewProvider(self.target_object)
                results_view = TaskResults()
                self.results_presenter = TaskResultsPresenter(
                    view=results_view,
                    model=report_model,
                    bridge=view_bridge,
                    history_manager=history_manager,
                    doc_name=App.ActiveDocument.Name,  # type: ignore
                    shape_name=self.target_object.Label,  # type: ignore
                )

        except Exception as e:
            App.Console.PrintError(f"A critical error occurred during analysis: {e}\n")
            import traceback

            App.Console.PrintError(traceback.format_exc())

        finally:
            self.is_running = False
            self.abort_requested = False

            if self.form:
                try:
                    self.form.lProgress.setText("")
                    self.form.gbAnalysis.hide()
                except RuntimeError:
                    pass

    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.StandardButton.Close

    def reject(self):
        if self.is_running:
            self.abort_requested = True

        self._reset_picking_ui()

        self.indicator.remove()
        Gui.Selection.removeObserver(self)
        Gui.Control.closeDialog()

    def accept(self):
        self.indicator.remove()
        self._reset_picking_ui()

        Gui.Selection.removeObserver(self)
        Gui.Control.closeDialog()


def _resolve_geometry_refs(
    results: list[CheckResult],
    target_object,
) -> None:
    """
    Converts raw OCC objects in CheckResult.failing_geometry into serialisable
    GeometryRef instances stored in CheckResult.refs.

    After this call, failing_geometry is cleared — the GUI layer must use .refs.

    Supports: Face, Edge, Vertex.
    """
    shape = target_object.Shape

    occ_faces = [Part.__toPythonOCC__(f) for f in shape.Faces]
    occ_edges = [Part.__toPythonOCC__(e) for e in shape.Edges]
    occ_vertices = [Part.__toPythonOCC__(v) for v in shape.Vertexes]

    def resolve_one(occ_obj) -> Optional[GeometryRef]:
        # Try Face
        for i, occ_face in enumerate(occ_faces):
            if occ_face.IsSame(occ_obj):
                return GeometryRef(type="Face", index=i, label=f"Face{i + 1}")
        # Try Edge
        for i, occ_edge in enumerate(occ_edges):
            if occ_edge.IsSame(occ_obj):
                return GeometryRef(type="Edge", index=i, label=f"Edge{i + 1}")
        # Try Vertex
        for i, occ_vertex in enumerate(occ_vertices):
            if occ_vertex.IsSame(occ_obj):
                return GeometryRef(type="Vertex", index=i, label=f"Vertex{i + 1}")
        return None

    for result in results:
        resolved = []
        for occ_obj in result.failing_geometry:
            ref = resolve_one(occ_obj)
            if ref is not None:
                resolved.append(ref)
            else:
                App.Console.PrintWarning(
                    f"Could not resolve geometry ref for {result.rule_id.label}\n"
                )
        result.refs = resolved
        result.failing_geometry = []  # clear as OCC objects are no longer needed


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


if App.GuiUp:
    Gui.addCommand("DFM_SetupAnalysis", DfmAnalysisCommand())
