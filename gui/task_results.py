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

import csv
import html
from collections import defaultdict
from typing import Any, Callable

import FreeCAD  # type: ignore
import FreeCADGui as Gui  # type: ignore
import Part  # type: ignore
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtGui import QStandardItemModel, QStandardItem
from PySide6.QtWidgets import QFileDialog, QMessageBox

from OCC.Core.TopoDS import TopoDS_Face

from dfm.models import CheckResult, Severity
from dfm.processes.process import Process
from dfm.rules import Rulebook


class TaskResults:
    """Passive View: Only handles Widgets and Signals."""

    def __init__(self):
        self.form: Any = Gui.PySideUic.loadUi(":/ui/task_results.ui", None)  # type: ignore
        self.form.setWindowTitle("DFM Analysis")
        self.model = QStandardItemModel()
        self.form.tvResults.setModel(self.model)
        self.form.tvResults.setHeaderHidden(True)

        self.form.leTarget.setReadOnly(True)
        self.form.leProcess.setReadOnly(True)
        self.form.leMaterial.setReadOnly(True)
        self.form.leVerdict.setReadOnly(True)
        self.form.tbDetails.setReadOnly(True)

        self.form.tbDetails.setHtml(
            "Select a result in the tree to view details of the DFM issues."
        )

        self.on_row_selected: Callable[[CheckResult | list[CheckResult]], None] | None = None
        self.on_row_double_clicked: Callable[[CheckResult], None] | None = None
        self.on_toggle_ignore: Callable[[CheckResult], None] | None = None
        self.on_export_clicked: Callable[[], None] | None = None

        self.form.tvResults.clicked.connect(self._handle_click)
        self.form.tvResults.doubleClicked.connect(self._handle_double_click)
        self.form.pbExportResults.clicked.connect(self._handle_export_btn)
        self.form.tvResults.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.form.tvResults.customContextMenuRequested.connect(self._show_context_menu)

    def adjust_details_height(self):
        """Dynamic resizing of the description box based on content."""
        doc = self.form.tbDetails.document()
        content_height = doc.documentLayout().documentSize().height()
        final_height = int(content_height) + 10  # Padding
        self.form.tbDetails.setFixedHeight(max(60, min(final_height, 300)))

    def render_tree(self, grouped_data: dict, face_namer_func: Callable, all_process_rules: list):
        """Renders the DFM results tree"""
        expanded_rules = set()
        for i in range(self.model.rowCount()):
            idx = self.model.index(i, 0)
            if self.form.tvResults.isExpanded(idx):
                expanded_rules.add(self.model.item(i).text().split(" [")[0])

        self.model.clear()
        root = self.model.invisibleRootItem()

        # Failed/Warning Rules
        for rule_id, findings in grouped_data.items():
            active_count = sum(1 for f in findings if not f.ignore)
            rule_item = QStandardItem(f"{rule_id.label} [{active_count}]")
            rule_item.setEditable(False)
            rule_item.setData(findings, QtCore.Qt.ItemDataRole.UserRole)
            rule_item.setIcon(self._get_icon(findings[0].severity))

            for finding in findings:
                name = face_namer_func(finding.failing_geometry[0])
                child = QStandardItem(f"{name} ({finding.overview})")
                child.setEditable(False)
                child.setData(finding, QtCore.Qt.ItemDataRole.UserRole)
                child.setIcon(self._get_icon(finding.severity))
                if finding.ignore:
                    child.setForeground(QtGui.QBrush(QtCore.Qt.GlobalColor.gray))
                    font = child.font()
                    font.setStrikeOut(True)
                    child.setFont(font)
                rule_item.appendRow(child)
            root.appendRow(rule_item)
            if rule_id.label in expanded_rules:
                self.form.tvResults.setExpanded(rule_item.index(), True)

        # Add Passed Rules
        for rule_key in all_process_rules:
            rule_enum = Rulebook[rule_key]
            if rule_enum not in grouped_data:
                pass_item = QStandardItem(f"{rule_enum.label} [Passed]")
                pass_item.setEditable(False)
                pass_item.setIcon(
                    QtWidgets.QApplication.style().standardIcon(
                        QtWidgets.QStyle.StandardPixmap.SP_DialogApplyButton
                    )
                )
                root.appendRow(pass_item)

    def _get_icon(self, severity: Severity):
        """Helper function to get the icon for a given severity."""
        style = QtWidgets.QApplication.style()
        px = {
            Severity.ERROR: QtWidgets.QStyle.StandardPixmap.SP_MessageBoxCritical,
            Severity.WARNING: QtWidgets.QStyle.StandardPixmap.SP_MessageBoxWarning,
        }.get(severity, QtWidgets.QStyle.StandardPixmap.SP_MessageBoxInformation)
        return style.standardIcon(px)

    def _handle_click(self, index: QtCore.QModelIndex):
        """Helper function to handle row selection."""
        item = self.model.itemFromIndex(index)
        if item and self.on_row_selected:
            data = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if data:
                self.on_row_selected(data)

    def _handle_double_click(self, index: QtCore.QModelIndex):
        """Helper function to handle row double-click."""
        item = self.model.itemFromIndex(index)
        if item:
            data = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if isinstance(data, CheckResult) and self.on_row_double_clicked:
                self.on_row_double_clicked(data)

    def _handle_export_btn(self):
        """Helper function to handle export button click."""
        if self.on_export_clicked:
            self.on_export_clicked()

    def _show_context_menu(self, point: QtCore.QPoint):
        """Helper function to show context menu at a given point."""
        index = self.form.tvResults.indexAt(point)
        item = self.model.itemFromIndex(index)
        if item:
            data = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if isinstance(data, CheckResult):
                menu = QtWidgets.QMenu()
                txt = "Restore" if data.ignore else "Ignore"
                action = menu.addAction(txt)
                if self.on_toggle_ignore:
                    action.triggered.connect(lambda: self.on_toggle_ignore(data))  # type: ignore
                menu.exec(self.form.tvResults.viewport().mapToGlobal(point))


