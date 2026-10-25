# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import csv

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import QFileDialog, QMessageBox

from ...app.history import HistoryManager
from ...app.document_object import (
    save_results_to_document,
    find_dfm_results_object,
)

from ...core.models import CheckResult, Severity
from ...core.registries.process_registry import ProcessRegistry
from ...app.prefs import dfm_params
from ...gui.results.bridge import DFMViewProvider
from ...gui.results.delegates import HistoryRowDelegate
from ...gui.results.models import DFMReportModel
from ...gui.results.utils import CSVResultExporter, CSVExportConfig
from ...gui.results.viewport_markers import ViewportMarkerController
from ...gui.results.visuals import severity_color
from ...gui.results.widgets import DFMSparkline
from ...gui.task_results import TaskResults


class TaskResultsPresenter:
    """Presenter for the TaskResults view. Handles UI updates and event handling."""

    def __init__(
        self,
        view: TaskResults,
        model: DFMReportModel,
        bridge: DFMViewProvider,
        history_manager: HistoryManager,
        doc_name="",
        shape_name="",
        runner=None,
        run_settings: dict | None = None,
    ):
        self.view = view
        self.model = model
        self.bridge = bridge
        self.runner = runner
        self.run_settings = run_settings or {}

        self.view.on_row_selected = self.handle_selection
        self.view.on_row_double_clicked = self.handle_zoom
        self.view.on_toggle_ignore = self.handle_ignore
        self.view.on_export_clicked = self.handle_export
        self.view.on_closed = self.handle_cleanup
        self.view.on_save_clicked = self.handle_save
        self.view.on_toggle_ignore_all = self.handle_ignore_all
        self.view.on_zoom_to_rule = self.handle_zoom_to_rule
        self.view.on_material_changed = self.handle_material_changed
        self.view.on_filters_changed = self._handle_filters_changed
        self.view.on_markers_toggled = self._handle_markers_toggled

        self.history_manager = history_manager
        self.doc_name = doc_name
        self.shape_name = shape_name

        # Guard flag to prevent circular updates between tree and markers.
        self._syncing = False

        # Viewport marker overlay.
        self._marker_controller = ViewportMarkerController(
            target_object=bridge.target_object,
            on_finding_selected=self._handle_marker_finding_selected,
            on_finding_hover_started=self._handle_marker_hover_started,
            on_finding_hover_ended=self._handle_marker_hover_ended,
        )

        # Read default marker visibility from preferences.
        self._markers_active = dfm_params().GetBool("ResultsShowMarkersDefault", False)

        # Material switching cache: material_name -> list[CheckResult]
        self._material_cache: dict[str, list[CheckResult]] = {}
        self._current_material = model.material
        # Cache the initial results
        self._material_cache[self._current_material] = list(model.results)

        self._populate_material_combo()
        self.build_history_tab()

        self.refresh_ui()
        Gui.Control.showDialog(self.view)

    def _populate_material_combo(self):
        """Fill the material combo box with available materials for this process."""
        combo = self.view.form.cbMaterialSwitch
        combo.blockSignals(True)
        combo.clear()

        process = self.model.process
        materials = list(process.materials.keys())
        combo.addItems(materials)

        # Select the current material
        idx = combo.findText(self._current_material)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setCurrentIndex(0)

        combo.blockSignals(False)
        combo.currentTextChanged.connect(self._on_material_combo_changed)

    def _on_material_combo_changed(self, text: str):
        if self.view.on_material_changed:
            self.view.on_material_changed(text)

    def refresh_ui(self):
        self.view.form.leTarget.setText(self.bridge.target_object.Label)
        self.view.form.leProcess.setText(self.model.process.name)
        self.view.form.leTarget.setCursorPosition(0)
        self.view.form.leProcess.setCursorPosition(0)

        text, color = self.model.get_verdict()
        self.view.form.leVerdict.setText(text)
        self.view.form.leVerdict.setStyleSheet(f"color: {color}; font-weight: bold;")

        self.view.render_tree(
            self.model.get_grouped_results(),
            self.model.process.active_rules,
            self.model.process.get_criticality,
        )

        # Activate viewport markers only when the toggle is on.
        if self._markers_active:
            self._marker_controller.activate(
                self.model.results,
                show_errors=self.view.show_errors,
                show_warnings=self.view.show_warnings,
                show_ignored=self.view.show_ignored,
            )
        else:
            self._marker_controller.deactivate()

    def handle_selection(self, data: CheckResult | list[CheckResult]):
        if self._syncing:
            return

        Gui.Selection.clearSelection()
        if isinstance(data, list) and len(data) == 0:
            self.bridge.highlight_faces_and_edges_by_index([], [])
            self._marker_controller.clear_highlight()
            return
        elif isinstance(data, list):
            face_pairs = [
                (ref.index, severity_color(r.severity))
                for r in data
                if not r.ignore
                for ref in r.refs
                if ref.type == "Face"
            ]
            edge_pairs = [
                (ref.index, severity_color(r.severity))
                for r in data
                if not r.ignore
                for ref in r.refs
                if ref.type == "Edge"
            ]
            self.bridge.highlight_faces_and_edges_by_index(face_pairs, edge_pairs)
            self._marker_controller.clear_highlight()

        elif isinstance(data, CheckResult):
            color = severity_color(data.severity)

            face_refs = [ref for ref in data.refs if ref.type == "Face"]
            edge_refs = [ref for ref in data.refs if ref.type == "Edge"]

            self.bridge.highlight_faces_and_edges_by_index(
                [(ref.index, color) for ref in face_refs],
                [(ref.index, color) for ref in edge_refs],
            )

            self.bridge.show_finding_overlay(data, color)

            # Highlight the corresponding viewport marker.
            self._marker_controller.highlight_finding(data)

    def handle_zoom_to_rule(self, findings: list[CheckResult]):
        face_pairs = [
            (ref.index, severity_color(r.severity))
            for r in findings
            if not r.ignore
            for ref in r.refs
            if ref.type == "Face"
        ]
        edge_pairs = [
            (ref.index, severity_color(r.severity))
            for r in findings
            if not r.ignore
            for ref in r.refs
            if ref.type == "Edge"
        ]
        self.bridge.highlight_faces_and_edges_by_index(face_pairs, edge_pairs)
        self.bridge.zoom_to_selection()

    def handle_zoom(self, result: CheckResult):
        self.handle_selection(result)
        self.bridge.zoom_to_selection()

    def handle_ignore(self, result: CheckResult):
        self.model.toggle_ignore_state(result)
        self.refresh_ui()

    def handle_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self.view.form, "Export CSV", "", "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"

        config = self.view.get_export_config()

        if CSVResultExporter.export(
            path,
            self.bridge.target_object.Label,
            self.model,
            config,
            self.model.process.get_criticality,
        ):
            QMessageBox.information(self.view.form, "Done", "Export Successful")

    def handle_cleanup(self):
        self._marker_controller.deactivate()
        self.bridge.restore()
        Gui.Selection.addSelection(self.bridge.target_object)
        Gui.Selection.clearSelection()

    def handle_save(self):
        """Save results to the FreeCAD document as a tree object."""
        doc = App.ActiveDocument
        if not doc:
            QMessageBox.warning(
                self.view.form, "No Document", "No active document to save results to."
            )
            return

        verdict_text, _ = self.model.get_verdict()

        obj = save_results_to_document(
            doc=doc,
            target_object=self.bridge.target_object,
            process_name=self.model.process.name,
            material_name=self._current_material,
            verdict=verdict_text,
            results=self.model.results,
            run_settings=self.run_settings,
        )

        # Also save to legacy history for the history tab
        self.history_manager.save_run(
            results=self.model.results,
            doc_name=self.doc_name,
            shape_name=self.shape_name,
            process_name=self.model.process.name,
            material=self._current_material,
            verdict=verdict_text,
        )

        App.Console.PrintNotification(f"DFM results saved to '{obj.Label}'.")

    def handle_material_changed(self, new_material: str):
        """Switch material: rerun checkers only (not analyzers), with caching."""
        if new_material == self._current_material:
            return

        # Check cache first
        if new_material in self._material_cache:
            self._switch_to_cached_material(new_material)
            return

        # Need to rerun checks with the new material
        if not self.runner or not self.runner.analyzer_cache:
            reply = QMessageBox.question(
                self.view.form,
                "Rerun Required",
                f"Analyzer cache is not available.\n\n"
                f"Run a full analysis with material '{new_material}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._launch_rerun(new_material)
                return
            # Reset combo to current material
            combo = self.view.form.cbMaterialSwitch
            combo.blockSignals(True)
            idx = combo.findText(self._current_material)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)
            return

        # Rerun checks only
        process_name = self.model.process.name

        try:
            new_results = self.runner.rerun_checks_only(
                process_name=process_name,
                material_name=new_material,
                **self.run_settings,
            )

            # Resolve geometry refs for new results
            from ...gui.task_setup import _resolve_geometry_refs

            _resolve_geometry_refs(new_results)

            # Cache the results
            self._material_cache[new_material] = new_results
            self._switch_to_cached_material(new_material)

        except Exception as e:
            App.Console.PrintError(f"Failed to rerun checks for material '{new_material}': {e}\n")
            import traceback

            App.Console.PrintError(traceback.format_exc())

            # Reset combo
            combo = self.view.form.cbMaterialSwitch
            combo.blockSignals(True)
            idx = combo.findText(self._current_material)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def _launch_rerun(self, material_name: str | None = None):
        """Close results and launch a full analysis with presets.

        Deferred to the next event-loop tick so the current signal handler
        and any open message box have fully unwound before the dialog is
        torn down and replaced.
        """
        process_name = self.model.process.name
        material = material_name or self._current_material
        target = self.bridge.target_object

        def _do_rerun():
            from ...gui.task_setup import TaskSetup

            self.bridge.restore()
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(target)
            Gui.Control.closeDialog()

            task = TaskSetup(
                preset_process=process_name,
                preset_material=material,
                auto_run=True,
            )
            Gui.Control.showDialog(task)

        QtCore.QTimer.singleShot(0, _do_rerun)

    def _switch_to_cached_material(self, material_name: str):
        """Switch the display to a cached material's results."""
        self._current_material = material_name
        cached_results = self._material_cache[material_name]

        # Update the model
        self.model = DFMReportModel(
            results=cached_results,
            process=self.model.process,
            material=material_name,
        )

        self.refresh_ui()

    def handle_ignore_all(self, findings: list[CheckResult]):
        for finding in findings:
            self.model.toggle_ignore_state(finding)
        self.refresh_ui()

    # ── Viewport marker integration ─────────────────────

    def _handle_filters_changed(self) -> None:
        """Update viewport markers when toolbar filter toggles change."""
        if self._markers_active:
            self._marker_controller.update_filters(
                show_errors=self.view.show_errors,
                show_warnings=self.view.show_warnings,
                show_ignored=self.view.show_ignored,
            )

    def _handle_marker_finding_selected(self, finding: CheckResult) -> None:
        """Handle a finding selection originating from a viewport marker click."""
        if finding not in self.model.results:
            return
        self._syncing = True
        try:
            # Select the finding in the tree (scrolls and expands ancestors).
            self.view.select_finding(finding)

            # Trigger the same highlight + overlay as a tree click.
            color = severity_color(finding.severity)
            face_refs = [ref for ref in finding.refs if ref.type == "Face"]
            edge_refs = [ref for ref in finding.refs if ref.type == "Edge"]
            self.bridge.highlight_faces_and_edges_by_index(
                [(ref.index, color) for ref in face_refs],
                [(ref.index, color) for ref in edge_refs],
            )

            self.bridge.show_finding_overlay(finding, color)
            self._marker_controller.highlight_finding(finding)
        finally:
            self._syncing = False

    def _handle_markers_toggled(self, on: bool) -> None:
        """Toggle viewport markers on or off from the toolbar button."""
        self._markers_active = on
        if on:
            self._marker_controller.activate(
                self.model.results,
                show_errors=self.view.show_errors,
                show_warnings=self.view.show_warnings,
                show_ignored=self.view.show_ignored,
            )
        else:
            self._marker_controller.deactivate()

    def _handle_marker_hover_started(self, marker) -> None:
        """Show the overlay card when hovering over a marker dot."""
        if not self._markers_active:
            return
        # Pick the worst-severity finding for the preview.
        active = [f for f in marker.findings if not f.ignore]
        if not active:
            active = marker.findings
        if not active:
            return
        finding = max(active, key=lambda f: f.severity.value)
        color = severity_color(finding.severity)
        self.bridge.show_finding_overlay(finding, color)

    def _handle_marker_hover_ended(self) -> None:
        """Clear the overlay when the mouse leaves all markers."""
        self.bridge.remove_finding_overlay()

    def build_history_tab(self):
        runs = self.history_manager.load_runs(self.doc_name, self.shape_name)
        if not runs:
            if hasattr(self.view.form, "gbHistoryTrend"):
                self.view.form.gbHistoryTrend.setVisible(False)
            return

        self.sparkline = DFMSparkline()
        recent_runs = runs[-10:]  # Last 10 runs
        e_hist, w_hist, s_hist, n_hist = [], [], [], []

        for run in recent_runs:
            e_hist.append(
                sum(1 for f in run.findings if not f.ignore and f.severity == Severity.ERROR)
            )
            w_hist.append(
                sum(1 for f in run.findings if not f.ignore and f.severity == Severity.WARNING)
            )
            s_hist.append(
                sum(1 for f in run.findings if not f.ignore and f.severity == Severity.SUCCESS)
            )
            n_hist.append(run.run)

        self.sparkline.set_data(e_hist, w_hist, s_hist, n_hist)

        container = self.view.form.gbHistoryTrend
        layout = container.layout()

        if layout is None:
            layout = QtWidgets.QVBoxLayout(container)
            layout.setContentsMargins(4, 8, 4, 4)

        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        layout.addWidget(self.sparkline)

        if len(runs) < 2:
            self.view.form.cbRun1.setEnabled(False)
            self.view.form.cbRun2.setEnabled(False)
            self.view.form.pbExportDiff.setEnabled(False)
            return

        for run in runs:
            self.view.form.cbRun1.addItem(run.label, userData=run.run)
            self.view.form.cbRun2.addItem(run.label, userData=run.run)

        self.view.form.cbRun1.setCurrentIndex(len(runs) - 2)
        self.view.form.cbRun2.setCurrentIndex(len(runs) - 1)

        self.view.form.lwDiffs.setItemDelegate(HistoryRowDelegate())

        self.view.form.pbExportDiff.clicked.connect(self._export_diff)
        self.view.form.cbRun1.currentIndexChanged.connect(self.render_diff)
        self.view.form.cbRun2.currentIndexChanged.connect(self.render_diff)

        self.render_diff()

    def render_diff(self):
        from ...app.history import diff_runs

        a_idx = self.view.form.cbRun1.currentData()
        b_idx = self.view.form.cbRun2.currentData()

        self.view.form.lwDiffs.clear()

        if a_idx is None or b_idx is None or a_idx == b_idx:
            self.view.form.lResolved.setText("—")
            self.view.form.lRegressed.setText("—")
            self.view.form.lUnchanged.setText("—")
            return

        run_a = self.history_manager.load_run(self.doc_name, self.shape_name, a_idx)
        run_b = self.history_manager.load_run(self.doc_name, self.shape_name, b_idx)

        if not run_a or not run_b:
            return

        diffs = diff_runs(run_a, run_b)

        resolved = sum(1 for d in diffs if d.status in ("resolved", "improved"))
        regressed = sum(1 for d in diffs if d.status in ("regressed", "new"))
        unchanged = sum(1 for d in diffs if d.status == "unchanged")

        C_ERR = "#E24B4A"
        C_WARN = "#D4900A"
        C_OK = "#639922"
        C_INFO = "#378ADD"

        self.view.form.lResolved.setText(f"+{resolved}" if resolved else "0")
        res_color = C_OK if resolved else "#666666"
        self.view.form.lResolved.setStyleSheet(f"color: {res_color}; font-weight: bold;")

        self.view.form.lRegressed.setText(f"+{regressed}" if regressed else "0")
        reg_color = C_ERR if regressed else "#666666"
        self.view.form.lRegressed.setStyleSheet(f"color: {reg_color}; font-weight: bold;")

        self.view.form.lUnchanged.setText(str(unchanged))
        unch_color = C_INFO if unchanged else "#666666"
        self.view.form.lUnchanged.setStyleSheet(f"color: {unch_color}; font-weight: bold;")

        def _pill(errors, warnings, successes):
            if errors == 0 and warnings == 0:
                return "Pass", C_OK
            if errors > 0:
                return f"{errors} E", C_ERR
            if warnings > 0:
                return f"{warnings} W", C_WARN
            return "S", C_OK

        # Sorting order for the list
        ORDER = {"regressed": 0, "new": 0, "improved": 1, "resolved": 1, "unchanged": 2}

        for d in sorted(diffs, key=lambda x: (ORDER.get(x.status, 3), x.rule_label)):
            if d.status in ("regressed", "new"):
                icon_path = ":/icons/dfm_error.svg"
            elif d.status in ("improved", "resolved"):
                icon_path = ":/icons/dfm_success.svg"
            else:
                if d.current_errors > 0:
                    icon_path = ":/icons/dfm_error.svg"
                elif d.current_warnings > 0:
                    icon_path = ":/icons/dfm_error.svg"
                else:
                    icon_path = ":/icons/dfm_success.svg"

            from_text, from_color = _pill(
                d.previous_errors, d.previous_warnings, d.previous_success
            )
            to_text, to_color = _pill(d.current_errors, d.current_warnings, d.current_success)

            item = QtWidgets.QListWidgetItem()
            item.setIcon(QtGui.QIcon(icon_path))
            item.setData(QtCore.Qt.ItemDataRole.UserRole + 1, "diff")
            item.setData(QtCore.Qt.ItemDataRole.UserRole + 2, d.rule_label)
            item.setData(QtCore.Qt.ItemDataRole.UserRole + 3, from_text)
            item.setData(QtCore.Qt.ItemDataRole.UserRole + 4, from_color)
            item.setData(QtCore.Qt.ItemDataRole.UserRole + 5, to_text)
            item.setData(QtCore.Qt.ItemDataRole.UserRole + 6, to_color)
            self.view.form.lwDiffs.addItem(item)

        # Dynamic Height
        item_count = self.view.form.lwDiffs.count()
        row_height = 28
        total_height = min(300, (item_count * row_height) + 4)
        self.view.form.lwDiffs.setFixedHeight(total_height)

        self.view.form.lwDiffs.viewport().update()
        QtWidgets.QApplication.processEvents()

    def _export_diff(self):
        a_idx = self.view.form.cbRun1.currentData()
        b_idx = self.view.form.cbRun2.currentData()

        if a_idx is None or b_idx is None or a_idx == b_idx:
            return

        run_a = self.history_manager.load_run(self.doc_name, self.shape_name, a_idx)
        run_b = self.history_manager.load_run(self.doc_name, self.shape_name, b_idx)
        if not run_a or not run_b:
            return

        path, _ = QFileDialog.getSaveFileName(
            self.view.form, "Export Diff Report", "", "CSV Files (*.csv)"
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"

        from ...app.history import diff_runs

        diffs = diff_runs(run_a, run_b)

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["DFM Diff Report"])
                writer.writerow(["Document", self.doc_name, "Shape", self.shape_name])
                writer.writerow(["Run A", run_a.label, run_a.process, run_a.material])
                writer.writerow(["Run B", run_b.label, run_b.process, run_b.material])
                writer.writerow([])
                writer.writerow(["Rule", "Run A", "Run B", "Status"])
                for d in diffs:
                    writer.writerow([d.rule_label, d.previous_count, d.current_count, d.status])
            QMessageBox.information(self.view.form, "Done", "Diff report exported.")
        except Exception as e:
            App.Console.PrintError(f"Diff export failed: {e}\n")
