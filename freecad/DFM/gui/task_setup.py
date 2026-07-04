# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from pathlib import Path
from typing import Optional, Any
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


# =============================================================================
# Requirement handlers
#
# Each ProcessRequirement that needs user input owns a handler. The handler
# holds its own state (value / point / reference / flipped) and builds its own
# row of widgets (pick button, status line-edit, optional flip button). This
# keeps all the per-requirement branching in one place and makes adding a new
# requirement a matter of writing one small subclass.
# =============================================================================


class RequirementHandler(QtCore.QObject):
    """Base handler: owns the widgets and state for one ProcessRequirement."""

    def __init__(self, requirement: ProcessRequirement):
        super().__init__()
        self.requirement = requirement

        self.value: Any = None
        self.pnt = None
        self.ref: str = ""
        self.flipped: bool = False

        self.pb: Optional[QtWidgets.QPushButton] = None
        self.le: Optional[QtWidgets.QLineEdit] = None
        self.flip_btn: Optional[QtWidgets.QAbstractButton] = None
        self._orig_text: str = ""

    @property
    def label(self) -> str:
        return self.requirement.name.replace("_", " ").title()

    def pick_hint(self) -> str:
        return "Click a face"

    def build_row(self, layout: QtWidgets.QLayout, on_pick) -> None:
        """Create this requirement's row of widgets and append to `layout`."""
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)

        self.pb = QtWidgets.QPushButton(f"Select {self.label}")
        self.pb.setMinimumHeight(28)
        self.pb.setCheckable(True)
        self.pb.setStyleSheet(PICK_BUTTON_STYLE)
        self._orig_text = self.pb.text()
        self.pb.clicked.connect(lambda _checked=False: on_pick(self))

        self.le = QtWidgets.QLineEdit()
        self.le.setMinimumHeight(28)
        self.le.setReadOnly(True)
        self.le.setPlaceholderText("Not defined")

        right = QtWidgets.QHBoxLayout()
        right.setSpacing(4)
        right.addWidget(self.le, 1)
        self._build_extra(right)

        row.addWidget(self.pb, 1)
        row.addLayout(right, 1)
        layout.addLayout(row)

        self.refresh_display()

    def _build_extra(self, row: QtWidgets.QHBoxLayout) -> None:
        """Hook for subclasses to add trailing widgets (e.g. a flip button)."""
        pass

    def detach(self) -> None:
        """Drop references to widgets that are about to be deleted."""
        self.pb = None
        self.le = None
        self.flip_btn = None

    def refresh_display(self) -> None:
        if self.le is None:
            return
        if self.ref:
            suffix = " (flipped)" if self.flipped else ""
            self.le.setText(f"{self.ref}{suffix}")
        else:
            self.le.setText("")

    def reset_ui(self) -> None:
        """Return the pick button to its idle look."""
        if self.pb is not None:
            self.pb.setText(self._orig_text)
            self.pb.setChecked(False)

    def enter_picking_ui(self) -> None:
        if self.pb is not None:
            self.pb.setText(self.pick_hint())
            self.pb.setChecked(True)

    def apply_selection(self) -> bool:
        """Read the current 3D selection into state. True on success."""
        raise NotImplementedError

    def is_satisfied(self) -> bool:
        return self.value is not None

    def clear(self) -> None:
        self.value = None
        self.pnt = None
        self.ref = ""
        self.flipped = False

    def remove_indicator(self) -> None:
        """Remove any 3D indicator this handler owns. Base handlers have none."""
        pass


# =============================================================================


