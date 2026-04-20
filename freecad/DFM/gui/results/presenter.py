# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the DFM addon.

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
#  *   License along with this library; see the file COPYING.LIB. If not,   *
#  *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
#  *   Suite 330, Boston, MA  02111-1307, USA                                *
#  *                                                                         *
#  ***************************************************************************

import html
import csv

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import QFileDialog, QMessageBox

from ...app.history import HistoryManager

from ...dfm.models import CheckResult, Severity
from ...gui.results.bridge import DFMViewProvider
from ...gui.results.delegates import HistoryRowDelegate
from ...gui.results.models import DFMReportModel
from ...gui.results.utils import CSVResultExporter, icon_to_html
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
    ):
        self.view = view
        self.model = model
        self.bridge = bridge

        self.view.on_row_selected = self.handle_selection
        self.view.on_row_double_clicked = self.handle_zoom
        self.view.on_toggle_ignore = self.handle_ignore
        self.view.on_export_clicked = self.handle_export
        self.view.on_closed = self.handle_cleanup
        self.view.on_save_clicked = self.handle_save
        self.view.on_toggle_ignore_all = self.handle_ignore_all
        self.view.on_zoom_to_rule = self.handle_zoom_to_rule

        self.history_manager = history_manager
        self.doc_name = doc_name
        self.shape_name = shape_name

        self.build_history_tab()

        self.view.adjust_details_height()

        self.refresh_ui()
        Gui.Control.showDialog(self.view)

    def refresh_ui(self):
        self.view.form.leTarget.setText(self.bridge.target_object.Label)
        self.view.form.leProcess.setText(self.model.process.name)
        self.view.form.leMaterial.setText(self.model.material)
        self.view.form.leTarget.setCursorPosition(0)
        self.view.form.leProcess.setCursorPosition(0)
        self.view.form.leMaterial.setCursorPosition(0)

        text, color = self.model.get_verdict()
        self.view.form.leVerdict.setText(text)
        self.view.form.leVerdict.setStyleSheet(f"color: {color}; font-weight: bold;")

        self.view.render_tree(
            self.model.get_grouped_results(),
            self.model.process.active_rules,
        )

        if not self.model.active_results:
            self.view.form.tbDetails.setHtml(
                "<b>No issues found.</b><br>"
                f"This design passed all active checks for <i>{self.model.process.name}</i> "
                f"with material <i>{self.model.material}</i>. "
                "It meets the manufacturing requirements as configured."
            )
            self.view.adjust_details_height()

    def handle_selection(self, data: CheckResult | list[CheckResult]):
        Gui.Selection.clearSelection()
        if isinstance(data, list) and len(data) == 0:
            self.bridge.highlight_faces_and_edges_by_index([], [])
            icon = self.view._get_icon(Severity.SUCCESS)
            icon_html = icon_to_html(icon, size=16)
            self.view.form.tbDetails.setHtml(
                f"<table cellspacing='0' cellpadding='0'><tr>"
                f"<td valign='middle' style='padding-right:4px'>{icon_html}</td>"
                f"<td valign='middle'><b>No issues found.</b></td>"
                f"</tr></table>"
                f"<p style='margin-top:4px'>This rule passed all checks with the current process settings.</p>"
            )
            self.view.adjust_details_height()
            return
        elif isinstance(data, list):
            rule_name = data[0].rule_id.label if data else "Rule"
            active = [r for r in data if not r.ignore]
            worst_severity = max(
                (r.severity for r in active), key=lambda s: s.value, default=Severity.SUCCESS
            )
            icon = self.view._get_icon(worst_severity)
            icon_html = icon_to_html(icon, size=16)

            self.view.form.tbDetails.setHtml(
                f"<table cellspacing='0' cellpadding='0'><tr>"
                f"<td valign='middle' style='padding-right:4px'>{icon_html}</td>"
                f"<td valign='middle'><b>{rule_name}</b></td>"
                f"</tr></table>"
                f"<p style='margin-top:4px'>Showing all {len(active)} finding{'s' if len(active) != 1 else ''}.</p>"
            )

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

        elif isinstance(data, CheckResult):
            overview = html.escape(data.overview)
            color = severity_color(data.severity)
            icon = self.view._get_icon(data.severity)
            icon_html = icon_to_html(icon, size=16)
            self.view.form.tbDetails.setHtml(
                f"<table cellspacing='0' cellpadding='0'><tr>"
                f"<td valign='middle' style='padding-right:4px'>{icon_html}</td>"
                f"<td valign='middle'><b>{overview}</b></td>"
                f"</tr></table>"
                f"<p style='margin-top:4px'>{data.message}</p>"
            )

            face_refs = [ref for ref in data.refs if ref.type == "Face"]
            edge_refs = [ref for ref in data.refs if ref.type == "Edge"]

            self.bridge.highlight_faces_and_edges_by_index(
                [(ref.index, color) for ref in face_refs],
                [(ref.index, color) for ref in edge_refs],
            )

            # Annotate — prefer face, fall back to edge
            if face_refs:
                self.bridge.annotate_by_index(face_refs[0].index, data.overview, color)
            elif edge_refs:
                self.bridge.annotate_edge_by_index(edge_refs[0].index, data.overview, color)

        self.view.adjust_details_height()

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
        rule_name = findings[0].rule_id.label if findings else "Rule"
        self.view.form.tbDetails.setHtml(f"<b>Rule: {rule_name}</b><br>Showing all findings.")
        self.bridge.highlight_faces_and_edges_by_index(face_pairs, edge_pairs)
        self.bridge.zoom_to_selection()
        self.view.adjust_details_height()

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

        if CSVResultExporter.export(path, self.bridge.target_object.Label, self.model):
            QMessageBox.information(self.view.form, "Done", "Export Successful")

    def handle_cleanup(self):
        self.bridge.restore()
        Gui.Selection.addSelection(self.bridge.target_object)
        Gui.Selection.clearSelection()

    def handle_save(self):
        QMessageBox.information(
            self.view.form, "Not Implemented", "Save Results is not yet implemented."
        )

    def handle_ignore_all(self, findings: list[CheckResult]):
        for finding in findings:
            self.model.toggle_ignore_state(finding)
        self.refresh_ui()

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

        from app.history import diff_runs

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
