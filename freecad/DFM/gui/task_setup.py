# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from pathlib import Path
from typing import Optional
from PySide6 import QtGui, QtWidgets, QtCore

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore
import Part  # type: ignore

from OCP.gp import gp_Dir

from ..core.registries.analyzers_registry import get_analyzer_class
from ..core.registries.checks_registry import get_check_class
from ..core.registries.process_registry import ProcessRegistry
from ..app.analysis_runner import AnalysisRunner
from ..core.models import CheckResult, GeometryRef, ProcessRequirement
from ..core.utils.conversion import freecad_to_ocp

from ..app.history import HistoryManager

from ..gui import DFM_rc
from ..gui.results.bridge import DFMViewProvider
from ..gui.results.models import DFMReportModel
from ..gui.results.presenter import TaskResultsPresenter
from ..gui.task_results import TaskResults
from ..gui.visuals import DirectionIndicator


PICK_BUTTON_STYLE = """
QPushButton:checked {
    border: 2px solid palette(highlight);
    font-weight: bold;
}
"""

PICK_HINTS = {
    "model": "Click an object",
    "pull_dir": "Click a face or edge",
    "neutral_plane": "Click a face",
}


# =============================================================================


class _EscapeFilter(QtCore.QObject):
    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.KeyPress:
            if event.key() == QtCore.Qt.Key.Key_Escape:
                if self._callback():
                    return True
        return False


# =============================================================================


