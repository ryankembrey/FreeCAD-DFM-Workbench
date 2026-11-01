# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Export tab: a PDF report from a preset, or the findings as CSV.

The tab's main action is the PDF report. Three presets (Summary, Standard,
Detailed) choose how many viewport images it gets; each shows a thumbnail
and its page count, and the choice is remembered. "Customize…" opens the
Export PDF report dialog for everything else.

CSV is a secondary action: "Export CSV…" opens a small dialog with the CSV
options, which are remembered too.

The tab doesn't build reports itself. It asks for previews with
``preview_needed`` and the results presenter answers with ``set_presets``.
"""

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ....app.prefs import dfm_params
from ...rule_cards import DOWNLOAD_SVG, apply_svg_icon
from ..utils import CSVExportConfig, CSVResultExporter
from .common import C_ACCENT, LINE, TINT, MutedLabel, muted_label, section_header

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



def chip_css(text_color: str) -> str:
    """Chip style with the text in *text_color* in every state.

    Themes often give checked tool buttons white text for a filled
    background; chips are only outlined, so they keep the plain text color.
    """
    return _CHIP_CSS + (
        f"QToolButton, QToolButton:checked, QToolButton:hover, QToolButton:pressed "
        f"{{ color: {text_color}; }}"
    )


class ThemeChip(QtWidgets.QToolButton):
    """A checkable, outlined chip whose text uses the theme's ordinary text color."""

    def __init__(self, text: str = "", icon_path: Optional[str] = None,
                 parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._color = ""
        self.setCheckable(True)
        self.setText(text)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        if icon_path:
            self.setIcon(QtGui.QIcon(icon_path))
            self.setIconSize(QtCore.QSize(14, 14))
        self.setStyleSheet(_CHIP_CSS)

    def _apply_color(self) -> None:
        color = _text_color(self.parentWidget() or self).name()
        if color != self._color:
            self._color = color
            self.setStyleSheet(chip_css(color))

    def showEvent(self, event) -> None:
        self._apply_color()
        super().showEvent(event)

    def changeEvent(self, event) -> None:
        # The theme changed: read the text color again once it has settled.
        if event.type() in (QtCore.QEvent.Type.StyleChange, QtCore.QEvent.Type.PaletteChange) \
                and self.isVisible():
            QtCore.QTimer.singleShot(0, self._apply_color)
        super().changeEvent(event)


SLIDERS_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    b'<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/>'
    b'<circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>'
    b'</svg>'
)

TABLE_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    b'<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 10h16M4 15h16M10 4v16"/>'
    b'</svg>'
)

# Report presets: (value of the image_mode setting, title, description).
PRESETS = (
    ("none", "Summary", "Cover, tables and diagrams. No viewport images."),
    ("per_rule", "Standard", "One image per rule with all its findings."),
    ("per_finding", "Detailed", "A close-up of every finding."),
)

# CSV options remembered in the preferences: key -> (entry, default).
_CSV_PREFS = {
    "errors": ("ExportIncludeErrors", True),
    "warnings": ("ExportIncludeWarnings", True),
    "passed": ("ExportIncludePassed", True),
    "ignored": ("ExportIncludeIgnored", False),
    "criticality": ("ExportColumnCriticality", True),
    "unit": ("ExportColumnUnit", True),
    "feedback": ("ExportColumnFeedback", False),
    "metadata": ("ExportColumnMetadata", True),
}


def _pref(key: str) -> bool:
    name, default = _CSV_PREFS[key]
    return dfm_params().GetBool(name, default)


def _text_color(host: QtWidgets.QWidget) -> QtGui.QColor:
    """The color a plain label gets inside *host*, which follows the theme's stylesheet."""
    probe = QtWidgets.QLabel(host)
    probe.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen)
    probe.hide()
    probe.ensurePolished()
    color = QtGui.QColor(probe.palette().color(QtGui.QPalette.ColorRole.WindowText))
    probe.setParent(None)
    probe.deleteLater()
    return color


