# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The Export PDF report dialog: options on the left, a live preview on the right.

The options start from the preferences (``ReportSettings.load()``). Changes
apply to this export only, until "Save as defaults" writes them back.
"""

import os
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ...core.models import Severity
from ..results.tabs.common import C_ACCENT, LINE, muted_label, section_header
from ..results.tabs.export import _SEGMENT_CSS, ThemeChip, _FlowRow
from ..rule_cards import DOWNLOAD_SVG, apply_svg_icon
from . import logos
from .data import (
    ReportData,
    default_image_picks,
    finding_caption,
    image_eligible,
    image_jobs,
    rule_sections,
)
from .exporter import ask_path, estimate_bytes, export_pdf, format_bytes
from .layout import Page, ReportLayout, Typesetter, page_image, page_thumbnail
from .settings import CHOICES, ReportSettings
from .template import (
    BUILTIN_TEMPLATES,
    PAGE_SIZES,
    ReportTemplate,
    all_templates,
    get_template,
    import_template,
    save_template,
    template_folder,
)

PLACEHOLDER_HELP = (
    "You can use {title}, {project}, {revision}, {document}, {model}, {process},\n"
    "{material}, {date}, {company} and {prepared_by}. Parts separated by \" · \"\n"
    "are left out when their placeholders are empty."
)

_MAX_THUMBNAILS = 60


def _indent(widget: QtWidgets.QWidget, px: int = 24) -> QtWidgets.QWidget:
    holder = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(holder)
    row.setContentsMargins(px, 0, 0, 0)
    row.addWidget(widget)
    return holder


class _Form(QtWidgets.QWidget):
    """A scrolling column of sections with label / field rows."""

    def __init__(self):
        super().__init__()
        self.box = QtWidgets.QVBoxLayout(self)
        self.box.setContentsMargins(12, 6, 12, 12)
        self.box.setSpacing(6)
        self._grid: Optional[QtWidgets.QGridLayout] = None

    def section(self, title: str, note: str = "") -> None:
        self.box.addWidget(section_header(title, note))
        self._grid = None

    def _grid_layout(self) -> QtWidgets.QGridLayout:
        if self._grid is None:
            self._grid = QtWidgets.QGridLayout()
            self._grid.setHorizontalSpacing(10)
            self._grid.setVerticalSpacing(6)
            self._grid.setColumnStretch(1, 1)
            self.box.addLayout(self._grid)
        return self._grid

    def row(self, label: str, field: QtWidgets.QWidget | QtWidgets.QLayout,
            suffix: Optional[QtWidgets.QWidget] = None) -> QtWidgets.QLabel:
        grid = self._grid_layout()
        r = grid.rowCount()
        lbl = QtWidgets.QLabel(label)
        grid.addWidget(lbl, r, 0)
        if isinstance(field, QtWidgets.QLayout):
            grid.addLayout(field, r, 1, 1, 2 if suffix is None else 1)
        else:
            grid.addWidget(field, r, 1, 1, 2 if suffix is None else 1)
        if suffix is not None:
            grid.addWidget(suffix, r, 2)
        return lbl

    def add(self, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        self._grid = None
        self.box.addWidget(widget)
        return widget

    def finish(self) -> None:
        self.box.addStretch(1)


class _OptionCard(QtWidgets.QFrame):
    """A radio button with a title and a description, in a framed card."""

    def __init__(self, title: str, description: str, group: QtWidgets.QButtonGroup, value: str):
        super().__init__()
        self.setObjectName("optionCard")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.value = value
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 7)
        layout.setSpacing(1)
        self.radio = QtWidgets.QRadioButton(title)
        font = self.radio.font()
        font.setBold(True)
        self.radio.setFont(font)
        group.addButton(self.radio)
        layout.addWidget(self.radio)
        layout.addWidget(_indent(muted_label(description, small=True), 22))
        self.radio.toggled.connect(self._restyle)
        self._restyle()

    def mousePressEvent(self, event) -> None:
        self.radio.setChecked(True)
        super().mousePressEvent(event)

    def _restyle(self, *_):
        border = f"2px solid {C_ACCENT}" if self.radio.isChecked() else f"1px solid {LINE}"
        pad = "" if self.radio.isChecked() else "margin: 1px;"
        self.setStyleSheet(f"QFrame#optionCard {{ border: {border}; border-radius: 4px; {pad} }}")


class _LinkButton(QtWidgets.QPushButton):
    """A flat button that looks like a link, in the theme's link color."""

    def __init__(self, text: str):
        super().__init__(text)
        self.setFlat(True)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"QPushButton {{ border: none; padding: 2px 0; color: {C_ACCENT}; }}"
                           "QPushButton:hover { text-decoration: underline; }")


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


