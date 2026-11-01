# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Export tab: choose a format, what to include, and save.

CSV is the only format for now. PDF report has its place on the format
switch already, disabled until it is available, so adding it later only
means enabling the button and adding its options section.
"""

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ....app.prefs import dfm_params
from ...rule_cards import DOWNLOAD_SVG, apply_svg_icon
from ..utils import CSVExportConfig, CSVResultExporter
from .common import C_ACCENT, LINE, muted_label, section_header

# Columns every CSV export has: Status, Rule, Face, Value, Comparison, Limit.
_BASE_COLUMNS = 6

_DELIMITERS = ("Comma ( , )", "Semicolon ( ; )", "Tab")

_SEGMENT_CSS = f"""
QPushButton {{
    border: 1px solid {LINE}; padding: 4px 10px; background: transparent;
}}
QPushButton:checked {{ background: {C_ACCENT}; border-color: {C_ACCENT}; color: white; }}
QPushButton#first {{ border-top-left-radius: 3px; border-bottom-left-radius: 3px; }}
QPushButton#last {{ border-left: none; border-top-right-radius: 3px; border-bottom-right-radius: 3px; }}
"""

_CHIP_CSS = f"""
QToolButton {{
    border: 1px solid {LINE}; border-radius: 11px; padding: 2px 9px 2px 6px; background: transparent;
}}
QToolButton:hover {{ background: rgba(127, 127, 127, 35); }}
QToolButton:checked {{ border: 2px solid {C_ACCENT}; padding: 1px 8px 1px 5px; }}
"""


class ExportTab(QtWidgets.QWidget):
    """The Export tab's contents."""

    export_requested = QtCore.Signal()

    # Preference keys for remembered choices.
    _PREFS = {
        "errors": ("ExportIncludeErrors", True),
        "warnings": ("ExportIncludeWarnings", True),
        "passed": ("ExportIncludePassed", True),
        "ignored": ("ExportIncludeIgnored", False),
        "criticality": ("ExportColumnCriticality", True),
        "unit": ("ExportColumnUnit", True),
        "feedback": ("ExportColumnFeedback", False),
        "metadata": ("ExportColumnMetadata", True),
    }

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._counts = {"errors": 0, "warnings": 0, "passed": 0,
                        "ignored_errors": 0, "ignored_warnings": 0}
        params = dfm_params()

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # -- Format --------------------------------------------------------
        root.addWidget(section_header("Format"))
        seg = QtWidgets.QHBoxLayout()
        seg.setSpacing(0)
        self.btn_csv = QtWidgets.QPushButton("CSV spreadsheet")
        self.btn_csv.setObjectName("first")
        self.btn_csv.setCheckable(True)
        self.btn_csv.setChecked(True)
        self.btn_pdf = QtWidgets.QPushButton("PDF report (coming soon)")
        self.btn_pdf.setObjectName("last")
        self.btn_pdf.setCheckable(True)
        self.btn_pdf.setEnabled(False)
        self.btn_pdf.setToolTip("PDF reports are not available yet.")
        group = QtWidgets.QButtonGroup(self)
        for b in (self.btn_csv, self.btn_pdf):
            b.setStyleSheet(_SEGMENT_CSS)
            group.addButton(b)
            seg.addWidget(b, stretch=1)
        root.addLayout(seg)

        # -- Include -------------------------------------------------------
        root.addWidget(section_header("Include"))
        chips = _FlowRow()
        self.chip_errors = self._chip("Errors", ":/icons/dfm_error.svg", "errors")
        self.chip_warnings = self._chip("Warnings", ":/icons/dfm_warning.svg", "warnings")
        self.chip_passed = self._chip("Passed", ":/icons/dfm_success.svg", "passed")
        self.chip_ignored = self._chip("Ignored", None, "ignored")
        for chip in (self.chip_errors, self.chip_warnings, self.chip_passed, self.chip_ignored):
            chips.add(chip)
        root.addWidget(chips)

        # -- Columns -------------------------------------------------------
        root.addWidget(section_header("Columns"))
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        self.cb_criticality = self._check("Criticality", "criticality")
        self.cb_unit = self._check("Unit", "unit")
        self.cb_feedback = self._check("Feedback message", "feedback")
        self.cb_metadata = self._check("Header with model and material", "metadata")
        self.cb_metadata.setToolTip("Adds the model, process, material and verdict above the table")
        grid.addWidget(self.cb_criticality, 0, 0)
        grid.addWidget(self.cb_unit, 0, 1)
        grid.addWidget(self.cb_feedback, 1, 0)
        grid.addWidget(self.cb_metadata, 1, 1)
        root.addLayout(grid)

        # -- CSV options ---------------------------------------------------
        self._csv_section = QtWidgets.QWidget()
        csv_layout = QtWidgets.QVBoxLayout(self._csv_section)
        csv_layout.setContentsMargins(0, 0, 0, 0)
        csv_layout.setSpacing(6)
        csv_layout.addWidget(section_header("CSV"))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Delimiter"))
        row.addStretch(1)
        self.cb_delimiter = QtWidgets.QComboBox()
        self.cb_delimiter.addItems(_DELIMITERS)
        self.cb_delimiter.setCurrentIndex(
            min(max(params.GetInt("ExportDelimiter", 0), 0), len(_DELIMITERS) - 1)
        )
        self.cb_delimiter.currentIndexChanged.connect(
            lambda i: dfm_params().SetInt("ExportDelimiter", i)
        )
        self.cb_delimiter.setMinimumWidth(170)
        row.addWidget(self.cb_delimiter)
        csv_layout.addLayout(row)
        root.addWidget(self._csv_section)

        # -- Footer --------------------------------------------------------
        footer = QtWidgets.QHBoxLayout()
        footer.setContentsMargins(0, 10, 0, 0)
        self._summary = muted_label(small=True)
        footer.addWidget(self._summary)
        footer.addStretch(1)
        self.btn_export = QtWidgets.QPushButton("Export CSV…")
        self.btn_export.setDefault(True)
        apply_svg_icon(self.btn_export, DOWNLOAD_SVG, 16)
        self.btn_export.clicked.connect(self.export_requested)
        footer.addWidget(self.btn_export)
        root.addLayout(footer)

        self._refresh_summary()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_counts(self, errors: int, warnings: int, passed: int,
                   ignored_errors: int, ignored_warnings: int) -> None:
        """Counts for the current results; shown on the chips and in the summary."""
        self._counts = {"errors": errors, "warnings": warnings, "passed": passed,
                        "ignored_errors": ignored_errors, "ignored_warnings": ignored_warnings}
        self._set_chip_count(self.chip_errors, "Errors", errors)
        self._set_chip_count(self.chip_warnings, "Warnings", warnings)
        self._set_chip_count(self.chip_passed, "Passed", passed)
        self._set_chip_count(self.chip_ignored, "Ignored", ignored_errors + ignored_warnings)
        self._refresh_summary()

    def config(self) -> CSVExportConfig:
        return CSVExportConfig(
            include_criticality=self.cb_criticality.isChecked(),
            include_feedback=self.cb_feedback.isChecked(),
            include_metadata=self.cb_metadata.isChecked(),
            include_unit=self.cb_unit.isChecked(),
            include_errors=self.chip_errors.isChecked(),
            include_warnings=self.chip_warnings.isChecked(),
            include_passed=self.chip_passed.isChecked(),
            include_ignored=self.chip_ignored.isChecked(),
            delimiter=CSVResultExporter.DELIMITER_MAP.get(self.cb_delimiter.currentIndex(), ","),
        )

    def row_count(self) -> int:
        """Rows the CSV will contain, matching CSVResultExporter's filtering."""
        c, cfg = self._counts, self.config()
        rows = 0
        if cfg.include_errors:
            rows += c["errors"] + (c["ignored_errors"] if cfg.include_ignored else 0)
        if cfg.include_warnings:
            rows += c["warnings"] + (c["ignored_warnings"] if cfg.include_ignored else 0)
        if cfg.include_passed:
            rows += c["passed"]
        return rows

    def column_count(self) -> int:
        cfg = self.config()
        return _BASE_COLUMNS + sum((cfg.include_criticality, cfg.include_unit, cfg.include_feedback))

    # ------------------------------------------------------------------

    def _chip(self, text: str, icon_path: Optional[str], key: str) -> QtWidgets.QToolButton:
        chip = QtWidgets.QToolButton()
        chip.setCheckable(True)
        chip.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        chip.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        if icon_path:
            chip.setIcon(QtGui.QIcon(icon_path))
            chip.setIconSize(QtCore.QSize(14, 14))
        chip.setStyleSheet(_CHIP_CSS)
        name, default = self._PREFS[key]
        chip.setChecked(dfm_params().GetBool(name, default))
        chip.setText(text)
        chip.toggled.connect(lambda on, n=name: self._remember(n, on))
        return chip

    def _check(self, text: str, key: str) -> QtWidgets.QCheckBox:
        cb = QtWidgets.QCheckBox(text)
        name, default = self._PREFS[key]
        cb.setChecked(dfm_params().GetBool(name, default))
        cb.toggled.connect(lambda on, n=name: self._remember(n, on))
        return cb

    def _remember(self, name: str, on: bool) -> None:
        dfm_params().SetBool(name, on)
        self._refresh_summary()

    @staticmethod
    def _set_chip_count(chip: QtWidgets.QToolButton, text: str, count: int) -> None:
        chip.setText(f"{text}  {count}")

    def _refresh_summary(self) -> None:
        rows = self.row_count()
        if rows == 0:
            self._summary.setText("Nothing to export with these options")
        else:
            self._summary.setText(
                f"{rows} row{'s' if rows != 1 else ''} · {self.column_count()} columns"
            )
        self.btn_export.setEnabled(rows > 0)