def csv_config() -> CSVExportConfig:
    """The remembered CSV options."""
    index = min(max(dfm_params().GetInt("ExportDelimiter", 0), 0), len(_DELIMITERS) - 1)
    return CSVExportConfig(
        include_criticality=_pref("criticality"),
        include_feedback=_pref("feedback"),
        include_metadata=_pref("metadata"),
        include_unit=_pref("unit"),
        include_errors=_pref("errors"),
        include_warnings=_pref("warnings"),
        include_passed=_pref("passed"),
        include_ignored=_pref("ignored"),
        delimiter=CSVResultExporter.DELIMITER_MAP.get(index, ","),
    )


# ---------------------------------------------------------------------------
# Export tab
# ---------------------------------------------------------------------------


class ExportTab(QtWidgets.QWidget):
    """The Export tab's contents."""

    export_requested = QtCore.Signal()  # CSV, after its dialog is accepted
    pdf_export_requested = QtCore.Signal()
    pdf_configure_requested = QtCore.Signal()
    pdf_preset_changed = QtCore.Signal(str)  # the image_mode value
    preview_needed = QtCore.Signal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._counts = {"errors": 0, "warnings": 0, "passed": 0,
                        "ignored_errors": 0, "ignored_warnings": 0}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        root.addWidget(section_header("PDF report"))
        tiles = QtWidgets.QHBoxLayout()
        tiles.setSpacing(6)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self.tiles: dict[str, _PresetTile] = {}
        for value, title, description in PRESETS:
            tile = _PresetTile(title, description)
            self._group.addButton(tile)
            tile.clicked.connect(lambda _=False, v=value: self._preset_clicked(v))
            tiles.addWidget(tile, 1)
            self.tiles[value] = tile
        root.addLayout(tiles)

        self._note = _NoteBox()
        root.addWidget(self._note)

        actions = QtWidgets.QHBoxLayout()
        actions.setContentsMargins(0, 4, 0, 0)
        self.btn_customize = QtWidgets.QPushButton("Customize…")
        self.btn_customize.setToolTip("Choose everything that goes in the report, with a page preview")
        apply_svg_icon(self.btn_customize, SLIDERS_SVG, 16)
        self.btn_customize.clicked.connect(self.pdf_configure_requested)
        self.btn_export = QtWidgets.QPushButton("Export PDF…")
        self.btn_export.setDefault(True)
        apply_svg_icon(self.btn_export, DOWNLOAD_SVG, 16)
        self.btn_export.clicked.connect(self.pdf_export_requested)
        actions.addWidget(self.btn_customize)
        actions.addStretch(1)
        actions.addWidget(self.btn_export)
        root.addLayout(actions)

        rule = QtWidgets.QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"QFrame {{ background: {LINE}; border: none; }}")
        root.addSpacing(10)
        root.addWidget(rule)
        csv_row = QtWidgets.QHBoxLayout()
        csv_row.setContentsMargins(0, 4, 0, 0)
        csv_row.addWidget(muted_label("Need the raw data?"))
        csv_row.addStretch(1)
        self.btn_csv = QtWidgets.QPushButton("Export CSV…")
        self.btn_csv.setToolTip("Save the findings as a spreadsheet, one row per finding")
        apply_svg_icon(self.btn_csv, TABLE_SVG, 16)
        self.btn_csv.clicked.connect(self._open_csv_dialog)
        csv_row.addWidget(self.btn_csv)
        root.addLayout(csv_row)

        self.set_preset(dfm_params().GetString("ReportImageMode", "per_rule"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def preset(self) -> str:
        """The chosen preset, as an ``image_mode`` value."""
        for value, tile in self.tiles.items():
            if tile.isChecked():
                return value
        return "per_rule"

    def set_preset(self, image_mode: str) -> None:
        tile = self.tiles.get(image_mode) or self.tiles["per_rule"]
        tile.setChecked(True)

    def set_presets(self, previews: dict[str, tuple[Optional[QtGui.QImage], int]], note: str = "") -> None:
        """Thumbnail and page count for each preset, and the line under the presets."""
        for value, tile in self.tiles.items():
            image, pages = previews.get(value, (None, 0))
            tile.set_preview(image, pages)
        self._note.setText(note)
        self._even_tiles()

    def set_counts(self, errors: int, warnings: int, passed: int,
                   ignored_errors: int, ignored_warnings: int) -> None:
        """Counts for the current results, shown in the CSV dialog."""
        self._counts = {"errors": errors, "warnings": warnings, "passed": passed,
                        "ignored_errors": ignored_errors, "ignored_warnings": ignored_warnings}

    def config(self) -> CSVExportConfig:
        """The CSV options, as last chosen in the CSV dialog."""
        return csv_config()

    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.preview_needed.emit()
        self._even_tiles()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._even_tiles()

    def _even_tiles(self) -> None:
        """Give the preset tiles one height, that of the tallest."""
        tallest = max(t.heightForWidth(max(t.width(), t.minimumSizeHint().width()))
                      for t in self.tiles.values())
        for tile in self.tiles.values():
            if tile.height() != tallest or tile.minimumHeight() != tallest:
                tile.setFixedHeight(tallest)

    def _preset_clicked(self, value: str) -> None:
        dfm_params().SetString("ReportImageMode", value)
        self.pdf_preset_changed.emit(value)

    def _open_csv_dialog(self) -> None:
        dialog = CSVExportDialog(self._counts, self.window())
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.export_requested.emit()


class _NoteBox(QtWidgets.QFrame):
    """Secondary text on a light tint."""

    def __init__(self):
        super().__init__()
        self.setObjectName("exportNote")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QFrame#exportNote {{ background: {TINT}; border-radius: 3px; }}"
                           "QFrame#exportNote QLabel { background: transparent; }")
        box = QtWidgets.QVBoxLayout(self)
        box.setContentsMargins(8, 6, 8, 6)
        self._label = MutedLabel()
        self._label.setWordWrap(True)
        font = self._label.font()
        font.setPointSizeF(font.pointSizeF() * 0.92)
        self._label.setFont(font)
        box.addWidget(self._label)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self._label.setText(text)
        self.setVisible(bool(text))


class _PresetTile(QtWidgets.QAbstractButton):
    """A checkable card: page thumbnail, title, description and page count."""

    THUMB_W = 58
    PAD = 8

    def __init__(self, title: str, description: str):
        super().__init__()
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.setToolTip(description)
        self._title = title
        self._description = description
        self._image: Optional[QtGui.QImage] = None
        self._pages = 0
        self._hover = False
        self._text: Optional[QtGui.QColor] = None

    def set_preview(self, image: Optional[QtGui.QImage], pages: int) -> None:
        self._image = image
        self._pages = pages
        self.updateGeometry()
        self.update()

    def _thumb_height(self) -> int:
        if self._image is not None and not self._image.isNull():
            return round(self._image.height() / self._image.devicePixelRatio())
        return round(self.THUMB_W * 1.414)

    def _fonts(self) -> tuple[QtGui.QFont, QtGui.QFont]:
        bold = QtGui.QFont(self.font())
        bold.setBold(True)
        small = QtGui.QFont(self.font())
        small.setPointSizeF(small.pointSizeF() * 0.88)
        return bold, small

    def _text_height(self, width: int) -> int:
        bold, small = self._fonts()
        flags = int(QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.TextFlag.TextWordWrap)
        desc = QtGui.QFontMetrics(small).boundingRect(QtCore.QRect(0, 0, width, 1000), flags,
                                                       self._description).height()
        return QtGui.QFontMetrics(bold).height() + 3 + desc + 4 + QtGui.QFontMetrics(small).height()

    def sizeHint(self) -> QtCore.QSize:
        width = 120
        return QtCore.QSize(width, self.PAD * 2 + self._thumb_height() + 8
                            + self._text_height(width - 2 * self.PAD))

    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(self.THUMB_W + 2 * self.PAD, self.sizeHint().height())

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self.PAD * 2 + self._thumb_height() + 8 + self._text_height(width - 2 * self.PAD)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def changeEvent(self, event) -> None:
        if event.type() in (QtCore.QEvent.Type.StyleChange, QtCore.QEvent.Type.PaletteChange):
            self._text = None
        super().changeEvent(event)

    def paintEvent(self, _event) -> None:
        if self._text is None:
            self._text = _text_color(self.parentWidget() or self)
        text = QtGui.QColor(self._text)
        muted = QtGui.QColor(text)
        muted.setAlphaF(0.68)

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        fill = QtGui.QColor(127, 127, 127, 40 if self._hover else 22)
        if self.isChecked():
            pen = QtGui.QPen(QtGui.QColor(C_ACCENT), 2)
        else:
            pen = QtGui.QPen(QtGui.QColor(127, 127, 127, 110), 1)
        p.setPen(pen)
        p.setBrush(fill)
        p.drawRoundedRect(r, 4, 4)

        # Thumbnail, centred, with a soft shadow.
        th = self._thumb_height()
        tx = (self.width() - self.THUMB_W) // 2
        ty = self.PAD
        thumb = QtCore.QRect(tx, ty, self.THUMB_W, th)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(0, 0, 0, 45))
        p.drawRect(thumb.translated(1, 2))
        if self._image is not None and not self._image.isNull():
            p.drawImage(thumb, self._image)
        else:
            p.setBrush(QtGui.QColor("#ffffff"))
            p.drawRect(thumb)

        bold, small = self._fonts()
        y = ty + th + 8
        inner = QtCore.QRect(self.PAD, y, self.width() - 2 * self.PAD, 1000)
        center = int(QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop)
        p.setFont(bold)
        p.setPen(text)
        p.drawText(inner, center, self._title)
        y += QtGui.QFontMetrics(bold).height() + 3
        p.setFont(small)
        p.setPen(muted)
        wrap = center | int(QtCore.Qt.TextFlag.TextWordWrap)
        desc = p.boundingRect(QtCore.QRect(self.PAD, y, inner.width(), 1000), wrap, self._description)
        p.drawText(QtCore.QRect(self.PAD, y, inner.width(), desc.height()), wrap, self._description)
        # The page count sits at the bottom, so it lines up across the tiles.
        p.setPen(text)
        pages = f"{self._pages} page{'s' if self._pages != 1 else ''}" if self._pages else "…"
        line = QtGui.QFontMetrics(small).height()
        p.drawText(QtCore.QRect(self.PAD, self.height() - self.PAD - line, inner.width(), line),
                   center, pages)
        p.end()


