# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from typing import Any, Callable

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtGui import QStandardItemModel, QStandardItem

import FreeCADGui as Gui  # type: ignore

from ..core.models import CheckResult, Severity

from ..gui.results.delegates import DFMTreeDelegate
from ..gui.results.visuals import severity_color
from ..core.rules import Criticality
from ..gui.results.utils import CSVExportConfig, CSVResultExporter


class TaskResults(QtCore.QObject):
    """Passive View: Only handles Widgets and Signals."""

    def __init__(self):
        super().__init__()
        self.form: Any = Gui.PySideUic.loadUi(":/ui/task_results.ui", None)  # type: ignore
        self.form.setWindowTitle("DFM Analysis")
        icon = QtGui.QIcon(":/icons/dfm_analysis.svg")
        self.form.setWindowIcon(icon)
        self.model = QStandardItemModel()
        self.form.tvResults.setModel(self.model)
        self.form.tvResults.setHeaderHidden(True)
        self.form.tvResults.installEventFilter(self)

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

        self.form.tvResults.doubleClicked.connect(self._handle_double_click)
        self.form.pbExportCSV.clicked.connect(self._handle_export_btn)

        self.form.cbDelimiter.addItems(["Comma  ,", "Semicolon  ;", "Tab"])

        self.form.tvResults.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.form.tvResults.customContextMenuRequested.connect(self._show_context_menu)
        self.form.tvResults.selectionModel().currentChanged.connect(self._handle_selection_change)
        self.form.gbDetails.setCheckable(True)
        self.form.gbDetails.setChecked(True)
        self.form.gbDetails.toggled.connect(self._on_details_toggled)

    def _on_details_toggled(self, checked: bool):
        self.form.tbDetails.setVisible(checked)
        if checked:
            self.adjust_details_height()

    def adjust_details_height(self):
        """Dynamic resizing of the description box based on content."""
        doc = self.form.tbDetails.document()
        content_height = doc.documentLayout().documentSize().height()
        final_height = int(content_height) + 10
        self.form.tbDetails.setFixedHeight(max(60, min(final_height, 300)))

    def render_tree(self, grouped_data: dict, all_process_rules: list, get_criticality=None):
        expanded_labels = set()

        def _collect_expanded(parent_item):
            for row in range(parent_item.rowCount()):
                child = parent_item.child(row)
                if self.form.tvResults.isExpanded(self.model.indexFromItem(child)):
                    expanded_labels.add(child.data(QtCore.Qt.ItemDataRole.UserRole + 2))
                _collect_expanded(child)

        _collect_expanded(self.model.invisibleRootItem())

        self.model.clear()
        self.form.tvResults.setItemDelegate(DFMTreeDelegate())
        root = self.model.invisibleRootItem()

        all_findings_full = [f for findings in grouped_data.values() for f in findings]
        all_findings_active = [f for f in all_findings_full if not f.ignore]
        total_errors = sum(1 for f in all_findings_active if f.severity == Severity.ERROR)
        total_warnings = sum(1 for f in all_findings_active if f.severity == Severity.WARNING)

        all_item = QStandardItem()
        all_item.setEditable(False)
        all_item.setData(all_findings_full, QtCore.Qt.ItemDataRole.UserRole)
        all_item.setData("all", QtCore.Qt.ItemDataRole.UserRole + 1)
        all_item.setData("Recommendations", QtCore.Qt.ItemDataRole.UserRole + 2)
        all_item.setData("0", QtCore.Qt.ItemDataRole.UserRole + 3)
        all_item.setData(
            "#E24B4A" if total_errors else "#D4900A" if total_warnings else "#639922",
            QtCore.Qt.ItemDataRole.UserRole + 4,
        )
        all_item.setData(total_errors, QtCore.Qt.ItemDataRole.UserRole + 6)
        all_item.setData(total_warnings, QtCore.Qt.ItemDataRole.UserRole + 7)
        all_item.setIcon(
            self._get_icon(Severity.ERROR)
            if total_errors
            else self._get_icon(Severity.WARNING)
            if total_warnings
            else self._get_icon(Severity.SUCCESS)
        )
        root.appendRow(all_item)

        from collections import defaultdict

        crit_groups: dict = defaultdict(list)
        for rule_id, findings in grouped_data.items():
            crit = get_criticality(rule_id) if get_criticality else Criticality.MEDIUM
            crit_groups[crit].append((rule_id, findings))

        for criticality in sorted(crit_groups, key=lambda c: c.value):
            rules = crit_groups[criticality]
            group_findings_full = [f for _, findings in rules for f in findings]
            group_findings_active = [f for f in group_findings_full if not f.ignore]
            g_errors = sum(1 for f in group_findings_active if f.severity == Severity.ERROR)
            g_warnings = sum(1 for f in group_findings_active if f.severity == Severity.WARNING)

            crit_item = QStandardItem()
            crit_item.setEditable(False)
            crit_item.setData(group_findings_full, QtCore.Qt.ItemDataRole.UserRole)
            crit_item.setData("criticality", QtCore.Qt.ItemDataRole.UserRole + 1)
            crit_item.setData(criticality.label, QtCore.Qt.ItemDataRole.UserRole + 2)
            crit_item.setData("0", QtCore.Qt.ItemDataRole.UserRole + 3)
            crit_item.setData(
                "#E24B4A" if g_errors else "#D4900A" if g_warnings else "#639922",
                QtCore.Qt.ItemDataRole.UserRole + 4,
            )
            crit_item.setData(g_errors, QtCore.Qt.ItemDataRole.UserRole + 6)
            crit_item.setData(g_warnings, QtCore.Qt.ItemDataRole.UserRole + 7)
            crit_item.setIcon(
                self._get_icon(Severity.ERROR)
                if g_errors
                else self._get_icon(Severity.WARNING)
                if g_warnings
                else self._get_icon(Severity.SUCCESS)
            )
            all_item.appendRow(crit_item)

            for rule_id, findings in rules:
                error_count = sum(
                    1 for f in findings if not f.ignore and f.severity == Severity.ERROR
                )
                warning_count = sum(
                    1 for f in findings if not f.ignore and f.severity == Severity.WARNING
                )
                active_count = sum(1 for f in findings if not f.ignore)
                all_ignored = active_count == 0
                severity = findings[0].severity
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
                rule_item.setIcon(
                    self._get_icon(Severity.SUCCESS) if all_ignored else self._get_icon(severity)
                )

                for finding in findings:
                    name = finding.refs[0].label if finding.refs else "Unknown"
                    child = QStandardItem()
                    child.setEditable(False)
                    child.setData(finding, QtCore.Qt.ItemDataRole.UserRole)
                    child.setData("finding", QtCore.Qt.ItemDataRole.UserRole + 1)
                    child.setData(name, QtCore.Qt.ItemDataRole.UserRole + 2)
                    child.setData(finding.overview, QtCore.Qt.ItemDataRole.UserRole + 3)
                    child.setData(
                        severity_color(finding.severity), QtCore.Qt.ItemDataRole.UserRole + 4
                    )
                    child.setData(finding.ignore, QtCore.Qt.ItemDataRole.UserRole + 5)
                    child.setIcon(self._get_icon(finding.severity))
                    rule_item.appendRow(child)

                crit_item.appendRow(rule_item)
                if rule_id.label in expanded_labels:
                    self.form.tvResults.setExpanded(self.model.indexFromItem(rule_item), True)

            # if g_errors or g_warnings:
            #     self.form.tvResults.setExpanded(self.model.indexFromItem(crit_item), True)

        passed_rules = [r for r in all_process_rules if r not in grouped_data]
        if passed_rules:
            passed_item = QStandardItem()
            passed_item.setEditable(False)
            passed_item.setData([], QtCore.Qt.ItemDataRole.UserRole)
            passed_item.setData("criticality", QtCore.Qt.ItemDataRole.UserRole + 1)
            passed_item.setData("Passed", QtCore.Qt.ItemDataRole.UserRole + 2)
            passed_item.setData("0", QtCore.Qt.ItemDataRole.UserRole + 3)
            passed_item.setData("#639922", QtCore.Qt.ItemDataRole.UserRole + 4)
            passed_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 6)
            passed_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 7)
            passed_item.setIcon(self._get_icon(Severity.SUCCESS))
            root.appendRow(passed_item)

            for rule in passed_rules:
                pass_item = QStandardItem()
                pass_item.setEditable(False)
                pass_item.setData([], QtCore.Qt.ItemDataRole.UserRole)
                pass_item.setData("rule", QtCore.Qt.ItemDataRole.UserRole + 1)
                pass_item.setData(rule.label, QtCore.Qt.ItemDataRole.UserRole + 2)
                pass_item.setData("0", QtCore.Qt.ItemDataRole.UserRole + 3)
                pass_item.setData("#639922", QtCore.Qt.ItemDataRole.UserRole + 4)
                pass_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 6)
                pass_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 7)
                pass_item.setIcon(QtGui.QIcon(":/icons/dfm_success.svg"))
                passed_item.appendRow(pass_item)

        self.form.tvResults.setCurrentIndex(self.model.index(0, 0))
        self.form.tvResults.expand(self.model.index(0, 0))
        self.form.tvResults.setFocus()

    def get_export_config(self) -> CSVExportConfig:
        return CSVExportConfig(
            include_criticality=self.form.cbColCriticality.isChecked(),
            include_feedback=self.form.cbColFeedback.isChecked(),
            include_metadata=self.form.cbColMetadata.isChecked(),
            include_unit=self.form.cbColUnit.isChecked(),
            include_errors=self.form.cbRowErrors.isChecked(),
            include_warnings=self.form.cbRowWarnings.isChecked(),
            include_passed=self.form.cbRowPassed.isChecked(),
            include_ignored=self.form.cbRowIgnored.isChecked(),
            delimiter=CSVResultExporter.DELIMITER_MAP.get(
                self.form.cbDelimiter.currentIndex(), ","
            ),
        )

    def _get_icon(self, severity: Severity) -> QtGui.QIcon:
        """Returns a severity circle icon for the given severity level."""
        icon_map = {
            Severity.ERROR: ":/icons/dfm_error.svg",
            Severity.WARNING: ":/icons/dfm_warning.svg",
            Severity.INFO: ":/icons/dfm_info.svg",
        }
        path = icon_map.get(severity, ":/icons/dfm_success.svg")
        return QtGui.QIcon(path)

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
        item_type = item.data(QtCore.Qt.ItemDataRole.UserRole + 1)
        label = item.data(QtCore.Qt.ItemDataRole.UserRole + 2) or ""
        menu = QtWidgets.QMenu()

        if isinstance(data, list):
            findings = data
            active = [f for f in findings if not f.ignore]
            ignored = [f for f in findings if f.ignore]

            if not findings and not active and not ignored:
                return

            if findings:
                zoom_action = menu.addAction("Zoom to All Findings")
                zoom_action.triggered.connect(
                    lambda: self.on_zoom_to_rule(findings) if self.on_zoom_to_rule else None
                )
                menu.addSeparator()

            if active:
                ignore_all = menu.addAction(f"Ignore All ({len(active)})")
                ignore_all.triggered.connect(
                    lambda checked=False, a=active: (
                        self.on_toggle_ignore_all(a) if self.on_toggle_ignore_all else None
                    )
                )
            if ignored:
                restore_all = menu.addAction(f"Restore All ({len(ignored)})")
                restore_all.triggered.connect(
                    lambda checked=False, ig=ignored: (
                        self.on_toggle_ignore_all(ig) if self.on_toggle_ignore_all else None
                    )
                )

            if findings:
                menu.addSeparator()
                copy_action = menu.addAction("Copy Summary")
                copy_action.triggered.connect(
                    lambda checked=False, f=findings, l=label: self._copy_summary(f, l)
                )

        elif isinstance(data, CheckResult):
            finding = data

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

        if not menu.isEmpty():
            menu.exec(self.form.tvResults.viewport().mapToGlobal(point))

    def _copy_summary(self, findings: list[CheckResult], label: str):
        """Copies a summary for any tree level"""
        lines = [f"{label} - {len(findings)} finding(s)"]
        for f in findings:
            status = "[ignored]" if f.ignore else f"[{f.severity.name}]"
            rule_prefix = f"[{f.rule_id.label}] " if label in ("All findings",) else ""
            lines.append(f"  {status} {rule_prefix}{f.overview}")
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

    def _handle_selection_change(self, current, previous):
        item = self.model.itemFromIndex(current)
        if item and self.on_row_selected:
            data = item.data(QtCore.Qt.ItemDataRole.UserRole)
            item_type = item.data(QtCore.Qt.ItemDataRole.UserRole + 1)
            if item_type in ("all", "criticality"):
                self.on_row_selected(data)
            elif item_type == "rule" and not data:
                self.on_row_selected([])
            elif data:
                self.on_row_selected(data)

    def eventFilter(self, obj, event):
        if self.form is None:
            return False
        if obj is self.form.tvResults and event.type() == QtCore.QEvent.Type.KeyPress:
            if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                index = self.form.tvResults.currentIndex()
                item = self.model.itemFromIndex(index)
                if item:
                    data = item.data(QtCore.Qt.ItemDataRole.UserRole)
                    if isinstance(data, list):
                        if self.form.tvResults.isExpanded(index):
                            self.form.tvResults.collapse(index)
                        else:
                            self.form.tvResults.expand(index)
                        if self.on_zoom_to_rule:
                            self.on_zoom_to_rule(data)
                    elif isinstance(data, CheckResult):
                        if self.on_row_double_clicked:
                            self.on_row_double_clicked(data)
                return True
        return False

    def getStandardButtons(self):
        return (
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Close
        )

    def on_save_clicked(self):
        print("saved")

    def clicked(self, button):
        if button == QtWidgets.QDialogButtonBox.StandardButton.Save:
            self._save_clicked = True
            if self.on_save_clicked:
                self.on_save_clicked()

    def reject(self):
        if self.form:
            self.form.tvResults.removeEventFilter(self)
        if self.on_closed:
            self.on_closed()
        self.form = None
        Gui.Control.closeDialog()

    def accept(self):
        if self._save_clicked:
            self._save_clicked = False
            return
        if self.form:
            self.form.tvResults.removeEventFilter(self)
        if self.on_closed:
            self.on_closed()
        self.form = None
        Gui.Control.closeDialog()