class VectorRequirement(RequirementHandler):
    """A direction picked from a face normal or an edge tangent (with flip)."""

    _COLORS = {
        ProcessRequirement.PULL_DIRECTION: (1.0, 0.15, 0.15),  # red
        ProcessRequirement.PRINT_ORIENTATION: (0.15, 0.45, 1.0),  # blue
    }

    def __init__(self, requirement):
        super().__init__(requirement)
        color = self._COLORS.get(requirement, (1.0, 0.15, 0.15))
        self.indicator = DirectionIndicator(color, self.label)

    def pick_hint(self) -> str:
        return "Click a face or edge"

    def _build_extra(self, container: QtWidgets.QHBoxLayout) -> None:
        self.flip_btn = QtWidgets.QToolButton()
        self.flip_btn.setIcon(QtGui.QIcon(":/icons/flip_direction.svg"))
        self.flip_btn.setToolTip("Flip direction 180 degrees")
        self.flip_btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.flip_btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.flip_btn.setIconSize(QtCore.QSize(18, 18))
        self.flip_btn.setFixedSize(26, 28)
        self.flip_btn.setStyleSheet(
            "QToolButton { border: none; background: transparent; padding: 0px; margin: 0px; }"
            "QToolButton:hover { background: rgba(127, 127, 127, 40); border-radius: 3px; }"
        )
        self.flip_btn.setEnabled(self.value is not None)
        self.flip_btn.clicked.connect(lambda _checked=False: self.flip())
        container.addWidget(self.flip_btn, 0)

    def refresh_display(self) -> None:
        super().refresh_display()
        if self.flip_btn is not None:
            self.flip_btn.setEnabled(self.value is not None)

    def apply_selection(self) -> bool:
        try:
            sel = Gui.Selection.getSelectionEx()
            if not sel or not sel[0].SubObjects:
                return False

            sub_obj = sel[0].SubObjects[0]
            sub_name = sel[0].SubElementNames[0] if sel[0].SubElementNames else "Selected"

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

            self.pnt = pnt
            self.value = gp_Dir(dir_vec.x, dir_vec.y, dir_vec.z)
            self.ref = sub_name
            self.flipped = False

            if self.le is not None:
                self.le.setText(sub_name)
            if self.flip_btn is not None:
                self.flip_btn.setEnabled(True)

            self.indicator.show(pnt, dir_vec)
            return True

        except Exception as e:
            App.Console.PrintError(f"Visualizing {self.label.lower()} failed: {e}\n")
            return False

    def flip(self) -> None:
        if not self.value or not self.pnt:
            return
        self.value.Reverse()
        self.flipped = not self.flipped
        suffix = " (flipped)" if self.flipped else ""
        if self.le is not None:
            self.le.setText(f"{self.ref}{suffix}")
        dx, dy, dz = self.value.X(), self.value.Y(), self.value.Z()
        self.indicator.show(self.pnt, App.Vector(dx, dy, dz))

    def remove_indicator(self) -> None:
        self.indicator.remove()


# =============================================================================


class PlaneRequirement(RequirementHandler):
    """A reference plane picked from a face."""

    def pick_hint(self) -> str:
        return "Click a face"

    def apply_selection(self) -> bool:
        try:
            sel = Gui.Selection.getSelectionEx()
            if not sel or not sel[0].SubElementNames:
                return False

            face_name = sel[0].SubElementNames[0]
            face_obj = sel[0].SubObjects[0]

            if not isinstance(face_obj, Part.Face):
                App.Console.PrintError(f"Selected element '{face_name}' is not a face.\n")
                return False

            self.value = freecad_to_ocp(face_obj)
            self.ref = face_name
            self.flipped = False

            if self.le is not None:
                self.le.setText(face_name)
            return True

        except Exception as e:
            App.Console.PrintError(f"Selection error: {e}\n")
            return False


# =============================================================================