class _ThumbnailGrid(QtWidgets.QWidget):
    """Page thumbnails with captions, wrapping to the available width."""

    page_clicked = QtCore.Signal(int)

    THUMB_W = 150
    GAP = 18
    CAPTION_H = 22

    def __init__(self):
        super().__init__()
        self._items: list[tuple[QtGui.QImage, str]] = []
        self._more = 0
        self.setMouseTracking(True)
        self._hover = -1

    def set_pages(self, items: list[tuple[QtGui.QImage, str]], more: int = 0) -> None:
        self._items = items
        self._more = more
        self.updateGeometry()
        self.setMinimumHeight(self.heightForWidth(max(self.width(), self.THUMB_W + 2 * self.GAP)))
        self.update()

    def _columns(self, width: int) -> int:
        return max(1, (width - self.GAP) // (self.THUMB_W + self.GAP))

    def _cell(self) -> QtCore.QSize:
        h = self._items[0][0].height() / self._items[0][0].devicePixelRatio() if self._items else 212
        return QtCore.QSize(self.THUMB_W, int(h) + self.CAPTION_H)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        n = len(self._items) + (1 if self._more else 0)
        rows = (n + self._columns(width) - 1) // self._columns(width)
        return self.GAP + rows * (self._cell().height() + self.GAP)

    def resizeEvent(self, event) -> None:
        self.setMinimumHeight(self.heightForWidth(self.width()))
        super().resizeEvent(event)

    def _rect(self, i: int) -> QtCore.QRect:
        cols = self._columns(self.width())
        cell = self._cell()
        used = cols * self.THUMB_W + (cols - 1) * self.GAP
        x0 = max(self.GAP, (self.width() - used) // 2)
        r, c = divmod(i, cols)
        return QtCore.QRect(x0 + c * (self.THUMB_W + self.GAP), self.GAP + r * (cell.height() + self.GAP),
                            cell.width(), cell.height())

    def _hit(self, pos: QtCore.QPoint) -> int:
        for i in range(len(self._items)):
            if self._rect(i).contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, event) -> None:
        hover = self._hit(event.position().toPoint())
        if hover != self._hover:
            self._hover = hover
            self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor if hover >= 0
                           else QtCore.Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        i = self._hit(event.position().toPoint())
        if i >= 0:
            self.page_clicked.emit(i)

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        self.ensurePolished()
        text = QtGui.QColor(self.palette().color(QtGui.QPalette.ColorRole.WindowText))
        text.setAlphaF(0.7)
        shadow = QtGui.QColor(0, 0, 0, 40)
        for i, (image, caption) in enumerate(self._items):
            r = self._rect(i)
            ih = int(image.height() / image.devicePixelRatio())
            page = QtCore.QRect(r.x(), r.y(), r.width(), ih)
            p.fillRect(page.translated(1, 2), shadow)
            p.drawImage(page, image)
            if i == self._hover:
                p.setPen(QtGui.QPen(QtGui.QColor(C_ACCENT), 2))
                p.drawRect(page.adjusted(-1, -1, 0, 0))
            p.setPen(text)
            fm = p.fontMetrics()
            p.drawText(QtCore.QRect(r.x() - 8, r.y() + ih + 4, r.width() + 16, self.CAPTION_H),
                       int(QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop),
                       fm.elidedText(caption, QtCore.Qt.TextElideMode.ElideRight, r.width() + 16))
        if self._more:
            r = self._rect(len(self._items))
            p.setPen(text)
            p.drawText(r, int(QtCore.Qt.AlignmentFlag.AlignCenter), f"and {self._more} more pages")
        p.end()


class _SinglePage(QtWidgets.QWidget):
    """One page at a readable size."""

    def __init__(self):
        super().__init__()
        self._image: Optional[QtGui.QImage] = None

    def set_image(self, image: Optional[QtGui.QImage]) -> None:
        self._image = image
        if image is not None:
            self.setMinimumHeight(int(image.height() / image.devicePixelRatio()) + 32)
        self.update()

    def paintEvent(self, _event) -> None:
        if self._image is None:
            return
        p = QtGui.QPainter(self)
        w = int(self._image.width() / self._image.devicePixelRatio())
        h = int(self._image.height() / self._image.devicePixelRatio())
        r = QtCore.QRect((self.width() - w) // 2, 16, w, h)
        p.fillRect(r.translated(1, 2), QtGui.QColor(0, 0, 0, 40))
        p.drawImage(r, self._image)
        p.end()


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class ReportDialog(QtWidgets.QDialog):
    """Configure, preview and export a PDF report."""

    def __init__(self, data: ReportData, settings: Optional[ReportSettings] = None,
                 capture_factory: Optional[Callable[[ReportSettings], object]] = None,
                 parent: Optional[QtWidgets.QWidget] = None,
                 template: Optional[ReportTemplate] = None,
                 picks: Optional[set[int]] = None):
        super().__init__(parent)
        self.setWindowTitle("Export PDF report")
        self.data = data
        self.settings = (settings or ReportSettings.load()).copy()
        self.template = (template or get_template(self.settings.template)).copy()
        self.picks: Optional[set[int]] = set(picks) if picks is not None else None
        self._capture_factory = capture_factory
        self._bindings: dict[str, Callable[[], None]] = {}
        self._template_bindings: dict[str, Callable[[], None]] = {}
        self._pages: list[Page] = []
        self._ts: Optional[Typesetter] = None
        self._single_index = 0
        self._syncing = False

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._rebuild)

        self._build()
        self._sync_all()
        self.resize(1080, 700)
        self._rebuild()

    # -- Structure -------------------------------------------------------

    def _build(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        # Options.
        left = QtWidgets.QWidget()
        left_box = QtWidgets.QVBoxLayout(left)
        left_box.setContentsMargins(8, 8, 4, 6)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._scroll(self._content_tab()), "Content")
        self.tabs.addTab(self._scroll(self._images_tab()), "Viewport images")
        self.tabs.addTab(self._scroll(self._layout_tab()), "Layout")
        left_box.addWidget(self.tabs, 1)
        links = QtWidgets.QHBoxLayout()
        links.setContentsMargins(4, 4, 0, 0)
        self.btn_save_defaults = _LinkButton("Save as defaults")
        self.btn_save_defaults.setToolTip("Use these options for every new report")
        self.btn_save_defaults.clicked.connect(self._save_defaults)
        self.btn_reset_defaults = _LinkButton("Reset to defaults")
        self.btn_reset_defaults.setToolTip("Go back to the options in the preferences")
        self.btn_reset_defaults.clicked.connect(self._reset_defaults)
        links.addWidget(self.btn_save_defaults)
        links.addWidget(muted_label("·"))
        links.addWidget(self.btn_reset_defaults)
        links.addStretch(1)
        self._saved_note = muted_label("", small=True)
        links.addWidget(self._saved_note)
        left_box.addLayout(links)
        left.setMinimumWidth(400)
        splitter.addWidget(left)

        # Preview.
        right = QtWidgets.QWidget()
        right_box = QtWidgets.QVBoxLayout(right)
        right_box.setContentsMargins(4, 8, 8, 6)
        head = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Preview")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        head.addWidget(title)
        self._page_count = muted_label("")
        head.addWidget(self._page_count)
        head.addStretch(1)
        self.btn_prev = QtWidgets.QToolButton()
        self.btn_prev.setText("‹")
        self.btn_prev.setToolTip("Previous page")
        self.btn_prev.clicked.connect(lambda: self._show_single(self._single_index - 1))
        self.btn_next = QtWidgets.QToolButton()
        self.btn_next.setText("›")
        self.btn_next.setToolTip("Next page")
        self.btn_next.clicked.connect(lambda: self._show_single(self._single_index + 1))
        self._single_label = muted_label("")
        for w in (self.btn_prev, self._single_label, self.btn_next):
            head.addWidget(w)
        head.addSpacing(8)
        self.btn_pages = QtWidgets.QPushButton("Pages")
        self.btn_pages.setObjectName("first")
        self.btn_single = QtWidgets.QPushButton("Single page")
        self.btn_single.setObjectName("last")
        mode = QtWidgets.QButtonGroup(self)
        for b in (self.btn_pages, self.btn_single):
            b.setCheckable(True)
            b.setStyleSheet(_SEGMENT_CSS)
            mode.addButton(b)
        seg = QtWidgets.QHBoxLayout()
        seg.setSpacing(0)
        seg.addWidget(self.btn_pages)
        seg.addWidget(self.btn_single)
        head.addLayout(seg)
        right_box.addLayout(head)

        self._stack = QtWidgets.QStackedWidget()
        self._grid = _ThumbnailGrid()
        self._grid.page_clicked.connect(self._open_single)
        self._single = _SinglePage()
        for w in (self._grid, self._single):
            area = QtWidgets.QScrollArea()
            area.setWidgetResizable(True)
            area.setWidget(w)
            area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            area.setObjectName("previewArea")
            area.viewport().setObjectName("previewViewport")
            area.setStyleSheet("QWidget#previewCanvas { background: rgba(127, 127, 127, 38); }"
                               "QScrollArea#previewArea { border: none; }")
            w.setObjectName("previewCanvas")
            w.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            self._stack.addWidget(area)
        self._single_area = self._stack.widget(1)
        self._single_area.viewport().installEventFilter(self)
        right_box.addWidget(self._stack, 1)
        self.btn_pages.setChecked(True)
        self.btn_pages.toggled.connect(self._mode_changed)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([440, 640])

        # Buttons.
        bar = QtWidgets.QFrame()
        bar.setObjectName("buttonBar")
        bar_box = QtWidgets.QHBoxLayout(bar)
        bar_box.setContentsMargins(12, 8, 12, 10)
        self._note = muted_label("", small=True)
        bar_box.addWidget(self._note, 1)
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_export = QtWidgets.QPushButton("Export PDF…")
        self.btn_export.setDefault(True)
        apply_svg_icon(self.btn_export, DOWNLOAD_SVG, 16)
        self.btn_export.clicked.connect(self._export)
        bar_box.addWidget(self.btn_cancel)
        bar_box.addWidget(self.btn_export)
        root.addWidget(bar)
        self._mode_changed()

    @staticmethod
    def _scroll(widget: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(widget)
        return area

    # -- Bindings --------------------------------------------------------

    def _bind_line(self, name: str, placeholder: str = "", target: str = "settings") -> QtWidgets.QLineEdit:
        edit = QtWidgets.QLineEdit()
        edit.setPlaceholderText(placeholder)
        obj = lambda: getattr(self, target)  # noqa: E731
        edit.textChanged.connect(lambda v: self._set(obj(), name, v))
        self._register(target, name, lambda: self._quiet(edit.setText, getattr(obj(), name)))
        return edit

    def _bind_check(self, name: str, text: str, target: str = "settings") -> QtWidgets.QCheckBox:
        box = QtWidgets.QCheckBox(text)
        obj = lambda: getattr(self, target)  # noqa: E731
        box.toggled.connect(lambda v: self._set(obj(), name, v))
        self._register(target, name, lambda: self._quiet(box.setChecked, bool(getattr(obj(), name))))
        return box

    def _bind_combo(self, name: str, choices: Optional[list[tuple[str, str]]] = None,
                    target: str = "settings") -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        for value, label in choices or CHOICES[name]:
            combo.addItem(label, value)
        obj = lambda: getattr(self, target)  # noqa: E731
        combo.currentIndexChanged.connect(lambda _i: self._set(obj(), name, combo.currentData()))

        def sync():
            i = combo.findData(getattr(obj(), name))
            self._quiet(combo.setCurrentIndex, max(i, 0))

        self._register(target, name, sync)
        return combo

    def _bind_spin(self, name: str, lo: int, hi: int, target: str = "settings") -> QtWidgets.QSpinBox:
        spin = QtWidgets.QSpinBox()
        spin.setRange(lo, hi)
        obj = lambda: getattr(self, target)  # noqa: E731
        spin.valueChanged.connect(lambda v: self._set(obj(), name, v))
        self._register(target, name, lambda: self._quiet(spin.setValue, int(getattr(obj(), name))))
        return spin

    def _register(self, target: str, name: str, sync: Callable[[], None]) -> None:
        table = self._bindings if target == "settings" else self._template_bindings
        previous = table.get(name)
        table[name] = (lambda: (previous(), sync())) if previous else sync

    def _quiet(self, setter, value) -> None:
        self._syncing = True
        try:
            setter(value)
        finally:
            self._syncing = False

    def _set(self, obj, name: str, value) -> None:
        if self._syncing or getattr(obj, name) == value:
            return
        setattr(obj, name, value)
        if obj is self.template:
            self._template_edited()
        self._changed(name)

    def _sync_all(self) -> None:
        for sync in list(self._bindings.values()) + list(self._template_bindings.values()):
            sync()
        self._refresh_template_combo()
        self._refresh_dependents()

    # -- Content tab -----------------------------------------------------

    def _content_tab(self) -> QtWidgets.QWidget:
        f = _Form()
        f.section("Cover page")
        self.ed_title = self._bind_line("title", "DFM report: {model}")
        self.ed_title.setToolTip(PLACEHOLDER_HELP)
        f.row("Title", self.ed_title)
        f.row("Project", self._bind_line("project"))
        f.row("Prepared by", self._bind_line("prepared_by"))
        rev = self._bind_line("revision")
        rev.setMaximumWidth(90)
        rev_row = QtWidgets.QHBoxLayout()
        rev_row.addWidget(rev)
        rev_row.addStretch(1)
        f.row("Revision", rev_row)
        f.add(self._bind_check("overview_image", "Overview image with all findings highlighted"))
        self.cb_overview_view = self._bind_combo("overview_view")
        view_row = QtWidgets.QWidget()
        vr = QtWidgets.QHBoxLayout(view_row)
        vr.setContentsMargins(24, 0, 0, 0)
        vr.addWidget(QtWidgets.QLabel("View"))
        vr.addStretch(1)
        vr.addWidget(self.cb_overview_view)
        self.cb_overview_view.setMinimumWidth(180)
        self._overview_row = f.add(view_row)
        design = self._bind_check("design_image", "Also show the model as designed, next to it")
        design.setToolTip("A second image with the model's own colors and transparency,\n"
                          "so readers can see what the part is before the findings")
        self._design_row = f.add(_indent(design))
        f.add(self._bind_check("summary_table", "Summary table by criticality"))
        f.add(self._bind_check("show_notes", "Notes"))
        self.ed_notes = QtWidgets.QPlainTextEdit()
        self.ed_notes.setPlaceholderText("Shown on the cover page")
        self.ed_notes.setFixedHeight(64)
        self.ed_notes.textChanged.connect(lambda: self._set(self.settings, "notes", self.ed_notes.toPlainText()))
        self._register("settings", "notes", lambda: self._quiet(self.ed_notes.setPlainText, self.settings.notes))
        self._notes_row = f.add(_indent(self.ed_notes))

        f.section("Findings")
        chips = _FlowRow()
        self.chip_errors = self._chip("include_errors", "Errors", ":/icons/dfm_error.svg")
        self.chip_warnings = self._chip("include_warnings", "Warnings", ":/icons/dfm_warning.svg")
        self.chip_passed = self._chip("include_passed", "Passed rules", ":/icons/dfm_success.svg")
        self.chip_ignored = self._chip("include_ignored", "Ignored", None)
        for chip in (self.chip_errors, self.chip_warnings, self.chip_passed, self.chip_ignored):
            chips.add(chip)
        f.add(chips)
        counts = self.data.counts()
        self.chip_errors.setText(f"Errors  {counts['errors']}")
        self.chip_warnings.setText(f"Warnings  {counts['warnings']}")
        self.chip_passed.setText(f"Passed rules  {counts['passed']}")
        self.chip_ignored.setText(f"Ignored  {counts['ignored']}")
        f.row("Group by", self._bind_combo("group_by"))
        diag = self._bind_check("rule_diagrams", "Rule diagrams")
        diag.setToolTip("The bad / good diagram that explains each rule, once per rule")
        f.add(diag)
        f.add(self._bind_check("feedback_messages", "Feedback messages"))
        f.add(self._bind_check("target_and_limit", "Target and limit for each rule"))
        f.add(self._bind_check("settings_appendix", "Process and material settings appendix"))

        f.section("Large results")
        at_most = QtWidgets.QHBoxLayout()
        self.sp_list = self._bind_spin("list_at_most", 0, 1000)
        self.sp_list.setToolTip("0 lists no findings in the rule sections")
        at_most.addWidget(self.sp_list)
        at_most.addWidget(QtWidgets.QLabel("findings per rule"))
        at_most.addStretch(1)
        f.row("List at most", at_most)
        note = muted_label("The rest are summarized as “and 62 more”, with the full list in an appendix.",
                           small=True)
        note.setWordWrap(True)
        f.add(note)
        appendix = self._bind_check("findings_appendix", "Full findings table as appendix")
        appendix.setToolTip("Added when a rule has more findings than are listed in its section")
        f.add(appendix)
        f.finish()
        return f

    def _chip(self, name: str, text: str, icon: Optional[str]) -> QtWidgets.QToolButton:
        chip = ThemeChip(text, icon)
        chip.toggled.connect(lambda v: self._set(self.settings, name, v))
        self._register("settings", name, lambda: self._quiet(chip.setChecked, getattr(self.settings, name)))
        return chip

    # -- Viewport images tab ---------------------------------------------

    def _images_tab(self) -> QtWidgets.QWidget:
        f = _Form()
        f.section("Images")
        self._mode_group = QtWidgets.QButtonGroup(self)
        self._mode_cards = []
        descriptions = {
            "none": "Smallest file. Diagrams and tables only.",
            "per_rule": "Each rule gets one image with all its findings highlighted.",
            "per_finding": "A close-up for every selected finding. Best for a few findings.",
        }
        for value, label in CHOICES["image_mode"]:
            card = _OptionCard(label, descriptions[value], self._mode_group, value)
            card.radio.toggled.connect(lambda on, v=value: on and self._set(self.settings, "image_mode", v))
            self._mode_cards.append(card)
            f.add(card)
        self._register("settings", "image_mode", lambda: [
            self._quiet(c.radio.setChecked, c.value == self.settings.image_mode) for c in self._mode_cards])

        f.section("Which findings get an image")
        self.cb_image_findings = self._bind_combo("image_findings")
        f.row("Include", self.cb_image_findings)
        per_rule = QtWidgets.QHBoxLayout()
        self.sp_images = self._bind_spin("images_per_rule", 1, 200)
        per_rule.addWidget(self.sp_images)
        per_rule.addWidget(QtWidgets.QLabel("images per rule"))
        per_rule.addStretch(1)
        self._at_most_label = f.row("At most", per_rule)
        self.cb_choose = QtWidgets.QCheckBox("Choose findings individually")
        self.cb_choose.toggled.connect(self._choose_toggled)
        f.add(self.cb_choose)
        self.picker = QtWidgets.QTreeWidget()
        self.picker.setHeaderHidden(True)
        self.picker.setRootIsDecorated(True)
        self.picker.setMinimumHeight(170)
        self.picker.itemChanged.connect(self._pick_changed)
        f.add(self.picker)
        tip = muted_label("Tip: findings you ignore in the results panel can be left out on the Content tab.",
                          small=True)
        tip.setWordWrap(True)
        self._picker_tip = f.add(tip)

        f.section("Camera")
        self.cb_framing = self._bind_combo("framing")
        f.row("Framing", self.cb_framing)
        self.cb_direction = self._bind_combo("view_direction")
        f.row("View direction", self.cb_direction)
        f.add(self._bind_check("fade_rest", "Show the rest of the model faded"))
        see = QtWidgets.QHBoxLayout()
        self.sp_see_through = self._bind_spin("see_through", 0, 90)
        self.sp_see_through.setSuffix(" %")
        self.sp_see_through.setSingleStep(10)
        see.addWidget(self.sp_see_through)
        see.addStretch(1)
        see_label = f.row("See-through part", see)
        tip = ("Transparency for images that show several findings (the cover\n"
               "overview and one image per rule), so findings on the far side of\n"
               "the part still show. Close-ups of one finding stay solid.")
        see_label.setToolTip(tip)
        self.sp_see_through.setToolTip(tip)
        f.add(self._bind_check("hide_markers", "Hide viewport markers and annotations"))

        f.section("Quality")
        f.row("Resolution", self._bind_combo("resolution"))
        f.row("Background", self._bind_combo("background"))
        self._estimate = muted_label("", small=True)
        self._estimate.setWordWrap(True)
        f.add(self._estimate)
        f.finish()
        return f

    # -- Layout tab ------------------------------------------------------

    def _layout_tab(self) -> QtWidgets.QWidget:
        f = _Form()
        f.section("Template")
        row = QtWidgets.QHBoxLayout()
        self.cb_template = QtWidgets.QComboBox()
        self.cb_template.setMinimumWidth(150)
        self.cb_template.currentIndexChanged.connect(self._template_chosen)
        row.addWidget(self.cb_template, 1)
        self.btn_import = QtWidgets.QPushButton("Import…")
        self.btn_import.setToolTip("Add a template file to your template folder")
        self.btn_import.clicked.connect(self._import_template)
        self.btn_save_template = QtWidgets.QPushButton("Save as…")
        self.btn_save_template.setToolTip("Save the page and style options below as a template")
        self.btn_save_template.clicked.connect(self._save_template)
        row.addWidget(self.btn_import)
        row.addWidget(self.btn_save_template)
        f.row("Template", row)
        self._template_note = muted_label("", small=True)
        self._template_note.setWordWrap(True)
        f.add(self._template_note)

        f.section("Page")
        self.cb_size = self._bind_combo("page_size", [(k, k) for k in PAGE_SIZES], target="template")
        f.row("Size", self.cb_size)
        orient = QtWidgets.QHBoxLayout()
        orient.setSpacing(0)
        self.btn_portrait = QtWidgets.QPushButton("Portrait")
        self.btn_portrait.setObjectName("first")
        self.btn_landscape = QtWidgets.QPushButton("Landscape")
        self.btn_landscape.setObjectName("last")
        group = QtWidgets.QButtonGroup(self)
        for b, value in ((self.btn_portrait, "portrait"), (self.btn_landscape, "landscape")):
            b.setCheckable(True)
            b.setStyleSheet(_SEGMENT_CSS)
            group.addButton(b)
            orient.addWidget(b)
            b.toggled.connect(lambda on, v=value: on and self._set(self.template, "orientation", v))
        orient.addStretch(1)
        self._register("template", "orientation", lambda: (
            self._quiet(self.btn_portrait.setChecked, self.template.orientation == "portrait"),
            self._quiet(self.btn_landscape.setChecked, self.template.orientation == "landscape")))
        f.row("Orientation", orient)
        per_row = QtWidgets.QHBoxLayout()
        per_row.addWidget(self._bind_spin("images_per_row", 1, 4, target="template"))
        per_row.addStretch(1)
        f.row("Images per row", per_row)
        self.btn_accent = QtWidgets.QPushButton()
        self.btn_accent.clicked.connect(self._choose_accent)
        self._register("template", "accent", self._sync_accent)
        accent_row = QtWidgets.QHBoxLayout()
        accent_row.addWidget(self.btn_accent)
        accent_row.addStretch(1)
        f.row("Accent color", accent_row)
        brk = self._bind_check("rule_page_break", "Start each rule on a new page", target="template")
        f.add(brk)
        header = self._bind_line("header_text", target="template")
        header.setToolTip(PLACEHOLDER_HELP)
        f.row("Header text", header)
        footer = self._bind_line("footer_text", target="template")
        footer.setToolTip(PLACEHOLDER_HELP)
        f.row("Footer text", footer)

        f.section("Branding")
        f.row("Company", self._bind_line("company_name", "Shown in the header when there is no logo"))
        logo_row = QtWidgets.QHBoxLayout()
        self.ed_logo = self._bind_line("company_logo", "SVG, PNG or JPEG")
        logo_row.addWidget(self.ed_logo, 1)
        choose = QtWidgets.QPushButton("Choose…")
        choose.clicked.connect(self._choose_logo)
        logo_row.addWidget(choose)
        f.row("Logo", logo_row)
        self._logo_note = muted_label("", small=True)
        f.add(self._logo_note)
        f.add(QtWidgets.QLabel("Show logos"))
        self.cb_company_logo = self._bind_check("show_company_logo", "Company logo, in the header")
        self.cb_dfm_logo = self._bind_check("show_dfm_logo", "DFM workbench logo, in the footer")
        self.cb_freecad_logo = self._bind_check("show_freecad_logo", "FreeCAD logo, in the footer")
        if logos.freecad_logo() is None:
            self.cb_freecad_logo.setEnabled(False)
            self.cb_freecad_logo.setToolTip("The FreeCAD logo wasn't found in this installation.")
        for cb in (self.cb_company_logo, self.cb_dfm_logo, self.cb_freecad_logo):
            f.add(_indent(cb, 12))

        f.section("File")
        name = self._bind_line("file_name", "{document}_{material}_DFM_{date}")
        name.setToolTip("The suggested file name. " + PLACEHOLDER_HELP)
        f.row("File name", name)
        f.add(self._bind_check("open_after", "Open the PDF after exporting"))
        f.finish()
        return f

    # -- Reactions -------------------------------------------------------

    def _changed(self, name: str) -> None:
        if name in ("include_errors", "include_warnings", "include_ignored", "image_findings",
                    "images_per_rule", "group_by"):
            if not self.cb_choose.isChecked():
                self.picks = None
            self._fill_picker()
        self._saved_note.setText("")
        self._refresh_dependents()
        self._timer.start()

    def _refresh_dependents(self) -> None:
        s = self.settings
        self._overview_row.setEnabled(s.overview_image)
        self._design_row.setEnabled(s.overview_image)
        self._notes_row.setVisible(s.show_notes)
        per_finding = s.image_mode == "per_finding"
        images = s.image_mode != "none"
        for w in (self.cb_image_findings, self.cb_framing, self.cb_direction):
            w.setEnabled(images)
        self.sp_images.setEnabled(per_finding and not self.cb_choose.isChecked())
        self._at_most_label.setEnabled(self.sp_images.isEnabled())
        self.cb_choose.setVisible(per_finding)
        self.picker.setVisible(per_finding)
        self._picker_tip.setVisible(per_finding)
        self.picker.setEnabled(self.cb_choose.isChecked())
        if self.picker.topLevelItemCount() == 0:
            self._fill_picker()
        path = os.path.expanduser(s.company_logo)
        if s.company_logo and logos.company_logo(s.company_logo) is None:
            self._logo_note.setText("This file can't be read as an image." if os.path.exists(path)
                                    else "This file doesn't exist.")
            self._logo_note.show()
        else:
            self._logo_note.hide()

    def _template_edited(self) -> None:
        self._refresh_template_note()

    # -- Finding picker --------------------------------------------------

    def _current_picks(self) -> set[int]:
        return self.picks if self.picks is not None else default_image_picks(self.data, self.settings)

    def _fill_picker(self) -> None:
        picks = self._current_picks()
        self.picker.blockSignals(True)
        self.picker.clear()
        bold = self.picker.font()
        bold.setBold(True)
        for section in rule_sections(self.data, self.settings):
            eligible = [f for f in section.findings if image_eligible(f, self.settings)]
            if not eligible:
                continue
            parent = QtWidgets.QTreeWidgetItem([f"{section.rule.label}  {len(eligible)}"])
            parent.setFont(0, bold)
            parent.setFlags(parent.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                            | QtCore.Qt.ItemFlag.ItemIsAutoTristate)
            self.picker.addTopLevelItem(parent)
            for finding in eligible:
                child = QtWidgets.QTreeWidgetItem([finding_caption(finding)])
                child.setData(0, QtCore.Qt.ItemDataRole.UserRole, id(finding))
                child.setFlags(child.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, QtCore.Qt.CheckState.Checked if id(finding) in picks
                                    else QtCore.Qt.CheckState.Unchecked)
                if finding.severity == Severity.ERROR:
                    child.setIcon(0, QtGui.QIcon(":/icons/dfm_error.svg"))
                elif finding.severity == Severity.WARNING:
                    child.setIcon(0, QtGui.QIcon(":/icons/dfm_warning.svg"))
                parent.addChild(child)
            parent.setExpanded(len(eligible) <= 12)
        self.picker.blockSignals(False)

    def _choose_toggled(self, on: bool) -> None:
        if on:
            self.picks = set(self._current_picks())
        else:
            self.picks = None
            self._fill_picker()
        self._refresh_dependents()
        self._timer.start()

    def _pick_changed(self, item: QtWidgets.QTreeWidgetItem, _column: int) -> None:
        if self.picks is None:
            return
        key = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if key is None:
            return
        if item.checkState(0) == QtCore.Qt.CheckState.Checked:
            self.picks.add(key)
        else:
            self.picks.discard(key)
        self._timer.start()

    # -- Templates -------------------------------------------------------

    def _refresh_template_combo(self) -> None:
        names = list(all_templates())
        current = self.template.name
        if current not in names:
            names.append(current)
        self.cb_template.blockSignals(True)
        self.cb_template.clear()
        for name in names:
            self.cb_template.addItem(name, name)
        self.cb_template.setCurrentIndex(max(self.cb_template.findData(current), 0))
        self.cb_template.blockSignals(False)
        self._refresh_template_note()

    def _refresh_template_note(self) -> None:
        saved = all_templates().get(self.template.name)
        if saved is not None and not saved.same_look(self.template):
            self._template_note.setText(
                f"Changed from the {self.template.name} template, for this report only. "
                "Use Save as… to keep the changes as a template.")
        else:
            self._template_note.setText(
                "Built in: " + ", ".join(BUILTIN_TEMPLATES) + ". Templates are small YAML files "
                f"you can share with your team, kept in {template_folder()}.")

    def _template_chosen(self, _index: int) -> None:
        name = self.cb_template.currentData()
        if not name:
            return
        self.template = get_template(name)
        self.settings.template = name
        for sync in self._template_bindings.values():
            sync()
        self._refresh_template_note()
        self._timer.start()

    def _import_template(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import report template", "", "Report templates (*.yaml *.yml)")
        if not path:
            return
        try:
            template = import_template(path)
        except (ValueError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, "Import report template", str(exc))
            return
        self.template = template.copy()
        self.settings.template = template.name
        self._sync_all()
        self._timer.start()

    def _save_template(self) -> None:
        suggestion = self.template.name if not self.template.builtin else f"{self.template.name} copy"
        name, ok = QtWidgets.QInputDialog.getText(self, "Save report template", "Template name",
                                                  text=suggestion)
        name = name.strip()
        if not ok or not name:
            return
        if name in BUILTIN_TEMPLATES:
            QtWidgets.QMessageBox.information(
                self, "Save report template", f"{name} is a built-in template. Choose another name.")
            return
        try:
            saved = save_template(self.template, name)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Save report template", f"The template could not be saved.\n{exc}")
            return
        self.template = saved.copy()
        self.settings.template = saved.name
        self._refresh_template_combo()

    def _sync_accent(self) -> None:
        color = QtGui.QColor(self.template.accent)
        pix = QtGui.QPixmap(14, 14)
        pix.fill(color if color.isValid() else QtGui.QColor("#1B2A4A"))
        self.btn_accent.setIcon(QtGui.QIcon(pix))
        self.btn_accent.setText(self.template.accent.upper())

    def _choose_accent(self) -> None:
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.template.accent), self, "Accent color")
        if color.isValid():
            self._set(self.template, "accent", color.name())
            self._sync_accent()

    def _choose_logo(self) -> None:
        start = os.path.dirname(os.path.expanduser(self.settings.company_logo)) if self.settings.company_logo else ""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose a company logo", start, "Images (*.svg *.png *.jpg *.jpeg)")
        if path:
            self.ed_logo.setText(path)

    # -- Defaults --------------------------------------------------------

    def _save_defaults(self) -> None:
        self.settings.template = self.template.name
        self.settings.save()
        self._saved_note.setText("Saved")

    def _reset_defaults(self) -> None:
        keep = {k: getattr(self.settings, k) for k in ("project", "revision", "notes")}
        self.settings = ReportSettings.load().copy(**keep)
        self.template = get_template(self.settings.template)
        self.picks = None
        self.cb_choose.setChecked(False)
        self._sync_all()
        self._fill_picker()
        self._saved_note.setText("")
        self._timer.start()

    # -- Preview ---------------------------------------------------------

    def _rebuild(self) -> None:
        picks = self.picks if self.settings.image_mode == "per_finding" else None
        layout = ReportLayout(self.data, self.settings, self.template, picks)
        self._pages = layout.build()
        self._ts = layout.ts
        jobs = image_jobs(self.data, self.settings, picks)
        n = len(self._pages)
        self._page_count.setText(f"{n} page{'s' if n != 1 else ''}")

        size = format_bytes(estimate_bytes(self.settings, layout, jobs, n))
        if jobs:
            seconds = "a few seconds" if len(jobs) <= 12 else (
                "under a minute" if len(jobs) <= 60 else "a few minutes")
            images = f"Capturing {len(jobs)} image{'s' if len(jobs) != 1 else ''} takes {seconds}."
        else:
            images = "No viewport images to capture."
        self._estimate.setText(f"≈ {n} pages, about {size}. {images}")
        self._note.setText("Images are captured from the 3D view when you export." if jobs
                           else f"About {size}.")
        self.btn_export.setEnabled(n > 0)
        self._render_preview()

    def _render_preview(self) -> None:
        if not self._pages or self._ts is None:
            return
        ratio = self.devicePixelRatioF()
        if self.btn_pages.isChecked():
            shown = self._pages[:_MAX_THUMBNAILS]
            items = [(self._thumbnail(p, ratio), p.label) for p in shown]
            self._grid.set_pages(items, len(self._pages) - len(shown))
        else:
            self._show_single(self._single_index)

    def _thumbnail(self, page: Page, ratio: float) -> QtGui.QImage:
        """A small page image, drawn large and scaled down so text keeps its proportions."""
        return page_thumbnail(page, self.template, self._ts, _ThumbnailGrid.THUMB_W, ratio)

    def _mode_changed(self, *_):
        single = self.btn_single.isChecked()
        self._stack.setCurrentIndex(1 if single else 0)
        for w in (self.btn_prev, self.btn_next, self._single_label):
            w.setVisible(single)
        self._render_preview()

    def _open_single(self, index: int) -> None:
        self._single_index = index
        self.btn_single.setChecked(True)

    def _show_single(self, index: int) -> None:
        if not self._pages or self._ts is None:
            return
        index = min(max(index, 0), len(self._pages) - 1)
        self._single_index = index
        width = max(220, min(self._single_area.viewport().width() - 40, 820))
        image = page_image(self._pages[index], self.template, self._ts, width, {}, self.devicePixelRatioF())
        self._single.set_image(image)
        self._single_label.setText(f"{index + 1} / {len(self._pages)}")
        self.btn_prev.setEnabled(index > 0)
        self.btn_next.setEnabled(index < len(self._pages) - 1)

    def eventFilter(self, obj, event) -> bool:
        if (event.type() == QtCore.QEvent.Type.Resize and self.btn_single.isChecked()
                and obj is self._single_area.viewport()):
            QtCore.QTimer.singleShot(0, lambda: self._show_single(self._single_index))
        return super().eventFilter(obj, event)

    # -- Export ----------------------------------------------------------

    def _export(self) -> None:
        self.settings.template = self.template.name
        path = ask_path(self, self.data, self.settings)
        if not path:
            return
        capture = self._capture_factory(self.settings) if self._capture_factory else None
        picks = self.picks if self.settings.image_mode == "per_finding" else None
        if export_pdf(self, path, self.data, self.settings, self.template, capture, picks):
            self.accept()
