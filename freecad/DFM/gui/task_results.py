# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Task-panel view for DFM analysis results.

This module provides the :class:`TaskResults` passive view that displays
findings in a configurable tree.  A :class:`ResultsToolbar` lets the user
switch between four grouping strategies, four sort orders, and three
severity / ignored filters — all without re-running any analysis.
"""

from collections import defaultdict
from typing import Any, Callable

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtGui import QStandardItemModel, QStandardItem

import FreeCADGui as Gui  # type: ignore

from ..core.models import CheckResult, Severity
from ..core.rules import Criticality, Rulebook

from ..gui.results.delegates import DFMTreeDelegate
from ..gui.results.toolbar import GroupMode, ResultsToolbar, SortMode
from ..gui.results.visuals import severity_color
from ..gui.results.utils import CSVExportConfig, CSVResultExporter


class _PanelResizeFilter(QtCore.QObject):
    """Recalculates tree fill-height when the task-panel viewport resizes.

    This fires on overlay ↔ docked mode transitions and any other
    resize of the scroll-area viewport.
    """

    def __init__(self, task_results: "TaskResults"):
        super().__init__()
        self._task = task_results

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.Type.Resize and self._task.form:
            # Defer so the new geometry has settled before we measure.
            QtCore.QTimer.singleShot(0, self._task._update_tree_fill)
        return False


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
        self.form.leVerdict.setReadOnly(True)
        self.form.pbExportDiff.setIcon(QtGui.QIcon(":/icons/export_diff.svg"))

        self._save_clicked = False
        self._scroll_area: QtWidgets.QScrollArea | None = None
        self._resize_filter: _PanelResizeFilter | None = None

        # Stored data for rebuilds triggered by the toolbar.
        self._grouped_data: dict = {}
        self._all_process_rules: list = []
        self._get_criticality: Callable | None = None

        self.on_closed: Callable[[], None] | None = None
        self.on_row_selected: Callable[[CheckResult | list[CheckResult]], None] | None = None
        self.on_row_double_clicked: Callable[[CheckResult], None] | None = None
        self.on_toggle_ignore: Callable[[CheckResult], None] | None = None
        self.on_export_clicked: Callable[[], None] | None = None
        self.on_toggle_ignore_all: Callable[[list[CheckResult]], None] | None = None
        self.on_zoom_to_rule: Callable[[list[CheckResult]], None] | None = None
        self.on_material_changed: Callable[[str], None] | None = None

        self.form.tvResults.doubleClicked.connect(self._handle_double_click)
        self.form.pbExportCSV.clicked.connect(self._handle_export_btn)

        self.form.cbDelimiter.addItems(["Comma  ,", "Semicolon  ;", "Tab"])

        self.form.tvResults.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.form.tvResults.customContextMenuRequested.connect(self._show_context_menu)
        self.form.tvResults.selectionModel().currentChanged.connect(self._handle_selection_change)

        # Inject the toolbar above the tree view.
        self._toolbar = ResultsToolbar()
        self._inject_toolbar()
        self._toolbar.settings_changed.connect(self._on_toolbar_changed)
        self._toolbar.expand_all_requested.connect(self._expand_all)
        self._toolbar.collapse_all_requested.connect(self._collapse_all)

    # ── Toolbar injection ────────────────────────────────

    def _inject_toolbar(self):
        """Insert the toolbar above the tree view inside the Findings group box.

        The .ui file places ``tvResults`` directly inside the group box's
        QHBoxLayout.  We remove the tree, build a vertical container
        (toolbar + tree), and put that container in the original layout
        position.  No .ui modification or resource recompilation required.
        """
        group_box: QtWidgets.QGroupBox = self.form.groupBox_2
        old_layout: QtWidgets.QHBoxLayout = group_box.layout()

        # Remove the tree from its current layout.
        old_layout.removeWidget(self.form.tvResults)

        # Build a container: toolbar on top, tree below.
        container = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        vbox.addWidget(self._toolbar)
        vbox.addWidget(self.form.tvResults, 1)

        old_layout.addWidget(container)

    # ── Tree building (public entry) ─────────────────────

    def render_tree(self, grouped_data: dict, all_process_rules: list, get_criticality=None):
        """Store fresh analysis data and rebuild the tree.

        Called by the presenter whenever the underlying data changes
        (new analysis, material switch, ignore toggle).  The toolbar's
        current grouping / sort / filter settings are applied
        automatically.
        """
        self._grouped_data = grouped_data
        self._all_process_rules = all_process_rules
        self._get_criticality = get_criticality
        self._build_tree()

        # Defer fill-height setup to the next event-loop tick.  At this
        # point ``Gui.Control.showDialog()`` hasn't run yet, so the form
        # isn't inside the task panel's scroll area.  By the time the
        # timer fires the dialog is shown and we can configure the
        # scroll area to let the form (and therefore the tree) fill all
        # available vertical space.
        QtCore.QTimer.singleShot(0, self._setup_fill_height)

    # ── Internal tree build ──────────────────────────────

    def _build_tree(self):
        """Rebuild the tree from stored data using toolbar settings."""
        expanded = self._collect_expanded_labels()

        self.model.clear()
        self.form.tvResults.setItemDelegate(DFMTreeDelegate())
        root = self.model.invisibleRootItem()

        # Apply filters to every rule's findings list.
        filtered: dict[Rulebook, list[CheckResult]] = {}
        total_all = 0
        total_shown = 0
        for rule_id, findings in self._grouped_data.items():
            total_all += len(findings)
            visible = self._apply_filters(findings)
            if visible:
                filtered[rule_id] = visible
            total_shown += len(visible)

        group = self._toolbar.group_mode
        sort_key = self._get_finding_sort_key()

        if group == GroupMode.CRITICALITY_RULE:
            self._build_criticality_rule(root, filtered, sort_key, expanded)
        elif group == GroupMode.RULE_ONLY:
            self._build_rule_only(root, filtered, sort_key, expanded)
        elif group == GroupMode.GEOMETRY:
            self._build_geometry(root, filtered, sort_key, expanded)
        elif group == GroupMode.FLAT:
            self._build_flat(root, filtered, sort_key)

        # Passed rules section (not in Geometry or Flat modes).
        if group in (GroupMode.CRITICALITY_RULE, GroupMode.RULE_ONLY):
            self._add_passed_section(root)

        # Select first row, expand top level, set focus.
        if self.model.rowCount():
            self.form.tvResults.setCurrentIndex(self.model.index(0, 0))
            self.form.tvResults.expand(self.model.index(0, 0))
        self.form.tvResults.setFocus()

        self._toolbar.update_count(total_shown, total_all)

    # ── Filtering ────────────────────────────────────────

    def _apply_filters(self, findings: list[CheckResult]) -> list[CheckResult]:
        """Return the subset of *findings* that pass toolbar filters."""
        show_errors = self._toolbar.show_errors
        show_warnings = self._toolbar.show_warnings
        show_ignored = self._toolbar.show_ignored

        out: list[CheckResult] = []
        for f in findings:
            # Ignored toggle.
            if f.ignore and not show_ignored:
                continue
            # Severity toggles apply only to non-ignored findings.
            if not f.ignore:
                if f.severity == Severity.ERROR and not show_errors:
                    continue
                if f.severity == Severity.WARNING and not show_warnings:
                    continue
            out.append(f)
        return out

    # ── Sorting ──────────────────────────────────────────

    def _get_finding_sort_key(self) -> Callable[[CheckResult], Any]:
        """Return a sort-key function for leaf findings."""
        mode = self._toolbar.sort_mode
        if mode == SortMode.SEVERITY:
            # Worst severity first (ERROR > WARNING); ignored after active.
            return lambda f: (f.ignore, -f.severity.value)
        elif mode == SortMode.GEOMETRY_INDEX:
            return lambda f: (f.refs[0].index if f.refs else 9999,)
        elif mode == SortMode.RULE_NAME:
            return lambda f: f.rule_id.label.lower()
        elif mode == SortMode.MEASURED_VALUE:
            # Worst deviation first.  For "min" comparisons the smallest
            # measured value is worst; for "max" the largest is.
            def _measured_key(f: CheckResult):
                if f.value is None:
                    return (1, 0.0)
                if f.comparison == "max":
                    return (0, -f.value)
                return (0, f.value)

            return _measured_key
        # Fallback – unreachable if the enum is exhaustive.
        return lambda f: 0  # pragma: no cover

    def _sort_rule_groups(
        self, rule_items: list[tuple[Rulebook, list[CheckResult]]]
    ) -> list[tuple[Rulebook, list[CheckResult]]]:
        """Sort (rule_id, filtered_findings) pairs at the group level."""
        mode = self._toolbar.sort_mode
        if mode == SortMode.SEVERITY:
            def _key(pair):
                _, findings = pair
                active = [f for f in findings if not f.ignore]
                if not active:
                    return (2, 0, "")
                worst = max(f.severity.value for f in active)
                return (2 - worst, -len(active), pair[0].label)
            return sorted(rule_items, key=_key)
        elif mode == SortMode.GEOMETRY_INDEX:
            def _key(pair):
                _, findings = pair
                idxs = [f.refs[0].index for f in findings if f.refs]
                return min(idxs) if idxs else 9999
            return sorted(rule_items, key=_key)
        elif mode == SortMode.RULE_NAME:
            return sorted(rule_items, key=lambda p: p[0].label.lower())
        elif mode == SortMode.MEASURED_VALUE:
            def _key(pair):
                _, findings = pair
                vals = [f.value for f in findings if f.value is not None]
                if not vals:
                    return 9999.0
                comp = pair[0].comparison
                return -max(vals) if comp == "max" else min(vals)
            return sorted(rule_items, key=_key)
        return rule_items  # pragma: no cover

    # ── Grouping strategies ──────────────────────────────

    def _build_criticality_rule(
        self,
        root: QStandardItem,
        filtered: dict[Rulebook, list[CheckResult]],
        sort_key: Callable,
        expanded: set[str],
    ):
        """Criticality → Rule → Finding  (default hierarchy)."""
        # "Recommendations" root item.
        all_findings = [f for fs in filtered.values() for f in fs]
        all_item = self._make_group_item("Recommendations", all_findings, "all")
        root.appendRow(all_item)

        # Partition rules by criticality.
        crit_groups: dict[Criticality, list[tuple[Rulebook, list[CheckResult]]]] = defaultdict(list)
        for rule_id, findings in filtered.items():
            crit = self._get_criticality(rule_id) if self._get_criticality else Criticality.MEDIUM
            crit_groups[crit].append((rule_id, findings))

        for criticality in sorted(crit_groups, key=lambda c: c.value):
            rules = crit_groups[criticality]
            group_findings = [f for _, fs in rules for f in fs]
            crit_item = self._make_group_item(criticality.label, group_findings, "criticality")
            all_item.appendRow(crit_item)

            for rule_id, findings in self._sort_rule_groups(rules):
                sorted_findings = sorted(findings, key=sort_key)
                rule_item = self._make_group_item(rule_id.label, sorted_findings, "rule")
                self._populate_finding_children(rule_item, sorted_findings)
                crit_item.appendRow(rule_item)
                if rule_id.label in expanded:
                    self.form.tvResults.setExpanded(self.model.indexFromItem(rule_item), True)

    def _build_rule_only(
        self,
        root: QStandardItem,
        filtered: dict[Rulebook, list[CheckResult]],
        sort_key: Callable,
        expanded: set[str],
    ):
        """Rule → Finding  (skip the criticality tier)."""
        all_findings = [f for fs in filtered.values() for f in fs]
        all_item = self._make_group_item("Recommendations", all_findings, "all")
        root.appendRow(all_item)

        rule_items = list(filtered.items())
        for rule_id, findings in self._sort_rule_groups(rule_items):
            sorted_findings = sorted(findings, key=sort_key)
            rule_item = self._make_group_item(rule_id.label, sorted_findings, "rule")
            self._populate_finding_children(rule_item, sorted_findings)
            all_item.appendRow(rule_item)
            if rule_id.label in expanded:
                self.form.tvResults.setExpanded(self.model.indexFromItem(rule_item), True)

    def _build_geometry(
        self,
        root: QStandardItem,
        filtered: dict[Rulebook, list[CheckResult]],
        sort_key: Callable,
        expanded: set[str],
    ):
        """Geometry (Face / Edge) → Finding."""
        # Collect all findings and bucket by geometry ref label.
        geo_groups: dict[str, list[CheckResult]] = defaultdict(list)
        all_findings: list[CheckResult] = []
        seen_per_geo: dict[str, set[int]] = defaultdict(set)
        for findings in filtered.values():
            for f in findings:
                all_findings.append(f)
                if f.refs:
                    for ref in f.refs:
                        fid = id(f)
                        if fid not in seen_per_geo[ref.label]:
                            seen_per_geo[ref.label].add(fid)
                            geo_groups[ref.label].append(f)
                else:
                    geo_groups["Unknown"].append(f)

        all_item = self._make_group_item("Recommendations", all_findings, "all")
        root.appendRow(all_item)

        # Sort geometry keys naturally (Face1, Face2, … Edge1, Edge2, …).
        def _geo_sort(label: str):
            import re
            m = re.match(r"([A-Za-z]+)(\d+)", label)
            if m:
                return (m.group(1), int(m.group(2)))
            return (label, 0)

        for geo_label in sorted(geo_groups, key=_geo_sort):
            findings = geo_groups[geo_label]
            sorted_findings = sorted(findings, key=sort_key)
            geo_item = self._make_group_item(geo_label, sorted_findings, "rule")
            self._populate_finding_children(geo_item, sorted_findings)
            all_item.appendRow(geo_item)
            if geo_label in expanded:
                self.form.tvResults.setExpanded(self.model.indexFromItem(geo_item), True)

    def _build_flat(
        self,
        root: QStandardItem,
        filtered: dict[Rulebook, list[CheckResult]],
        sort_key: Callable,
    ):
        """Single flat list — no nesting."""
        all_findings = sorted(
            [f for fs in filtered.values() for f in fs], key=sort_key
        )
        all_item = self._make_group_item("Recommendations", all_findings, "all")
        root.appendRow(all_item)

        for finding in all_findings:
            primary = finding.refs[0].label if finding.refs else "Unknown"
            secondary = f"[{finding.rule_id.label}] {finding.overview}"
            child = self._make_finding_item(finding, primary, secondary)
            all_item.appendRow(child)

    # ── Item factories ───────────────────────────────────

    def _make_group_item(
        self,
        label: str,
        findings: list[CheckResult],
        item_type: str,
    ) -> QStandardItem:
        """Create a group node (Recommendations / criticality / rule / geometry)."""
        active = [f for f in findings if not f.ignore]
        error_count = sum(1 for f in active if f.severity == Severity.ERROR)
        warning_count = sum(1 for f in active if f.severity == Severity.WARNING)
        active_count = len(active)
        ignored_count = sum(1 for f in findings if f.ignore)
        all_ignored = active_count == 0 and len(findings) > 0

        if item_type == "rule":
            severity = findings[0].severity if findings else Severity.SUCCESS
            color = "#666666" if all_ignored else severity_color(severity)
        else:
            color = (
                "#E24B4A" if error_count
                else "#D4900A" if warning_count
                else "#666666" if all_ignored
                else "#639922"
            )

        item = QStandardItem()
        item.setEditable(False)
        item.setData(findings, QtCore.Qt.ItemDataRole.UserRole)
        item.setData(item_type, QtCore.Qt.ItemDataRole.UserRole + 1)
        item.setData(label, QtCore.Qt.ItemDataRole.UserRole + 2)
        item.setData(str(active_count), QtCore.Qt.ItemDataRole.UserRole + 3)
        item.setData(color, QtCore.Qt.ItemDataRole.UserRole + 4)
        item.setData(error_count, QtCore.Qt.ItemDataRole.UserRole + 6)
        item.setData(warning_count, QtCore.Qt.ItemDataRole.UserRole + 7)
        item.setData(ignored_count, QtCore.Qt.ItemDataRole.UserRole + 8)

        # Icon: grey out original severity when all findings are ignored.
        if error_count:
            item.setIcon(self._get_icon(Severity.ERROR))
        elif warning_count:
            item.setIcon(self._get_icon(Severity.WARNING))
        elif all_ignored:
            worst = max((f.severity for f in findings), key=lambda s: s.value)
            item.setIcon(self._get_greyed_icon(worst))
        else:
            item.setIcon(self._get_icon(Severity.SUCCESS))

        return item

    def _make_finding_item(
        self,
        finding: CheckResult,
        primary: str,
        secondary: str,
    ) -> QStandardItem:
        """Create a leaf finding node."""
        child = QStandardItem()
        child.setEditable(False)
        child.setData(finding, QtCore.Qt.ItemDataRole.UserRole)
        child.setData("finding", QtCore.Qt.ItemDataRole.UserRole + 1)
        child.setData(primary, QtCore.Qt.ItemDataRole.UserRole + 2)
        child.setData(secondary, QtCore.Qt.ItemDataRole.UserRole + 3)
        child.setData(severity_color(finding.severity), QtCore.Qt.ItemDataRole.UserRole + 4)
        child.setData(finding.ignore, QtCore.Qt.ItemDataRole.UserRole + 5)
        if finding.ignore:
            child.setIcon(self._get_greyed_icon(finding.severity))
        else:
            child.setIcon(self._get_icon(finding.severity))
        return child

    def _populate_finding_children(
        self,
        parent: QStandardItem,
        findings: list[CheckResult],
    ):
        """Add leaf nodes under *parent* for each finding."""
        for finding in findings:
            name = finding.refs[0].label if finding.refs else "Unknown"
            child = self._make_finding_item(finding, name, finding.overview)
            parent.appendRow(child)

    # ── Passed rules ─────────────────────────────────────

    def _add_passed_section(self, root: QStandardItem):
        """Append a 'Passed' group for rules with zero findings.

        Uses the *unfiltered* ``_grouped_data`` so that filtering out
        errors / warnings doesn't create false 'Passed' entries.
        """
        passed_rules = [r for r in self._all_process_rules if r not in self._grouped_data]
        if not passed_rules:
            return

        passed_item = self._make_group_item("Passed", [], "criticality")
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
            pass_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 8)
            pass_item.setIcon(QtGui.QIcon(":/icons/dfm_success.svg"))
            passed_item.appendRow(pass_item)

    # ── Expand / Collapse helpers ────────────────────────

    def _collect_expanded_labels(self) -> set[str]:
        """Return the labels of all currently-expanded items."""
        labels: set[str] = set()

        def _walk(parent_item: QStandardItem):
            for row in range(parent_item.rowCount()):
                child = parent_item.child(row)
                if self.form.tvResults.isExpanded(self.model.indexFromItem(child)):
                    lbl = child.data(QtCore.Qt.ItemDataRole.UserRole + 2)
                    if lbl:
                        labels.add(lbl)
                _walk(child)

        _walk(self.model.invisibleRootItem())
        return labels

    def _expand_all(self):
        """Expand every node in the tree."""
        self.form.tvResults.expandAll()

    def _collapse_all(self):
        """Collapse everything, then re-expand only top-level items."""
        self.form.tvResults.collapseAll()
        for row in range(self.model.rowCount()):
            self.form.tvResults.expand(self.model.index(row, 0))

    # ── Toolbar callback ─────────────────────────────────

    def _on_toolbar_changed(self):
        """Rebuild the tree when any toolbar control changes."""
        if self._grouped_data:
            self._build_tree()

    # ── Fill-height helpers ──────────────────────────────

    def _setup_fill_height(self):
        """Make the tree fill all available vertical space in the task panel.

        FreeCAD wraps the form inside a ``QScrollArea`` in the ComboView.
        By default the form is sized to its ``sizeHint`` — which is small
        when the tree is collapsed — leaving empty space below.

        We fix this by:
        1. Setting ``widgetResizable(True)`` so the form expands to fill
           the scroll-area viewport.
        2. Calculating the exact remaining height below the tree's top
           edge and setting it as the tree's ``minimumHeight``.
        3. Installing a resize-event filter on the viewport so the
           height is recalculated when the panel switches between
           overlay and docked modes (or is otherwise resized).
        """
        if not self.form:
            return

        # Already configured (render_tree may be called more than once).
        if self._scroll_area is not None:
            self._update_tree_fill()
            return

        # Walk up to find FreeCAD's scroll-area wrapper.
        widget = self.form.parentWidget()
        while widget:
            if isinstance(widget, QtWidgets.QScrollArea):
                self._scroll_area = widget
                break
            widget = widget.parentWidget()

        if self._scroll_area:
            self._scroll_area.setWidgetResizable(True)
            # Watch for viewport resizes (overlay ↔ docked transitions).
            self._resize_filter = _PanelResizeFilter(self)
            self._scroll_area.viewport().installEventFilter(self._resize_filter)

        self._update_tree_fill()

    def _update_tree_fill(self):
        """Set the tree's minimum height so it fills the remaining panel space.

        Uses the distance from the form's top to the tree's top (which
        is constant regardless of scroll position) and subtracts that
        plus bottom margins from the viewport height.
        """
        if not self.form:
            return

        tree = self.form.tvResults

        if self._scroll_area:
            viewport_h = self._scroll_area.viewport().height()
        else:
            # Fallback: use whatever parent height is available.
            parent = self.form.parentWidget()
            if not parent:
                return
            viewport_h = parent.height()

        # Offset from the form's top edge to the tree's top edge.
        # This is constant — determined by the header grid, tab bar,
        # group-box title, and their cumulative layout margins.
        tree_offset = tree.mapTo(self.form, QtCore.QPoint(0, 0)).y()

        # Bottom margins: group-box frame + tab-content layout margin +
        # tab-widget frame + form layout margin  (≈ 25-30 px).
        bottom_margin = 30

        available = viewport_h - tree_offset - bottom_margin
        if available > 50:
            tree.setMinimumHeight(available)

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

    def _get_greyed_icon(self, severity: Severity) -> QtGui.QIcon:
        """Returns a greyed-out (low opacity) version of the severity icon."""
        original = self._get_icon(severity)
        pixmap = original.pixmap(QtCore.QSize(16, 16))

        greyed = QtGui.QPixmap(pixmap.size())
        greyed.fill(QtCore.Qt.GlobalColor.transparent)

        painter = QtGui.QPainter(greyed)
        painter.setOpacity(0.35)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()

        return QtGui.QIcon(greyed)

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

    def _cleanup_fill_height(self):
        """Remove the resize filter and release scroll-area references."""
        if self._scroll_area and self._resize_filter:
            self._scroll_area.viewport().removeEventFilter(self._resize_filter)
        self._scroll_area = None
        self._resize_filter = None

    def reject(self):
        if self.form:
            self.form.tvResults.removeEventFilter(self)
        self._cleanup_fill_height()
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
        self._cleanup_fill_height()
        if self.on_closed:
            self.on_closed()
        self.form = None
        Gui.Control.closeDialog()