# ---------------------------------------------------------------------------
# CSV dialog
# ---------------------------------------------------------------------------


class CSVExportDialog(QtWidgets.QDialog):
    """Rows, columns and delimiter for a CSV export. Choices are remembered."""

    def __init__(self, counts: dict[str, int], parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Export CSV")
        self._counts = counts
        params = dfm_params()

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 12)
        root.setSpacing(6)

        root.addWidget(section_header("Rows"))
        chips = _FlowRow()
        ignored = counts["ignored_errors"] + counts["ignored_warnings"]
        self.chip_errors = self._chip(f"Errors  {counts['errors']}", ":/icons/dfm_error.svg", "errors")
        self.chip_warnings = self._chip(f"Warnings  {counts['warnings']}", ":/icons/dfm_warning.svg", "warnings")
        self.chip_passed = self._chip(f"Passed  {counts['passed']}", ":/icons/dfm_success.svg", "passed")
        self.chip_ignored = self._chip(f"Ignored  {ignored}", None, "ignored")
        for chip in (self.chip_errors, self.chip_warnings, self.chip_passed, self.chip_ignored):
            chips.add(chip)
        root.addWidget(chips)

        root.addWidget(section_header("Columns"))
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        self.cb_criticality = self._check("Criticality", "criticality")
        self.cb_unit = self._check("Unit", "unit")
        self.cb_feedback = self._check("Feedback message", "feedback")
        self.cb_metadata = self._check("Model and material header", "metadata")
        self.cb_metadata.setToolTip("Adds the model, process, material and verdict above the table")
        grid.addWidget(self.cb_criticality, 0, 0)
        grid.addWidget(self.cb_unit, 0, 1)
        grid.addWidget(self.cb_feedback, 1, 0)
        grid.addWidget(self.cb_metadata, 1, 1)
        root.addLayout(grid)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        row.addWidget(QtWidgets.QLabel("Delimiter"))
        row.addStretch(1)
        self.cb_delimiter = QtWidgets.QComboBox()
        self.cb_delimiter.addItems(_DELIMITERS)
        self.cb_delimiter.setCurrentIndex(
            min(max(params.GetInt("ExportDelimiter", 0), 0), len(_DELIMITERS) - 1))
        self.cb_delimiter.setMinimumWidth(170)
        row.addWidget(self.cb_delimiter)
        root.addLayout(row)

        root.addStretch(1)
        footer = QtWidgets.QHBoxLayout()
        footer.setContentsMargins(0, 12, 0, 0)
        self._summary = muted_label(small=True)
        footer.addWidget(self._summary)
        footer.addStretch(1)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.btn_save = QtWidgets.QPushButton("Save…")
        self.btn_save.setDefault(True)
        apply_svg_icon(self.btn_save, DOWNLOAD_SVG, 16)
        self.btn_save.clicked.connect(self._save)
        footer.addWidget(cancel)
        footer.addWidget(self.btn_save)
        root.addLayout(footer)

        for w in (self.chip_errors, self.chip_warnings, self.chip_passed, self.chip_ignored,
                  self.cb_criticality, self.cb_unit, self.cb_feedback):
            w.toggled.connect(self._refresh)
        self._refresh()
        self.setMinimumWidth(380)
        self.resize(400, root.totalHeightForWidth(400) if root.hasHeightForWidth()
                    else root.sizeHint().height())

    def _chip(self, text: str, icon_path: Optional[str], key: str) -> QtWidgets.QToolButton:
        chip = ThemeChip(text, icon_path)
        chip.setChecked(_pref(key))
        return chip

    def _check(self, text: str, key: str) -> QtWidgets.QCheckBox:
        cb = QtWidgets.QCheckBox(text)
        cb.setChecked(_pref(key))
        return cb

    def _choices(self) -> dict[str, bool]:
        return {
            "errors": self.chip_errors.isChecked(), "warnings": self.chip_warnings.isChecked(),
            "passed": self.chip_passed.isChecked(), "ignored": self.chip_ignored.isChecked(),
            "criticality": self.cb_criticality.isChecked(), "unit": self.cb_unit.isChecked(),
            "feedback": self.cb_feedback.isChecked(), "metadata": self.cb_metadata.isChecked(),
        }

    def row_count(self) -> int:
        """Rows the CSV will contain, matching CSVResultExporter's filtering."""
        c, on = self._counts, self._choices()
        rows = 0
        if on["errors"]:
            rows += c["errors"] + (c["ignored_errors"] if on["ignored"] else 0)
        if on["warnings"]:
            rows += c["warnings"] + (c["ignored_warnings"] if on["ignored"] else 0)
        if on["passed"]:
            rows += c["passed"]
        return rows

    def column_count(self) -> int:
        on = self._choices()
        return _BASE_COLUMNS + sum((on["criticality"], on["unit"], on["feedback"]))

    def _refresh(self, *_) -> None:
        rows = self.row_count()
        if rows == 0:
            self._summary.setText("Nothing to export with these options")
        else:
            self._summary.setText(f"{rows} row{'s' if rows != 1 else ''} · {self.column_count()} columns")
        self.btn_save.setEnabled(rows > 0)

    def _save(self) -> None:
        params = dfm_params()
        for key, value in self._choices().items():
            params.SetBool(_CSV_PREFS[key][0], value)
        params.SetInt("ExportDelimiter", self.cb_delimiter.currentIndex())
        self.accept()


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
