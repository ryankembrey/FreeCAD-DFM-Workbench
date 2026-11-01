# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""History tab: compare two runs, see what changed, and follow the trend.

Layout, top to bottom: run pickers with a swap button, regressed / resolved /
unchanged counts, the changed rules grouped by outcome, a compact trend chart
of recent runs with the compared runs shaded, and an Export Comparison
button. Content keeps its natural height; spare space stays below it.
"""

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ...rule_cards import DOWNLOAD_SVG, SWAP_SVG, apply_svg_icon
from .charts import TrendChart, legend
from .common import (
    C_ERROR, C_PASS, C_WARNING, LINE, SOFT_LINE, TINT, muted_label, section_header, status_pill,
)

# Diff statuses (see app.history.RuleDiff.status) grouped by outcome.
_GROUPS = (
    ("Regressed", ("regressed", "new"), True),
    ("Resolved", ("resolved", "improved"), True),
    ("Unchanged", ("unchanged",), False),  # collapsed by default
)

_ICONS = {
    "error": ":/icons/dfm_error.svg",
    "warning": ":/icons/dfm_warning.svg",
    "pass": ":/icons/dfm_success.svg",
}


class HistoryTab(QtWidgets.QWidget):
    """The History tab's contents. The presenter fills it with run data."""

    compare_changed = QtCore.Signal()
    export_requested = QtCore.Signal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self._message = muted_label()
        self._message.setWordWrap(True)
        self._message.hide()
        root.addWidget(self._message)

        # -- Compare -------------------------------------------------------
        self._compare = QtWidgets.QWidget()
        compare = QtWidgets.QVBoxLayout(self._compare)
        compare.setContentsMargins(0, 0, 0, 0)
        compare.setSpacing(8)
        compare.addWidget(section_header("Compare"))

        pickers = QtWidgets.QHBoxLayout()
        pickers.setSpacing(6)
        self.cb_a = QtWidgets.QComboBox()
        self.cb_b = QtWidgets.QComboBox()
        for cb, tip in ((self.cb_a, "Earlier run"), (self.cb_b, "Later run")):
            cb.setToolTip(tip)
            cb.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(8)
            cb.currentIndexChanged.connect(self.compare_changed)
        self.btn_swap = QtWidgets.QToolButton()
        self.btn_swap.setToolTip("Swap runs")
        self.btn_swap.setAccessibleName("Swap runs")
        self.btn_swap.setAutoRaise(True)
        self.btn_swap.setFixedSize(26, 24)
        apply_svg_icon(self.btn_swap, SWAP_SVG, 16)
        self.btn_swap.clicked.connect(self._swap)
        pickers.addWidget(self.cb_a, stretch=1)
        pickers.addWidget(self.btn_swap)
        pickers.addWidget(self.cb_b, stretch=1)
        compare.addLayout(pickers)

        stats = QtWidgets.QHBoxLayout()
        stats.setSpacing(6)
        self._stat_regressed = _Stat("regressed", C_ERROR)
        self._stat_resolved = _Stat("resolved", C_PASS)
        self._stat_unchanged = _Stat("unchanged", None)
        for stat in (self._stat_regressed, self._stat_resolved, self._stat_unchanged):
            stats.addWidget(stat, stretch=1)
        compare.addLayout(stats)

        self._changes = _ChangeList()
        compare.addWidget(self._changes)
        root.addWidget(self._compare)

        # -- Trend ---------------------------------------------------------
        self._trend = QtWidgets.QWidget()
        trend = QtWidgets.QVBoxLayout(self._trend)
        trend.setContentsMargins(0, 0, 0, 0)
        trend.setSpacing(4)
        self._trend_header = section_header("Trend")
        trend.addWidget(self._trend_header)
        self.chart = TrendChart()
        trend.addWidget(self.chart)
        trend.addWidget(legend(("Errors", C_ERROR), ("Warnings", C_WARNING),
                               ("Shaded: compared runs", "rgba(61, 143, 216, 90)")))
        root.addWidget(self._trend)

        # -- Footer --------------------------------------------------------
        footer = QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        self.btn_export = QtWidgets.QPushButton("Export Comparison")
        self.btn_export.setToolTip("Save this comparison as a CSV file")
        apply_svg_icon(self.btn_export, DOWNLOAD_SVG, 16)
        self.btn_export.clicked.connect(self.export_requested)
        footer.addWidget(self.btn_export)
        self._footer = QtWidgets.QWidget()
        self._footer.setLayout(footer)
        footer.setContentsMargins(0, 6, 0, 0)
        root.addWidget(self._footer)

    # ------------------------------------------------------------------
    # Public API (used by the presenter)
    # ------------------------------------------------------------------

    def set_runs(self, runs: list[tuple[int, str]]) -> None:
        """Fill the pickers with (run number, label); select the last two runs."""
        for cb in (self.cb_a, self.cb_b):
            cb.blockSignals(True)
            cb.clear()
            for number, label in runs:
                cb.addItem(label, number)
            cb.blockSignals(False)

        if not runs:
            self._show_message("No saved runs yet. Each analysis you run is added here.")
            return
        if len(runs) == 1:
            self._show_message("Only one run so far. Run the analysis again to compare results.")
            self._trend.show()
            return

        self._message.hide()
        self._compare.show()
        self._trend.show()
        self._footer.show()
        self.cb_a.blockSignals(True)
        self.cb_b.blockSignals(True)
        self.cb_a.setCurrentIndex(len(runs) - 2)
        self.cb_b.setCurrentIndex(len(runs) - 1)
        self.cb_a.blockSignals(False)
        self.cb_b.blockSignals(False)

    def selected_runs(self) -> tuple[Optional[int], Optional[int]]:
        return self.cb_a.currentData(), self.cb_b.currentData()

    def set_trend(self, runs: list[int], errors: list[int], warnings: list[int]) -> None:
        self.chart.set_data(runs, errors, warnings)
        note = self._trend_header.findChildren(QtWidgets.QLabel)
        if len(note) > 1:
            note[1].setText(f"last {len(runs)} runs")
        else:
            self._trend_header.layout().insertWidget(
                1, muted_label(f"last {len(runs)} runs", small=True)
            )
        self.chart.set_compared(*self.selected_runs())

    def set_diff(self, diffs: list) -> None:
        """Show the counts and grouped changes for a list of ``RuleDiff``."""
        groups = {title: [d for d in diffs if d.status in statuses] for title, statuses, _ in _GROUPS}
        self._stat_regressed.set_count(len(groups["Regressed"]))
        self._stat_resolved.set_count(len(groups["Resolved"]))
        self._stat_unchanged.set_count(len(groups["Unchanged"]))
        self._changes.set_groups(
            [(title, groups[title], expanded) for title, _, expanded in _GROUPS]
        )
        a, b = self.selected_runs()
        self.chart.set_compared(a, b)
        self.btn_export.setEnabled(a is not None and b is not None and a != b)

    def clear_diff(self, message: str) -> None:
        for stat in (self._stat_regressed, self._stat_resolved, self._stat_unchanged):
            stat.set_count(None)
        self._changes.set_message(message)
        self.btn_export.setEnabled(False)

    # ------------------------------------------------------------------

    def _show_message(self, text: str) -> None:
        self._message.setText(text)
        self._message.show()
        self._compare.hide()
        self._trend.hide()
        self._footer.hide()

    def _swap(self) -> None:
        a, b = self.cb_a.currentIndex(), self.cb_b.currentIndex()
        for cb, index in ((self.cb_a, b), (self.cb_b, a)):
            cb.blockSignals(True)
            cb.setCurrentIndex(index)
            cb.blockSignals(False)
        self.compare_changed.emit()