class _FlowRow(QtWidgets.QWidget):
    """Chips laid out left to right, wrapping onto new lines when narrow."""

    def __init__(self):
        super().__init__()
        self._items: list[QtWidgets.QWidget] = []
        self._spacing = 6

    def add(self, widget: QtWidgets.QWidget) -> None:
        widget.setParent(self)
        self._items.append(widget)
        self.updateGeometry()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout(width, apply=False)

    def sizeHint(self) -> QtCore.QSize:
        width = sum(w.sizeHint().width() for w in self._items) + self._spacing * len(self._items)
        return QtCore.QSize(width, self._layout(max(width, 1), apply=False))

    def minimumSizeHint(self) -> QtCore.QSize:
        widest = max((w.sizeHint().width() for w in self._items), default=0)
        return QtCore.QSize(widest, self.heightForWidth(widest))

    def resizeEvent(self, event) -> None:
        self._layout(self.width(), apply=True)
        super().resizeEvent(event)

    def _layout(self, width: int, apply: bool) -> int:
        x = y = line_h = 0
        for w in self._items:
            hint = w.sizeHint()
            if x and x + hint.width() > width:
                x = 0
                y += line_h + self._spacing
                line_h = 0
            if apply:
                w.setGeometry(x, y, hint.width(), hint.height())
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h
