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
from PySide6.QtGui import QStandardItemModel, QStandardItem
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face
import Part
from collections import defaultdict

from dfm.models import CheckResult, Severity
from dfm.rules import Rulebook
from . import DFM_rc


class TaskResults:
    def __init__(self, results: list[CheckResult], target_object, process_id: str, material: str):
        self.target_object = target_object
        self.process_id = process_id
        self.material_name = material
        self.results = results

        self.form = Gui.PySideUic.loadUi(":/ui/task_results.ui")  # type: ignore
        self.form.setWindowTitle("DFM Analysis")

        self.tree = self.form.tvResults
        self.tree.setHeaderHidden(True)

        self.model = QStandardItemModel()
        self.tree.setModel(self.model)

        self.populate_info_widgets()
        self.populate_results_tree()
        self.tree.clicked.connect(self.on_result_clicked)

        Gui.Control.showDialog(self)
        Gui.Selection.clearSelection()

    def on_save_clicked(self):
        """"""
        pass

    def populate_info_widgets(self):
        """Populates the top-level information widgets."""
        self.form.leTarget.setText(self.target_object.Label)
        self.form.leTarget.setReadOnly(True)
        self.form.leProcess.setText(self.process_id)
        self.form.leProcess.setReadOnly(True)
        self.form.leMaterial.setText(self.material_name)
        self.form.leMaterial.setReadOnly(True)
        self.form.tbDetails.setHtml(
            "Select a result in the tree to view details of the DFM issues."
        )
        self.adjust_details_height()

    def populate_results_tree(self):
        self.model.clear()

        grouped_results = defaultdict(list)
        for result in self.results:
            grouped_results[result.rule_id].append(result)

        if not grouped_results:
            no_issues_item = QStandardItem("No DFM issues found.")
            no_issues_item.setFlags(
                QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
            )
            self.model.invisibleRootItem().appendRow(no_issues_item)
            return

        root_node = self.model.invisibleRootItem()

        for rule_id, findings in grouped_results.items():
            rule_item = QStandardItem(f"{rule_id.label} [{len(findings)} issues]")

            rule_item.setFlags(
                QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
            )

            most_critical_finding = max(findings, key=lambda f: f.severity.value)
            rule_item.setIcon(self._get_severity_icon(most_critical_finding.severity))

            for i, finding in enumerate(findings):
                instance_item = QStandardItem(f"{finding.severity.name}: Instance [{i + 1}]")

                instance_item.setFlags(
                    QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
                )
                instance_item.setIcon(self._get_severity_icon(finding.severity))

                instance_item.setToolTip(finding.message)
                instance_item.setData(finding, QtCore.Qt.ItemDataRole.UserRole)

                rule_item.appendRow(instance_item)

            root_node.appendRow(rule_item)

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
            self.form.tbDetails.clear()
            message = f"<div style='margin-top: 4px;'>{result_data.message}</div>"
            self.form.tbDetails.setHtml(message)
            self.adjust_details_height()
            failing_faces = result_data.failing_geometry

        elif item.hasChildren():
            for row in range(item.rowCount()):
                child_item = item.child(row)
                child_data = child_item.data(QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(child_data, CheckResult):
                    failing_faces.extend(child_data.failing_geometry)
        else:
            self.form.tbDetails.clear()
            self.form.tbDetails.setHtml(
                "Select a result in the tree to view details of the DFM issues."
            )
            self.adjust_details_height()

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