class _Stat(QtWidgets.QFrame):
    """Tinted count such as "2 regressed"."""

    def __init__(self, text: str, color: Optional[str]):
        super().__init__()
        self.setObjectName("HistoryStat")
        self._text = text
        self._color = color
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        self._label = QtWidgets.QLabel()
        self._label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        row.addWidget(self._label)
        self.set_count(None)

    def set_count(self, count: Optional[int]) -> None:
        active = bool(count) and self._color is not None
        tint = (
            f"rgba({QtGui.QColor(self._color).red()}, {QtGui.QColor(self._color).green()},"
            f" {QtGui.QColor(self._color).blue()}, 40)"
            if active else TINT
        )
        self.setStyleSheet(
            f"QFrame#HistoryStat {{ background: {tint}; border-radius: 3px; }}"
            "QFrame#HistoryStat QLabel { background: transparent; }"
        )
        number = "—" if count is None else str(count)
        color = f"color: {self._color};" if active else ""
        self._label.setText(
            f'<span style="font-size: 15px; font-weight: bold; {color}">{number}</span>'
            f"&nbsp; {self._text}"
        )


class _ChangeList(QtWidgets.QFrame):
    """Rules grouped by outcome, each with its result in both runs."""

    def __init__(self):
        super().__init__()
        self.setObjectName("ChangeList")
        self.setStyleSheet(
            f"QFrame#ChangeList {{ border: 1px solid {LINE}; border-radius: 3px; }}"
            "QFrame#ChangeList QWidget#ChangeRow QLabel { background: transparent; }"
        )
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

    def _clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def set_message(self, text: str) -> None:
        self._clear()
        lbl = muted_label(text)
        lbl.setContentsMargins(8, 6, 8, 6)
        self._layout.addWidget(lbl)

    def set_groups(self, groups: list[tuple[str, list, bool]]) -> None:
        self._clear()
        shown = [(t, d, e) for t, d, e in groups if d]
        if not shown:
            self.set_message("No rules had findings in either run.")
            return
        for title, diffs, expanded in shown:
            body = QtWidgets.QWidget()
            body_layout = QtWidgets.QVBoxLayout(body)
            body_layout.setContentsMargins(0, 0, 0, 0)
            body_layout.setSpacing(0)
            for d in sorted(diffs, key=lambda d: d.rule_label):
                body_layout.addWidget(_ChangeRow(d))
            body.setVisible(expanded)
            self._layout.addWidget(_GroupHeader(title, len(diffs), body, expanded))
            self._layout.addWidget(body)