class TaskSetup:
    def __init__(self):
        self.form = Gui.PySideUic.loadUi(":/ui/task_setup.ui")  # type: ignore
        if not self.form:
            raise RuntimeError("Failed to load task_setup.ui from resources.")
        self.form.setWindowTitle("DFM Analysis")
        self.form.setWindowIcon(QtGui.QIcon(":/icons/dfm_analysis.svg"))

        self.registry = ProcessRegistry.get_instance()

        self.target_object = None
        self.target_shape = None
        self.process = None
        self.pull_dir = None
        self.pull_dir_pnt = None
        self._pull_dir_ref = ""
        self._pull_dir_flipped = False
        self.neutral_plane_face = None
        self.indicator = DirectionIndicator()

        self.is_running = False
        self.abort_requested = False
        self.picking_mode: Optional[str] = None
        self.cursor_overridden = False

        self.requirement_widgets = {
            ProcessRequirement.PULL_DIRECTION: [
                self.form.pbPullDir,
                self.form.lePullDir,
                self.form.pbFlipPullDir,
            ],
            ProcessRequirement.NEUTRAL_PLANE: [
                self.form.pbNPlane,
                self.form.leNPlane,
            ],
        }

        self.orig_btn_texts = {
            "model": self.form.pbSelectModel.text(),
            "pull_dir": self.form.pbPullDir.text(),
            "neutral_plane": self.form.pbNPlane.text(),
        }

        Gui.Selection.addObserver(self)

        self._escape_filter = _EscapeFilter(self._on_escape)
        self.form.installEventFilter(self._escape_filter)

        self._setup_widgets()
        self._populate_categories()
        self._connect_signals()
        self._auto_select_model()
        self._update_run_button_state()

    def _setup_widgets(self):
        self.form.cbManCategory.setPlaceholderText("Select a category")
        self.form.cbManCategory.setCurrentIndex(-1)

        self.form.cbManProcess.setPlaceholderText("Select a category first")
        self.form.cbManProcess.setEnabled(False)

        self.form.cbMaterial.setPlaceholderText("Select a process first")
        self.form.cbMaterial.setEnabled(False)

        for le, placeholder in (
            (self.form.leSelectModel, "No model selected"),
            (self.form.lePullDir, "Not defined"),
            (self.form.leNPlane, "Not defined"),
        ):
            le.setReadOnly(True)
            le.setPlaceholderText(placeholder)

        self.form.gbOptions.hide()
        self.form.gbAnalysis.hide()

        w = self.form.pbPullDir.sizeHint().width()
        self.form.pbPullDir.setMinimumWidth(w)

        self.form.pbFlipPullDir.setIcon(QtGui.QIcon(":/icons/flip_direction.svg"))
        self.form.pbFlipPullDir.setToolTip("Flip pull direction 180 degrees")
        self.form.pbFlipPullDir.setEnabled(False)

        for btn in (self.form.pbSelectModel, self.form.pbPullDir, self.form.pbNPlane):
            btn.setCheckable(True)
            btn.setStyleSheet(PICK_BUTTON_STYLE)

        self.form.progressBar.setValue(0)

    def _populate_categories(self):
        self.form.cbManCategory.blockSignals(True)
        self.form.cbManCategory.clear()
        self.form.cbManCategory.addItems(self.registry.get_categories())
        self.form.cbManCategory.setCurrentIndex(-1)
        self.form.cbManCategory.blockSignals(False)

    def _connect_signals(self):
        self.form.pbRunAnalysis.clicked.connect(self.on_run_clicked)
        self.form.pbAbort.clicked.connect(self.on_abort_clicked)
        self.form.pbSelectModel.clicked.connect(self.on_select_shape)
        self.form.pbPullDir.clicked.connect(self.on_select_pull_dir)
        self.form.pbFlipPullDir.clicked.connect(self.on_flip_pull_dir)
        self.form.pbNPlane.clicked.connect(self.on_select_neutral_plane)
        self.form.cbManCategory.currentIndexChanged.connect(self.on_category_changed)
        self.form.cbManProcess.currentIndexChanged.connect(self.on_process_changed)
        self.form.cbMaterial.currentIndexChanged.connect(self._update_run_button_state)

    def _on_escape(self) -> bool:
        """Cancel picking mode on Escape. Returns True if handled."""
        if self.picking_mode:
            self.picking_mode = None
            self._reset_picking_ui()
            return True
        return False

    def addSelection(self, *args):
        if not self.picking_mode:
            return
        QtCore.QTimer.singleShot(50, self._process_picked_selection)

    def _process_picked_selection(self):
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
            self.picking_mode = None
            self._reset_picking_ui()
            Gui.Selection.clearSelection()
            self._update_run_button_state()

    def _reset_picking_ui(self):
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
        if self.picking_mode == mode:
            self.picking_mode = None
            self._reset_picking_ui()
            return

        self._reset_picking_ui()
        self.picking_mode = mode
        button.setChecked(True)
        button.setText(PICK_HINTS[mode])
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
            sub_name = sel[0].SubElementNames[0] if sel[0].SubElementNames else ""

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
            self._pull_dir_ref = sub_name
            self._pull_dir_flipped = False
            self.form.lePullDir.setText(sub_name)
            self.form.pbFlipPullDir.setEnabled(True)
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

            self.neutral_plane_face = freecad_to_ocp(face_obj)
            self.form.leNPlane.setText(face_name)
            return True
        except Exception as e:
            App.Console.PrintError(f"Selection error: {e}\n")
            return False

    def on_select_shape(self):
        if self._apply_model_selection():
            Gui.Selection.clearSelection()
            self.form.pbSelectModel.setChecked(False)
            self._update_run_button_state()
        else:
            self._toggle_pick_mode("model", self.form.pbSelectModel)

    def on_select_pull_dir(self):
        if self._apply_pull_dir_selection():
            Gui.Selection.clearSelection()
            self.form.pbPullDir.setChecked(False)
            self._update_run_button_state()
        else:
            self._toggle_pick_mode("pull_dir", self.form.pbPullDir)

    def on_select_neutral_plane(self):
        if self._apply_neutral_plane_selection():
            Gui.Selection.clearSelection()
            self.form.pbNPlane.setChecked(False)
            self._update_run_button_state()
        else:
            self._toggle_pick_mode("neutral_plane", self.form.pbNPlane)

    def on_flip_pull_dir(self):
        if not self.pull_dir or not self.pull_dir_pnt:
            return
        self.pull_dir.Reverse()
        self._pull_dir_flipped = not self._pull_dir_flipped
        suffix = " (flipped)" if self._pull_dir_flipped else ""
        self.form.lePullDir.setText(f"{self._pull_dir_ref}{suffix}")
        dx, dy, dz = self.pull_dir.X(), self.pull_dir.Y(), self.pull_dir.Z()
        fc_vector = App.Vector(dx, dy, dz)
        self.indicator.show(self.pull_dir_pnt, fc_vector)

    def _auto_select_model(self):
        if len(Gui.Selection.getSelection()) > 0:
            if self._apply_model_selection():
                assert self.target_object is not None
                Gui.Selection.clearSelection()
                App.Console.PrintMessage(f"DFM: auto-selected {self.target_object.Label}\n")

    def on_category_changed(self):
        selected_category = self.form.cbManCategory.currentText()

        self.form.cbManProcess.blockSignals(True)
        self.form.cbManProcess.clear()
        processes = self.registry.get_processes_for_category(selected_category)
        if processes:
            for process in processes:
                self.form.cbManProcess.addItem(process.name, userData=process.name)
            self.form.cbManProcess.setEnabled(True)
            self.form.cbManProcess.setPlaceholderText("Select a process")
        else:
            self.form.cbManProcess.setEnabled(False)
            self.form.cbManProcess.setPlaceholderText("No processes in this category")
        self.form.cbManProcess.setCurrentIndex(-1)
        self.form.cbManProcess.blockSignals(False)

        self.on_process_changed()

    def on_process_changed(self):
        process_id = self.form.cbManProcess.currentData()

        self.form.cbMaterial.blockSignals(True)
        self.form.cbMaterial.clear()

        if not process_id:
            self.process = None
            self.form.cbMaterial.setEnabled(False)
            self.form.cbMaterial.setPlaceholderText("Select a process first")
            self.form.cbMaterial.setCurrentIndex(-1)
            self.form.cbMaterial.blockSignals(False)
            self.form.gbOptions.hide()
            self._update_run_button_state()
            return

        self.process = self.registry.get_process_by_id(process_id)
        materials = list(self.process.materials.keys())
        if materials:
            self.form.cbMaterial.addItems(materials)
            self.form.cbMaterial.setEnabled(True)
            self.form.cbMaterial.setPlaceholderText("Select a material")
        else:
            self.form.cbMaterial.setEnabled(False)
            self.form.cbMaterial.setPlaceholderText("No materials defined")
        self.form.cbMaterial.setCurrentIndex(-1)
        self.form.cbMaterial.blockSignals(False)

        self._update_options_visibility()
        self._update_run_button_state()

    def _update_options_visibility(self):
        if not self.process:
            self.form.gbOptions.hide()
            return

        requirements = self.get_active_requirements()
        visible_labels = []
        for req, widgets in self.requirement_widgets.items():
            is_needed = req in requirements
            for widget in widgets:
                widget.setVisible(is_needed)
            if is_needed:
                visible_labels.append(req.name.replace("_", " ").title())

        self.form.gbOptions.setVisible(bool(visible_labels))
        if len(visible_labels) == 1:
            self.form.gbOptions.setTitle(visible_labels[0])
        else:
            self.form.gbOptions.setTitle("Parameters")

    def get_active_requirements(self) -> set[ProcessRequirement]:
        requirements = set()
        if not self.process:
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

    def _missing_requirements(self) -> list[str]:
        missing = []
        if not self.target_shape:
            missing.append("target model")
        if not self.process:
            missing.append("manufacturing process")
        if self.form.cbMaterial.currentIndex() < 0:
            missing.append("material")

        if self.process:
            active_reqs = self.get_active_requirements()
            if ProcessRequirement.PULL_DIRECTION in active_reqs and not self.pull_dir:
                missing.append("pull direction")
            if ProcessRequirement.NEUTRAL_PLANE in active_reqs and self.neutral_plane_face is None:
                missing.append("neutral plane")
        return missing

    def _update_run_button_state(self):
        missing = self._missing_requirements()
        btn = self.form.pbRunAnalysis
        if missing:
            btn.setEnabled(False)
            btn.setToolTip("Missing: " + ", ".join(missing))
        else:
            btn.setEnabled(True)
            btn.setToolTip("Starts the analysis for the selected model")

    def on_run_clicked(self):
        self.picking_mode = None
        self._reset_picking_ui()

        missing = self._missing_requirements()
        if missing:
            App.Console.PrintError(f"Cannot run analysis. Missing: {', '.join(missing)}\n")
            return
        self._start_analysis()

    def on_abort_clicked(self):
        if not self.is_running:
            return
        self.abort_requested = True
        self.form.pbAbort.setText("Aborting")
        self.form.pbAbort.setEnabled(False)

    def _start_analysis(self):
        assert self.target_shape is not None
        assert self.target_object is not None
        assert self.process is not None

        active_reqs = self.get_active_requirements()
        kwargs = {}
        if ProcessRequirement.PULL_DIRECTION in active_reqs:
            kwargs[ProcessRequirement.PULL_DIRECTION.name] = self.pull_dir
        if ProcessRequirement.NEUTRAL_PLANE in active_reqs:
            kwargs[ProcessRequirement.NEUTRAL_PLANE.name] = self.neutral_plane_face

        self.is_running = True
        self.abort_requested = False
        self.form.pbRunAnalysis.hide()
        self.form.lProgress.setText("Starting analysis")
        self.form.pbAbort.setText("Abort")
        self.form.pbAbort.setEnabled(True)
        self.form.gbAnalysis.show()

        try:
            results = self._execute_analysis(kwargs)
            if self.abort_requested:
                App.Console.PrintMessage("DFM Analysis aborted by user.\n")
                self.form.pbRunAnalysis.show()
                self._update_run_button_state()
            else:
                self._finish_analysis(results)
        except Exception as e:
            App.Console.PrintError(f"A critical error occurred during analysis: {e}\n")
            import traceback

            App.Console.PrintError(traceback.format_exc())
            self.form.pbRunAnalysis.show()
            self._update_run_button_state()
        finally:
            self._cleanup_after_run()

    def _execute_analysis(self, kwargs) -> list[CheckResult]:
        assert self.target_shape is not None

        def progress_callback(current: int, total: int, message: str = ""):
            if message:
                self.form.lProgress.setText(f"Running: {message}")
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

        process_name = self.form.cbManProcess.currentData()
        material_name = self.form.cbMaterial.currentText()
        runner = AnalysisRunner()
        return runner.run_analysis(
            process_name=process_name,
            material_name=material_name,
            shape=self.target_shape,
            progress_cb=progress_callback,
            check_abort=check_abort,
            **kwargs,
        )

    def _finish_analysis(self, results: list[CheckResult]):
        assert self.target_object is not None
        assert self.process is not None
        self.form.lProgress.setText("Mapping results to 3D model")
        QtWidgets.QApplication.processEvents()
        _resolve_geometry_refs(results)
        self.form.lProgress.setText("Preparing final report")
        QtWidgets.QApplication.processEvents()

        material_name = self.form.cbMaterial.currentText()
        process_name = self.form.cbManProcess.currentData()

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

        report_model = DFMReportModel(results=results, process=self.process, material=material_name)
        view_bridge = DFMViewProvider(self.target_object)
        results_view = TaskResults()

        self.indicator.remove()
        try:
            Gui.Selection.removeObserver(self)
        except Exception:
            pass
        Gui.Control.closeDialog()

        try:
            self.results_presenter = TaskResultsPresenter(
                view=results_view,
                model=report_model,
                bridge=view_bridge,
                history_manager=history_manager,
                doc_name=App.ActiveDocument.Name,  # type: ignore
                shape_name=self.target_object.Label,  # type: ignore
            )
        except Exception as e:
            App.Console.PrintError(f"Failed to open results panel: {e}\n")
            import traceback

            App.Console.PrintError(traceback.format_exc())

    def _cleanup_after_run(self):
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
        try:
            Gui.Selection.removeObserver(self)
        except Exception:
            pass
        Gui.Control.closeDialog()

    def accept(self):
        self._reset_picking_ui()
        self.indicator.remove()
        try:
            Gui.Selection.removeObserver(self)
        except Exception:
            pass
        Gui.Control.closeDialog()


# =============================================================================


def _resolve_geometry_refs(results: list[CheckResult]) -> None:
    for result in results:
        resolved = []
        for kind, index in result.failing_geometry:
            resolved.append(GeometryRef(type=kind, index=index - 1, label=f"{kind}{index}"))
        result.refs = resolved
        result.failing_geometry = []


# =============================================================================


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
