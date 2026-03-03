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

# Removed unused TopoDS_Face import to clear linter note

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

        self.on_closed: Callable[[], None] | None = None
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
        final_height = int(content_height) + 10
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
        style = QtWidgets.QApplication.style()
        px = {
            Severity.ERROR: QtWidgets.QStyle.StandardPixmap.SP_MessageBoxCritical,
            Severity.WARNING: QtWidgets.QStyle.StandardPixmap.SP_MessageBoxWarning,
        }.get(severity, QtWidgets.QStyle.StandardPixmap.SP_MessageBoxInformation)
        return style.standardIcon(px)

    def _handle_click(self, index: QtCore.QModelIndex):
        item = self.model.itemFromIndex(index)
        if item and self.on_row_selected:
            data = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if data:
                self.on_row_selected(data)

    def _handle_double_click(self, index: QtCore.QModelIndex):
        item = self.model.itemFromIndex(index)
        if item:
            data = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if isinstance(data, CheckResult) and self.on_row_double_clicked:
                self.on_row_double_clicked(data)

    def _handle_export_btn(self):
        if self.on_export_clicked:
            self.on_export_clicked()

    def _show_context_menu(self, point: QtCore.QPoint):
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

    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.StandardButton.Close

    def reject(self):
        if self.on_closed:
            self.on_closed()
        Gui.Control.closeDialog()

    def accept(self):
        if self.on_closed:
            self.on_closed()
        Gui.Control.closeDialog()


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
        return [r for r in self.results if not r.ignore]

    def get_grouped_results(self) -> dict:
        grouped = defaultdict(list)
        for result in self.results:
            grouped[result.rule_id].append(result)
        for rule_id in grouped:
            grouped[rule_id].sort(key=lambda x: (x.ignore, -x.severity.value))
        return dict(sorted(grouped.items(), key=lambda item: item[0].label))

    def get_verdict(self) -> tuple[str, str]:
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
        finding.ignore = not finding.ignore


class CSVResultExporter:
    @staticmethod
    def export(filepath: str, target_label: str, model: DFMReportModel, face_namer_func: Callable):
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


class DFMAnnotation:
    """Specialized component for creating and styling 3D UI callouts."""

    def __init__(self, name="DFM_Issue_Annotation"):
        self.name = name
        self._active_name = None

    def remove(self, doc):
        if self._active_name:
            try:
                if obj := doc.getObject(self._active_name):
                    doc.removeObject(obj.Name)
            except Exception:
                pass
            self._active_name = None

    def create(self, doc, base_pos, text_pos, text, style_cfg):
        self.remove(doc)
        label = doc.addObject("App::AnnotationLabel", self.name)
        self._active_name = label.Name
        label.LabelText = [text]
        label.BasePosition = base_pos
        label.TextPosition = text_pos

        vo = label.ViewObject
        if vo:
            vo.TextColor = style_cfg.get("text_color", (0.95, 0.95, 0.95))
            vo.BackgroundColor = style_cfg.get("bg_color", (0.15, 0.15, 0.15))
            if "DisplayMode" in vo.PropertiesList:
                vo.DisplayMode = "Line"
            font_size = style_cfg.get("font_size", 14)
            for target in [vo, label]:
                if hasattr(target, "PropertiesList") and "FontSize" in target.PropertiesList:
                    target.FontSize = font_size
        return label