class DFMReportModel:
    """Holds the data of a DFM report and handles grouping and verdicts."""

    STATUS_THEMES = {
        "FAILED": {"text": "Failed", "color": "#D32F2F"},
        "WARNING": {"text": "Warning", "color": "#E65100"},
        "SUCCESSFUL": {"text": "Successful", "color": "#2E7D32"},
    }

    def __init__(self, results: list[CheckResult], process: Process, material: str):
        self.results = results
        self.process = process
        self.material = material

    @property
    def active_results(self):
        """Returns only active results (not ignored)"""
        return [r for r in self.results if not r.ignore]

    def get_grouped_results(self) -> dict:
        """Returns results grouped by rule id"""
        grouped = defaultdict(list)
        for result in self.results:
            grouped[result.rule_id].append(result)

        for rule_id in grouped:
            grouped[rule_id].sort(key=lambda x: (x.ignore, -x.severity.value))

        return dict(sorted(grouped.items(), key=lambda item: item[0].label))

    def get_verdict(self) -> tuple[str, str]:
        """Returns verdict string based on active results"""
        errors = sum(1 for r in self.active_results if r.severity == Severity.ERROR)
        warnings = sum(1 for r in self.active_results if r.severity == Severity.WARNING)

        parts = []
        if errors:
            parts.append(f"{errors} error{'s' if errors != 1 else ''}")
        if warnings:
            parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")

        details = f" ({', '.join(parts)})" if parts else ""

        if errors:
            theme = self.STATUS_THEMES["FAILED"]
        elif warnings:
            theme = self.STATUS_THEMES["WARNING"]
        else:
            theme = self.STATUS_THEMES["SUCCESSFUL"]

        return f"{theme['text']}{details}", theme["color"]

    def toggle_ignore_state(self, finding: CheckResult):
        """Toggles the ignore state of a finding."""
        finding.ignore = not finding.ignore