class _GroupHeader(QtWidgets.QToolButton):
    """Clickable "▾ Regressed 2" row that shows or hides its rules."""

    def __init__(self, title: str, count: int, body: QtWidgets.QWidget, expanded: bool):
        super().__init__()
        self._body = body
        self.setText(f"{title}   {count}")
        self.setCheckable(True)
        self.setChecked(expanded)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setArrowType(QtCore.Qt.ArrowType.DownArrow if expanded else QtCore.Qt.ArrowType.RightArrow)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        font = self.font()
        font.setBold(True)
        self.setFont(font)
        self.setStyleSheet(
            f"QToolButton {{ background: {TINT}; border: none; border-top: 1px solid {SOFT_LINE};"
            " padding: 4px 6px; text-align: left; }"
            "QToolButton:checked { background: " + TINT + "; }"
        )
        self.toggled.connect(self._on_toggled)

    def _on_toggled(self, on: bool) -> None:
        self._body.setVisible(on)
        self.setArrowType(QtCore.Qt.ArrowType.DownArrow if on else QtCore.Qt.ArrowType.RightArrow)


class _ChangeRow(QtWidgets.QWidget):
    """One rule: status icon, name, and its result in each run."""

    def __init__(self, diff):
        super().__init__()
        self.setObjectName("ChangeRow")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(8)

        state = "error" if diff.current_errors else "warning" if diff.current_warnings else "pass"
        icon = QtWidgets.QLabel()
        icon.setPixmap(QtGui.QIcon(_ICONS[state]).pixmap(14, 14))
        icon.setFixedSize(14, 14)
        row.addWidget(icon)

        name = QtWidgets.QLabel(diff.rule_label)
        name.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        name.setToolTip(diff.rule_label)
        row.addWidget(name, stretch=1)

        row.addWidget(status_pill(diff.previous_errors, diff.previous_warnings))
        arrow = muted_label("→")
        row.addWidget(arrow)
        row.addWidget(status_pill(diff.current_errors, diff.current_warnings))
        self.setStyleSheet(f"QWidget#ChangeRow {{ border-top: 1px solid {SOFT_LINE}; }}")
