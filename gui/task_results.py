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

from typing import Any, Callable

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtGui import QStandardItemModel, QStandardItem

import FreeCADGui as Gui  # type: ignore

from dfm.models import CheckResult, Severity

from gui.results.delegates import DFMTreeDelegate
from gui.results.visuals import severity_color


class TaskResults:
    """Passive View: Only handles Widgets and Signals."""

    def __init__(self):
        self.form: Any = Gui.PySideUic.loadUi(":/ui/task_results.ui", None)  # type: ignore
        self.form.setWindowTitle("DFM Analysis")
        icon = QtGui.QIcon(":/icons/dfm_analysis.svg")
        self.form.setWindowIcon(icon)
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

        self._save_clicked = False

        self.on_closed: Callable[[], None] | None = None
        self.on_row_selected: Callable[[CheckResult | list[CheckResult]], None] | None = None
        self.on_row_double_clicked: Callable[[CheckResult], None] | None = None
        self.on_toggle_ignore: Callable[[CheckResult], None] | None = None
        self.on_export_clicked: Callable[[], None] | None = None
        self.on_toggle_ignore_all: Callable[[list[CheckResult]], None] | None = None
        self.on_zoom_to_rule: Callable[[list[CheckResult]], None] | None = None

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

    def render_tree(self, grouped_data: dict, all_process_rules: list):
        """Renders the DFM results tree"""
        expanded_rules = set()
        for i in range(self.model.rowCount()):
            idx = self.model.index(i, 0)
            if self.form.tvResults.isExpanded(idx):
                expanded_rules.add(self.model.item(i).text().split("\x00")[0])

        self.model.clear()
        self.form.tvResults.setItemDelegate(DFMTreeDelegate())
        root = self.model.invisibleRootItem()

        for rule_id, findings in grouped_data.items():
            error_count = sum(1 for f in findings if not f.ignore and f.severity == Severity.ERROR)
            warning_count = sum(
                1 for f in findings if not f.ignore and f.severity == Severity.WARNING
            )
            active_count = sum(1 for f in findings if not f.ignore)
            all_ignored = active_count == 0

            # Use success icon and color if all findings are ignored
            severity = findings[0].severity
            icon = self._get_icon(Severity.SUCCESS) if all_ignored else self._get_icon(severity)
            color = "#639922" if all_ignored else severity_color(severity)

            rule_item = QStandardItem()
            rule_item.setEditable(False)
            rule_item.setData(findings, QtCore.Qt.ItemDataRole.UserRole)
            rule_item.setData("rule", QtCore.Qt.ItemDataRole.UserRole + 1)
            rule_item.setData(rule_id.label, QtCore.Qt.ItemDataRole.UserRole + 2)
            rule_item.setData(str(active_count), QtCore.Qt.ItemDataRole.UserRole + 3)
            rule_item.setData(color, QtCore.Qt.ItemDataRole.UserRole + 4)
            rule_item.setData(error_count, QtCore.Qt.ItemDataRole.UserRole + 6)
            rule_item.setData(warning_count, QtCore.Qt.ItemDataRole.UserRole + 7)
            rule_item.setIcon(icon)

            for finding in findings:
                name = finding.refs[0].label if finding.refs else "Unknown"
                child = QStandardItem()
                child.setEditable(False)
                child.setData(finding, QtCore.Qt.ItemDataRole.UserRole)
                child.setData("finding", QtCore.Qt.ItemDataRole.UserRole + 1)
                child.setData(name, QtCore.Qt.ItemDataRole.UserRole + 2)
                child.setData(finding.overview, QtCore.Qt.ItemDataRole.UserRole + 3)
                child.setData(severity_color(finding.severity), QtCore.Qt.ItemDataRole.UserRole + 4)
                child.setData(finding.ignore, QtCore.Qt.ItemDataRole.UserRole + 5)
                child.setIcon(self._get_icon(finding.severity))
                rule_item.appendRow(child)

            root.appendRow(rule_item)
            if rule_id.label in expanded_rules:
                self.form.tvResults.setExpanded(rule_item.index(), True)

        for rule in all_process_rules:
            if rule not in grouped_data:
                pass_item = QStandardItem()
                pass_item.setEditable(False)
                pass_item.setData("rule", QtCore.Qt.ItemDataRole.UserRole + 1)
                pass_item.setData(rule.label, QtCore.Qt.ItemDataRole.UserRole + 2)
                pass_item.setData("0", QtCore.Qt.ItemDataRole.UserRole + 3)
                pass_item.setData("#639922", QtCore.Qt.ItemDataRole.UserRole + 4)
                pass_item.setIcon(QtGui.QIcon(":/icons/dfm_success.svg"))
                root.appendRow(pass_item)

    def _get_icon(self, severity: Severity) -> QtGui.QIcon:
        """Returns a severity circle icon for the given severity level."""
        icon_map = {
            Severity.ERROR: ":/icons/dfm_error.svg",
            Severity.WARNING: ":/icons/dfm_warning.svg",
            Severity.INFO: ":/icons/dfm_info.svg",
        }
        path = icon_map.get(severity, ":/icons/dfm_success.svg")
        return QtGui.QIcon(path)

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
        if not item:
            return

        data = item.data(QtCore.Qt.ItemDataRole.UserRole)
        menu = QtWidgets.QMenu()

        if isinstance(data, list):
            # Rule-level context menu
            findings: list[CheckResult] = data
            active = [f for f in findings if not f.ignore]
            ignored = [f for f in findings if f.ignore]

            zoom_action = menu.addAction("Zoom to All Findings")
            menu.addSeparator()

            if active:
                ignore_all = menu.addAction(f"Ignore All ({len(active)})")
                ignore_all.triggered.connect(
                    lambda: self.on_toggle_ignore_all(active) if self.on_toggle_ignore_all else None
                )

            if ignored:
                restore_all = menu.addAction(f"Restore All ({len(ignored)})")
                restore_all.triggered.connect(
                    lambda: self.on_toggle_ignore_all(ignored)
                    if self.on_toggle_ignore_all
                    else None
                )

            menu.addSeparator()
            copy_action = menu.addAction("Copy Summary")

            zoom_action.triggered.connect(
                lambda: self.on_zoom_to_rule(findings) if self.on_zoom_to_rule else None
            )
            copy_action.triggered.connect(lambda: self._copy_rule_summary(findings))

        elif isinstance(data, CheckResult):
            # Issue-level context menu
            finding: CheckResult = data

            zoom_action = menu.addAction("Zoom to Face")
            zoom_action.triggered.connect(
                lambda: self.on_row_double_clicked(finding) if self.on_row_double_clicked else None
            )

            menu.addSeparator()

            ignore_txt = "Restore" if finding.ignore else "Ignore"
            ignore_action = menu.addAction(ignore_txt)
            ignore_action.triggered.connect(
                lambda: self.on_toggle_ignore(finding) if self.on_toggle_ignore else None
            )

            menu.addSeparator()
            copy_action = menu.addAction("Copy Details")
            copy_action.triggered.connect(lambda: self._copy_issue_details(finding))

        else:
            return

        menu.exec(self.form.tvResults.viewport().mapToGlobal(point))

    def _copy_rule_summary(self, findings: list[CheckResult]):
        """Copies a plain-text summary of all findings under a rule to the clipboard."""
        if not findings:
            return
        rule_label = findings[0].rule_id.label
        lines = [f"{rule_label} — {len(findings)} finding(s)"]
        for f in findings:
            status = "[ignored]" if f.ignore else f"[{f.severity.name}]"
            lines.append(f"  {status} {f.overview}")
        QtWidgets.QApplication.clipboard().setText("\n".join(lines))

    def _copy_issue_details(self, finding: CheckResult):
        """Copies full issue details to the clipboard as plain text."""
        lines = [
            f"Rule: {finding.rule_id.label}",
            f"Severity: {finding.severity.name}",
            f"Overview: {finding.overview}",
            f"Details: {finding.message}",
        ]
        if finding.value is not None:
            lines.append(f"Measured: {finding.value:.2f}{finding.unit}")
        if finding.limit is not None:
            lines.append(f"Limit: {finding.limit:.2f}{finding.unit}")
        QtWidgets.QApplication.clipboard().setText("\n".join(lines))

    def on_save_clicked(self):
        print("saved")

    def getStandardButtons(self):
        return (
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Close
        )

    def clicked(self, button):
        if button == QtWidgets.QDialogButtonBox.StandardButton.Save:
            self._save_clicked = True
            if self.on_save_clicked:
                self.on_save_clicked()

    def reject(self):
        if self.on_closed:
            self.on_closed()
        Gui.Control.closeDialog()

    def accept(self):
        if self._save_clicked:
            self._save_clicked = False
            return
        if self.on_closed:
            self.on_closed()
        Gui.Control.closeDialog()