class CSVResultExporter:
    """Writes DFM report data to a CSV file."""

    @staticmethod
    def export(filepath: str, target_label: str, model: DFMReportModel, face_namer_func: Callable):
        """Writes the DFM report data to a CSV file."""
        try:
            with open(filepath, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                verdict_text, _ = model.get_verdict()
                writer.writerow(["Design", target_label])
                writer.writerow(["Process", model.process.name])
                writer.writerow(["Material", model.material])
                writer.writerow(["Verdict", verdict_text])
                writer.writerow(["Status", "Rule Name", "Faces", "Details"])

                for result in model.results:
                    if result.ignore:
                        continue
                    faces = "; ".join([face_namer_func(f) for f in result.failing_geometry])
                    writer.writerow(
                        [result.severity.name, result.rule_id.label, faces, result.overview]
                    )
            return True
        except Exception as e:
            FreeCAD.Console.PrintError(f"Export failed: {e}\n")
            return False


class DFMViewProvider:
    """Handles interaction with the FreeCAD 3D viewport using geometric identity."""

    def __init__(self, target_object):
        self.target_object = target_object

    def get_face_name(self, target_occ_face: TopoDS_Face) -> str:
        """
        Inputs a TopoDS_Face and returns the FreeCAD internal name (e.g., 'Face4').
        """
        shape_faces = self.target_object.Shape.Faces

        for i, part_face in enumerate(shape_faces, start=1):
            part_face_occ = Part.__toPythonOCC__(part_face)

            if part_face_occ.IsSame(target_occ_face):
                return f"Face{i}"

        return "Unknown Face"

    def highlight_faces(self, topo_faces: list[Any]):
        """Clears current selection and selects the failing faces."""
        Gui.Selection.clearSelection()

        if not topo_faces:
            return

        face_names = []
        for face in topo_faces:
            name = self.get_face_name(face)
            if name:
                face_names.append(name)

        if face_names:
            Gui.Selection.addSelection(self.target_object, face_names)

    def zoom_to_selection(self):
        """Zooms the 3D view to the current selection."""
        if Gui.ActiveDocument and Gui.activeView():
            Gui.SendMsgToActiveView("ViewSelection")
            Gui.SendMsgToActiveView("AlignToSelection")


class TaskResultsPresenter:
    """Presenter for the TaskResults view.  Handles UI updates and event handling."""

    def __init__(self, view: TaskResults, model: DFMReportModel, bridge: DFMViewProvider):
        self.view = view
        self.model = model
        self.bridge = bridge

        self.view.on_row_selected = self.handle_selection
        self.view.on_row_double_clicked = self.handle_zoom
        self.view.on_toggle_ignore = self.handle_ignore
        self.view.on_export_clicked = self.handle_export

        self.refresh_ui()
        Gui.Control.showDialog(self.view)

    def refresh_ui(self):
        """Refreshes the UI with current model data."""
        self.view.form.leTarget.setText(self.bridge.target_object.Label)
        self.view.form.leProcess.setText(self.model.process.name)
        self.view.form.leMaterial.setText(self.model.material)

        text, color = self.model.get_verdict()
        self.view.form.leVerdict.setText(text)
        self.view.form.leVerdict.setStyleSheet(f"color: {color}; font-weight: bold;")

        self.view.render_tree(
            self.model.get_grouped_results(), self.bridge.get_face_name, self.model.process.rules
        )

    def handle_selection(self, data: CheckResult | list[CheckResult]):
        """Highlights faces based on selection. Can be a single result or a group."""
        Gui.Selection.clearSelection()

        if isinstance(data, list):
            # Parent rule clicked: highlight all non-ignored child faces
            all_faces = []
            for result in data:
                if not result.ignore:
                    all_faces.extend(result.failing_geometry)

            rule_name = data[0].rule_id.label if data else "Rule"
            self.view.form.tbDetails.setHtml(
                f"<b>Rule: {rule_name}</b><br>Showing all findings for this rule."
            )
            self.bridge.highlight_faces(all_faces)

        elif isinstance(data, CheckResult):
            # Individual finding clicked
            overview = html.escape(data.overview)
            message = (
                f"<div style='margin-top: 4px; font-weight: bold;'>{overview}</div>"
                f"<div style='margin-top: 4px;'>{data.message}</div>"
            )
            self.view.form.tbDetails.setHtml(message)
            self.bridge.highlight_faces(data.failing_geometry)

        self.view.adjust_details_height()

    def handle_zoom(self, result: CheckResult):
        """Handle zooming to the selected issue."""
        self.handle_selection(result)
        self.bridge.zoom_to_selection()

    def handle_ignore(self, result: CheckResult):
        """Handle ignoring the selected issue."""
        self.model.toggle_ignore_state(result)
        self.refresh_ui()

    def handle_export(self):
        """Handle exporting the selected geometry to a CSV file."""
        path, _ = QFileDialog.getSaveFileName(self.view.form, "Export CSV", "", "CSV (*.csv)")
        if path:
            if CSVResultExporter.export(
                path, self.bridge.target_object.Label, self.model, self.bridge.get_face_name
            ):
                QMessageBox.information(self.view.form, "Done", "Export Successful")