REQUIREMENT_HANDLER_TYPES: dict[ProcessRequirement, type[RequirementHandler]] = {
    ProcessRequirement.PULL_DIRECTION: VectorRequirement,
    ProcessRequirement.PRINT_ORIENTATION: VectorRequirement,
    ProcessRequirement.NEUTRAL_PLANE: PlaneRequirement,
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

        self.indicator = DirectionIndicator()

        self.handlers: dict[ProcessRequirement, RequirementHandler] = {
            req: cls(req) for req, cls in REQUIREMENT_HANDLER_TYPES.items()
        }

        self.is_running = False
        self.abort_requested = False
        self.picking_mode: Any = None
        self.cursor_overridden = False

        self.orig_model_text = self.form.pbSelectModel.text()

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

        self.form.leSelectModel.setReadOnly(True)
        self.form.leSelectModel.setPlaceholderText("No model selected")

        self.form.pbSelectModel.setCheckable(True)
        self.form.pbSelectModel.setStyleSheet(PICK_BUTTON_STYLE)

        self.form.gbOptions.hide()
        self.form.gbAnalysis.hide()
        self.form.progressBar.setValue(0)

        if not self.form.gbOptions.layout():
            layout = QtWidgets.QVBoxLayout(self.form.gbOptions)
            self.form.gbOptions.setLayout(layout)

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

        if self.picking_mode == "model":
            success = self._apply_model_selection()
        else:
            handler = self.handlers.get(self.picking_mode)
            success = handler.apply_selection() if handler else False

        if success:
            self.picking_mode = None
            self._reset_picking_ui()
            Gui.Selection.clearSelection()
            self._update_run_button_state()

    def _reset_picking_ui(self):
        """Reset the active states of the model button and all requirement buttons."""
        self.form.pbSelectModel.setText(self.orig_model_text)
        self.form.pbSelectModel.setChecked(False)

        for handler in self.handlers.values():
            handler.reset_ui()

        if self.cursor_overridden:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.cursor_overridden = False

    def _toggle_pick_mode_model(self):
        if self.picking_mode == "model":
            self.picking_mode = None
            self._reset_picking_ui()
            return

        self._reset_picking_ui()
        self.picking_mode = "model"
        self.form.pbSelectModel.setChecked(True)
        self.form.pbSelectModel.setText("Click an object")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.cursor_overridden = True

    def on_select_shape(self):
        if self._apply_model_selection():
            Gui.Selection.clearSelection()
            self.form.pbSelectModel.setChecked(False)
            self._update_run_button_state()
        else:
            self._toggle_pick_mode_model()

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

    def _on_requirement_pick(self, handler: RequirementHandler):
        """Pick-button handler shared by every dynamic requirement row."""
        # A second click on the active button cancels picking.
        if self.picking_mode == handler.requirement:
            self.picking_mode = None
            self._reset_picking_ui()
            return

        if handler.apply_selection():
            Gui.Selection.clearSelection()
            handler.reset_ui()
            self._update_run_button_state()
            return

        self._reset_picking_ui()
        self.picking_mode = handler.requirement
        handler.enter_picking_ui()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.cursor_overridden = True

    def _auto_select_model(self):
        if len(Gui.Selection.getSelection()) > 0:
            if self._apply_model_selection():
                assert self.target_object is not None
                Gui.Selection.clearSelection()
                App.Console.PrintMessage(f"DFM: auto-selected {self.target_object.Label}\n")

    def _remove_all_indicators(self):
        for handler in self.handlers.values():
            handler.remove_indicator()

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
            self._update_options_visibility()
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

    def _clear_options_ui(self):
        """Recursively delete every widget from gbOptions and detach handlers."""
        layout = self.form.gbOptions.layout()

        def delete_items(layout_node):
            if layout_node is not None:
                while layout_node.count():
                    item = layout_node.takeAt(0)
                    widget = item.widget()
                    if widget is not None:
                        widget.deleteLater()
                    else:
                        delete_items(item.layout())

        delete_items(layout)

        for handler in self.handlers.values():
            handler.detach()

    def _active_requirements(self) -> list[ProcessRequirement]:
        """Active requirements that have a handler, in a fixed, stable order."""
        active = self.get_active_requirements()

        for req in active:
            if req != ProcessRequirement.NONE and req not in REQUIREMENT_HANDLER_TYPES:
                App.Console.PrintWarning(
                    f"DFM: no input handler for requirement '{req.name}'; it will be skipped.\n"
                )

        return [req for req in REQUIREMENT_HANDLER_TYPES if req in active]

    def _update_options_visibility(self):
        self._clear_options_ui()
        if not self.process:
            self.form.gbOptions.hide()
            return

        active_reqs = self._active_requirements()
        if not active_reqs:
            self.form.gbOptions.hide()
            return

        layout = self.form.gbOptions.layout()
        for req in active_reqs:
            self.handlers[req].build_row(layout, self._on_requirement_pick)

        title = "Parameters" if len(active_reqs) > 1 else self.handlers[active_reqs[0]].label
        self.form.gbOptions.setTitle(title)
        self.form.gbOptions.show()

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
            for req in self._active_requirements():
                if not self.handlers[req].is_satisfied():
                    missing.append(req.name.replace("_", " ").lower())
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

        kwargs = {}
        for req in self._active_requirements():
            kwargs[req.name] = self.handlers[req].value

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

        self._remove_all_indicators()
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
        self._remove_all_indicators()
        try:
            Gui.Selection.removeObserver(self)
        except Exception:
            pass
        Gui.Control.closeDialog()

    def accept(self):
        self._reset_picking_ui()
        self._remove_all_indicators()
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
