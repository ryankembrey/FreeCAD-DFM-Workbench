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
from ...core.processes.process import RuleLimit
from ...core.processes.threshold_resolvers import ThresholdResolver
from ...core.rules import Rulebook
from ...core.registries.process_registry import ProcessRegistry
from ...app.prefs import dfm_params
from ...gui.results.bridge import DFMViewProvider
from ...gui.results.models import DFMReportModel
from ...gui.results.utils import CSVResultExporter, CSVExportConfig
from ...gui.results.viewport_markers import ViewportMarkerController
from ...gui.results.visuals import severity_color
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
            self._rule_limit,
            self._values_title,
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

    def _rule_limit(self, rule: Rulebook) -> RuleLimit | None:
        """Target and limit for *rule* on the analysed material, for the rule popup.

        Rules whose thresholds come from the analysis setup (such as wall
        thickness derived from the nozzle diameter) cannot be recomputed
        here, so their limit is taken from the findings, when there are any.
        """
        process = self.model.process
        if rule in process.archetype.derived_rules:
            findings = self.model.get_grouped_results().get(rule) or []
            if findings:
                return RuleLimit(limit=f"{findings[0].limit:g}")
            return None
        material = process.materials.get(self.model.material)
        resolved = ThresholdResolver().resolve(rule, process, material)
        for attr in ("target", "limit"):
            if getattr(resolved, attr) in ("N/A", None):
                setattr(resolved, attr, "")
        return resolved

    def _values_title(self, rule: Rulebook) -> str:
        """Heading for the rule popup's values; only material rules name the material."""
        process = self.model.process
        if rule.per_material and rule not in process.archetype.derived_rules:
            return f"Values for {self.model.material}"
        return ""

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

    @staticmethod
    def _run_label(run) -> str:
        """Short picker label, such as "Run 32 · 31 Oct, 14:38"."""
        from datetime import datetime

        try:
            when = datetime.fromisoformat(run.timestamp)
            return f"Run {run.run} \u00b7 {when.day} {when:%b, %H:%M}"
        except (TypeError, ValueError):
            return run.label

    def build_history_tab(self):
        tab = self.view.history_tab
        runs = self.history_manager.load_runs(self.doc_name, self.shape_name)

        tab.set_runs([(run.run, self._run_label(run)) for run in runs])

        recent = runs[-10:]
        tab.set_trend(
            [run.run for run in recent],
            [sum(1 for f in run.findings if not f.ignore and f.severity == Severity.ERROR)
             for run in recent],
            [sum(1 for f in run.findings if not f.ignore and f.severity == Severity.WARNING)
             for run in recent],
        )

        if not getattr(self, "_history_wired", False):
            tab.compare_changed.connect(self.render_diff)
            tab.export_requested.connect(self._export_diff)
            self._history_wired = True

        if len(runs) >= 2:
            self.render_diff()

    def render_diff(self):
        from ...app.history import diff_runs

        tab = self.view.history_tab
        a_idx, b_idx = tab.selected_runs()
        if a_idx is None or b_idx is None:
            return
        if a_idx == b_idx:
            tab.clear_diff("Choose two different runs to compare.")
            return

        run_a = self.history_manager.load_run(self.doc_name, self.shape_name, a_idx)
        run_b = self.history_manager.load_run(self.doc_name, self.shape_name, b_idx)
        if not run_a or not run_b:
            tab.clear_diff("One of these runs could not be loaded.")
            return

        tab.set_diff(diff_runs(run_a, run_b))

    def _export_diff(self):
        a_idx, b_idx = self.view.history_tab.selected_runs()

        if a_idx is None or b_idx is None or a_idx == b_idx:
            return

        run_a = self.history_manager.load_run(self.doc_name, self.shape_name, a_idx)
        run_b = self.history_manager.load_run(self.doc_name, self.shape_name, b_idx)
        if not run_a or not run_b:
            return

        path, _ = QFileDialog.getSaveFileName(
            self.view.form, "Export Comparison", "", "CSV Files (*.csv)"
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
            QMessageBox.information(self.view.form, "Done", "Comparison exported.")
        except Exception as e:
            App.Console.PrintError(f"Diff export failed: {e}\n")
