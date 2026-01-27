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

import html
from collections import defaultdict
import csv

import FreeCAD
import FreeCADGui as Gui
import Part

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtGui import QStandardItemModel, QStandardItem
from PySide6.QtWidgets import QFileDialog, QMessageBox

from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face

from dfm.models import CheckResult, Severity
from dfm.processes.process import Process
from dfm.rules import Rulebook
from . import DFM_rc


class TaskResults:
    def __init__(self, results: list[CheckResult], target_object, process: Process, material: str):
        self.target_object = target_object
        self.process = process
        self.material_name = material
        self.results = results

        self.form = Gui.PySideUic.loadUi(":/ui/task_results.ui")  # type: ignore
        self.form.setWindowTitle("DFM Analysis")

        self.tree = self.form.tvResults
        self.tree.setHeaderHidden(True)

        self.model = QStandardItemModel()
        self.tree.setModel(self.model)

        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.on_context_menu)

        self.verdict = self.find_verdict(self.results)

        self.form.pbExportResults.clicked.connect(self.on_export_clicked)
        self.form.pbSaveResults.clicked.connect(self.on_save_clicked)

        self.populate_info_widgets()
        self.populate_results_tree()
        self.tree.clicked.connect(self.on_result_clicked)
        self.tree.doubleClicked.connect(self.on_result_double_clicked)

        Gui.Control.showDialog(self)
        Gui.Selection.clearSelection()

    def on_export_clicked(self):
        """Exports the current results to a CSV file selected by the user."""

        filename, _ = QFileDialog.getSaveFileName(
            self.form,
            "Export DFM Results",
            "",
            "CSV Files (*.csv);;All Files (*)",
        )

        if not filename:
            return

        if not filename.lower().endswith(".csv"):
            filename += ".csv"

        try:
            with open(filename, mode="w", newline="", encoding="utf-8") as csv_file:
                writer = csv.writer(csv_file)

                writer.writerow(["Design", self.target_object.Label])
                writer.writerow(["Manufacturing Process", self.process.name])
                writer.writerow(["Material", self.material_name])
                writer.writerow(["Verdict", self.verdict])
                writer.writerow(
                    [
                        "Status",
                        "Rule Name",
                        "Face ID",
                        "Value / Limit",
                    ]
                )

                for result in self.results:
                    face_names = []
                    if result.failing_geometry and not result.ignore:
                        for face in result.failing_geometry:
                            face_names.append(self.get_face_name(face))
                        face_str = "; ".join(face_names)
                    else:
                        continue  # Issue was ignored

                    writer.writerow(
                        [
                            result.severity.name,
                            result.rule_id.label,
                            face_str,
                            result.overview,
                        ]
                    )

            QMessageBox.information(
                self.form,
                "Export Successful",
                f"Successfully exported {len(self.results)} results to:\n{filename}",
            )

        except Exception as e:
            FreeCAD.Console.PrintError(f"CSV Export failed: {e}\n")
            QMessageBox.critical(
                self.form, "Export Failed", f"An error occurred while writing the file:\n{str(e)}"
            )

    def on_save_clicked(self):
        """"""
        QMessageBox.information(
            self.form,
            "Save Not Implemented",
            "Save results to .FCStd file is not currently implemented.",
        )

    def populate_info_widgets(self):
        """Populates the top-level information widgets."""
        self.form.leTarget.setText(self.target_object.Label)
        self.form.leTarget.setReadOnly(True)
        self.form.leProcess.setText(self.process.name)
        self.form.leProcess.setReadOnly(True)
        self.form.leMaterial.setText(self.material_name)
        self.form.leMaterial.setReadOnly(True)
        self.form.leVerdict.setText(self.verdict)
        self.form.leVerdict.setReadOnly(True)
        self.form.tbDetails.setHtml(
            "Select a result in the tree to view details of the DFM issues."
        )
        self.adjust_details_height()

    def populate_results_tree(self):
        """
        Populates the tree.
        Sorts items so ignored findings are at the bottom and styled gray.
        """
        expanded_rules = set()
        root = self.model.invisibleRootItem()
        for i in range(root.rowCount()):
            item = root.child(i)
            index = self.model.indexFromItem(item)
            if self.tree.isExpanded(index):
                expanded_rules.add(item.text())

        self.model.clear()

        grouped_results = defaultdict(list)
        for result in self.results:
            grouped_results[result.rule_id].append(result)

        root_node = self.model.invisibleRootItem()

        for rule_id, findings in grouped_results.items():
            # Sort findings by severity
            findings.sort(key=lambda x: (x.ignore, -x.severity.value))

            active_count = sum(1 for f in findings if not f.ignore)
            rule_label = f"{rule_id.label} [{active_count} issues]"

            rule_item = QStandardItem(rule_label)
            rule_item.setFlags(
                QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
            )

            active_findings = [f for f in findings if not f.ignore]
            if active_findings:
                worst_severity = max(active_findings, key=lambda f: f.severity.value).severity
                rule_item.setIcon(self._get_severity_icon(worst_severity))
            else:
                rule_item.setIcon(self._get_severity_icon(findings[0].severity))

            # Create result tree child items for geometry that failed the rule
            for i, finding in enumerate(findings):
                instance_item = QStandardItem(
                    f"{self.get_face_name(finding.failing_geometry[0])}  ({finding.overview}) [{i + 1}]"
                )

                instance_item.setFlags(
                    QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
                )
                instance_item.setIcon(self._get_severity_icon(finding.severity))
                instance_item.setToolTip(finding.message)
                instance_item.setData(finding, QtCore.Qt.ItemDataRole.UserRole)

                if finding.ignore:
                    instance_item.setForeground(QtGui.QBrush(QtCore.Qt.GlobalColor.gray))
                    font = instance_item.font()
                    font.setStrikeOut(True)
                    instance_item.setFont(font)

                rule_item.appendRow(instance_item)

            root_node.appendRow(rule_item)

            if any(rule_id.label in ex for ex in expanded_rules):
                self.tree.setExpanded(rule_item.index(), True)

        # Check if a rule declared in the process yaml file did not appear in the results. If it
        # did not, this means that all faces on the model passed this rule.
        for rule_id in self.process.rules:
            # Convert rule_id string to Rulebook member
            if Rulebook[rule_id] not in grouped_results.keys():
                rule_item = QStandardItem(f"{Rulebook[rule_id].label} [Passed]")
                rule_item.setFlags(
                    QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
                )
                style = QtWidgets.QApplication.style()
                icon = style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogApplyButton)
                rule_item.setIcon(icon)

                root_node.appendRow(rule_item)
                rule_index = self.model.indexFromItem(rule_item)
                self.tree.setFirstColumnSpanned(rule_index.row(), QtCore.QModelIndex(), True)
                continue

    def on_context_menu(self, point):
        """Handles the right-click context menu."""
        index = self.tree.indexAt(point)
        if not index.isValid():
            return

        item = self.model.itemFromIndex(index)
        data = item.data(QtCore.Qt.ItemDataRole.UserRole)

        if isinstance(data, CheckResult):
            menu = QtWidgets.QMenu()

            if data.ignore:
                action_text = "Restore Item"
                icon = QtGui.QIcon.fromTheme("list-add")
            else:
                action_text = "Ignore Item"
                icon = QtGui.QIcon.fromTheme("list-remove")

            action = menu.addAction(icon, action_text)
            action.triggered.connect(lambda: self.toggle_ignore_state(data))

            menu.exec(self.tree.viewport().mapToGlobal(point))

    def toggle_ignore_state(self, finding: CheckResult):
        """Toggles the ignore boolean and refreshes the tree."""
        finding.ignore = not finding.ignore

        self.verdict = self.find_verdict(self.results)
        self.form.leVerdict.setText(self.verdict)

        self.populate_results_tree()
        self.restore_selection(finding)

    def restore_selection(self, target_finding: CheckResult):
        """Attempts to re-select the item corresponding to the modified finding."""
        root = self.model.invisibleRootItem()
        for r in range(root.rowCount()):
            rule_item = root.child(r)
            for c in range(rule_item.rowCount()):
                child = rule_item.child(c)
                data = child.data(QtCore.Qt.ItemDataRole.UserRole)
                if data == target_finding:
                    selection_model = self.tree.selectionModel()
                    selection_model.select(
                        child.index(), QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect
                    )
                    self.tree.scrollTo(child.index())
                    return

    def set_details_text(self, message: str):
        """Sets the message to be displayed in the QTextBrowser"""
        self.form.tbDetails.clear()
        self.form.tbDetails.setHtml(message)
        self.adjust_details_height()

    def adjust_details_height(self):
        """Adjusts the height of the text browser based on its content."""
        doc = self.form.tbDetails.document()
        content_height = doc.documentLayout().documentSize().height()
        final_height = int(content_height) + 5
        final_height = max(60, min(final_height, 250))
        self.form.tbDetails.setFixedHeight(final_height)

    def on_result_clicked(self, index: QtCore.QModelIndex):
        """Called when a user clicks on any item in the tree."""
        item = self.model.itemFromIndex(index)
        if not item:
            return

        failing_faces: list[TopoDS_Face] = []
        result_data = item.data(QtCore.Qt.ItemDataRole.UserRole)

        if isinstance(result_data, CheckResult):
            safe_overview = html.escape(result_data.overview)
            message = (
                f"<div style='margin-top: 4px; font-weight: bold;'>{safe_overview}</div>"
                f"<div style='margin-top: 4px;'>{result_data.message}</div>"
            )
            self.set_details_text(message)
            failing_faces = result_data.failing_geometry

        elif item.hasChildren():
            for row in range(item.rowCount()):
                child_item = item.child(row)
                child_data = child_item.data(QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(child_data, CheckResult):
                    failing_faces.extend(child_data.failing_geometry)
            message = "Select a result in the tree to view details of the DFM issues."
            self.set_details_text(message)

        if failing_faces:
            unique_faces = list(set(failing_faces))

            Gui.Selection.clearSelection()
            self.highlight_faces(unique_faces)
        else:
            Gui.Selection.clearSelection()

    def highlight_faces(self, failing_topo_faces: list[TopoDS_Face]):
        """Highlights the given faces on the document object."""
        if not failing_topo_faces:
            return

        shape_faces = self.target_object.Shape.Faces
        failing_face_names = []

        for failing_face_occ in failing_topo_faces:
            for i, part_face in enumerate(shape_faces):
                part_face_occ = Part.__toPythonOCC__(part_face)

                if part_face_occ.IsSame(failing_face_occ):
                    face_name = f"Face{i + 1}"
                    failing_face_names.append(face_name)
                    break

        if failing_face_names:
            FreeCAD.Console.PrintMessage(
                f"Highlighting sub-elements: {', '.join(failing_face_names)}"
            )
            Gui.Selection.addSelection(self.target_object, failing_face_names)

    def _get_severity_icon(self, severity: Severity) -> QtGui.QIcon:
        """Returns a standard Qt icon based on the Severity level."""
        style = QtWidgets.QApplication.style()

        if severity == Severity.ERROR:
            return style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxCritical)

        elif severity == Severity.WARNING:
            return style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxWarning)

        else:
            return style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxInformation)

    def on_result_double_clicked(self, index: QtCore.QModelIndex):
        """Called when a user double-clicks an item. Zooms and aligns to the selected geometry."""
        item = self.model.itemFromIndex(index)
        if not item:
            return

        result_data = item.data(QtCore.Qt.ItemDataRole.UserRole)

        if isinstance(result_data, CheckResult):
            Gui.Selection.clearSelection()
            self.highlight_faces(result_data.failing_geometry)

            # TODO: Make the AlignToSelection aware of material volume
            Gui.SendMsgToActiveView("ViewSelection")
            Gui.SendMsgToActiveView("AlignToSelection")

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

    def _pluralise(self, count: int, noun: str) -> str:
        """Pluralises a word based on an integer value"""
        suffix = "s" if count != 1 else ""
        return f"{count} {noun}{suffix}"

    def find_verdict(self, results: list[CheckResult]) -> str:
        "Finds the verdict of the DFM analysis and returns the message as a string."
        errors = sum(1 for r in results if r.severity == Severity.ERROR and not r.ignore)
        warnings = sum(1 for r in results if r.severity == Severity.WARNING and not r.ignore)

        parts = []
        if errors > 0:
            parts.append(self._pluralise(errors, "error"))
        if warnings > 0:
            parts.append(self._pluralise(warnings, "warning"))

        details = ", ".join(parts)

        if errors > 0:
            return f"Failed ({details})"
        elif warnings > 0:
            return f"Successful ({details})"
        else:
            return "Successful"