class DFMViewProvider:
    """The Coordinator: Orchestrates viewport state and visual feedback."""

    def __init__(self, target_object):
        self.target_object = target_object  # Fixed naming
        self.view_object = target_object.ViewObject
        self.anno_mgr = DFMAnnotation()

        self._backup = {
            "diffuse": list(self.view_object.DiffuseColor),
            "shape": self.view_object.ShapeColor,
            "trans": self.view_object.Transparency,
        }
        self._highlighted_face_names = []

    def _get_face_index(self, target_occ_face) -> int:
        for i, part_face in enumerate(self.target_object.Shape.Faces):
            if Part.__toPythonOCC__(part_face).IsSame(target_occ_face):
                return i
        return -1

    def get_face_name(self, target_occ_face) -> str:
        """Helper for Presenter and CSV Export."""
        idx = self._get_face_index(target_occ_face)
        return f"Face{idx + 1}" if idx != -1 else "Unknown Face"

    def restore(self):
        self.anno_mgr.remove(FreeCAD.ActiveDocument)
        if self.view_object:
            vo = self.view_object
            vo.Transparency = self._backup["trans"]
            vo.ShapeColor = self._backup["shape"]
            vo.DiffuseColor = [(1.0, 1.0, 1.0, 1.0)]
            vo.DiffuseColor = self._backup["diffuse"]
            vo.update()

    def highlight_faces(self, topo_faces: list):
        self.anno_mgr.remove(FreeCAD.ActiveDocument)
        if not self.view_object:
            return

        Gui.Selection.clearSelection()
        target_indices = {
            self._get_face_index(f) for f in topo_faces if self._get_face_index(f) != -1
        }
        self._highlighted_face_names = [f"Face{i + 1}" for i in target_indices]

        if not target_indices:
            self.restore()
            return

        num_faces = len(self.target_object.Shape.Faces)
        base = self._backup["diffuse"][0] if self._backup["diffuse"] else self._backup["shape"]

        new_colors = []
        for i in range(num_faces):
            if i in target_indices:
                new_colors.append((1.0, 0.0, 0.0, 0.0))
            else:
                c = (
                    self._backup["diffuse"][i]
                    if len(self._backup["diffuse"]) == num_faces
                    else base
                )
                new_colors.append((c[0], c[1], c[2], 0.0))

        self.view_object.DiffuseColor = []
        self.view_object.DiffuseColor = new_colors
        self.view_object.Transparency = 50
        self.view_object.update()

        if self._highlighted_face_names:
            # Trick to make FreeCAD update the colors in 3D view
            Gui.Selection.addSelection(self.target_object, self._highlighted_face_names[0])
            Gui.Selection.clearSelection()

    def annotate_issue(self, topo_faces: list, text: str):
        if not topo_faces:
            return
        idx = self._get_face_index(topo_faces[0])
        if idx == -1:
            return

        center = self.target_object.Shape.Faces[idx].CenterOfMass
        offset = FreeCAD.Vector(15, 15, 15)
        self.anno_mgr.create(
            doc=FreeCAD.ActiveDocument,
            base_pos=center,
            text_pos=center.add(offset),
            text=text,
            style_cfg={"font_size": 14},
        )
        FreeCAD.ActiveDocument.recompute()  # type: ignore

    def zoom_to_selection(self):
        if self._highlighted_face_names and Gui.activeView():
            Gui.Selection.addSelection(self.target_object, self._highlighted_face_names)
            Gui.SendMsgToActiveView("ViewSelection")
            Gui.SendMsgToActiveView("AlignToSelection")
            Gui.Selection.clearSelection()


class TaskResultsPresenter:
    """Presenter for the TaskResults view. Handles UI updates and event handling."""

    def __init__(self, view: TaskResults, model: DFMReportModel, bridge: DFMViewProvider):
        self.view = view
        self.model = model
        self.bridge = bridge

        self.view.on_row_selected = self.handle_selection
        self.view.on_row_double_clicked = self.handle_zoom
        self.view.on_toggle_ignore = self.handle_ignore
        self.view.on_export_clicked = self.handle_export
        self.view.on_closed = self.handle_cleanup

        self.refresh_ui()
        Gui.Control.showDialog(self.view)

    def refresh_ui(self):
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
        Gui.Selection.clearSelection()
        if isinstance(data, list):
            all_faces = [f for r in data if not r.ignore for f in r.failing_geometry]
            rule_name = data[0].rule_id.label if data else "Rule"
            self.view.form.tbDetails.setHtml(f"<b>Rule: {rule_name}</b><br>Showing all findings.")
            self.bridge.highlight_faces(all_faces)
        elif isinstance(data, CheckResult):
            overview = html.escape(data.overview)
            self.view.form.tbDetails.setHtml(f"<b>{overview}</b><br>{data.message}")
            self.bridge.highlight_faces(data.failing_geometry)
            self.bridge.annotate_issue(data.failing_geometry, data.overview)
        self.view.adjust_details_height()

    def handle_zoom(self, result: CheckResult):
        self.handle_selection(result)
        self.bridge.zoom_to_selection()

    def handle_ignore(self, result: CheckResult):
        self.model.toggle_ignore_state(result)
        self.refresh_ui()

    def handle_export(self):
        path, _ = QFileDialog.getSaveFileName(self.view.form, "Export CSV", "", "CSV (*.csv)")
        if path:
            if CSVResultExporter.export(
                path, self.bridge.target_object.Label, self.model, self.bridge.get_face_name
            ):
                QMessageBox.information(self.view.form, "Done", "Export Successful")

    def handle_cleanup(self):
        self.bridge.restore()
        Gui.Selection.addSelection(self.bridge.target_object)
        Gui.Selection.clearSelection()
