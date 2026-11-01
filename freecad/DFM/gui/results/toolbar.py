# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Results toolbar — grouping, sorting, and filtering controls for the findings tree."""

from enum import Enum

from PySide6 import QtCore, QtGui, QtWidgets

from ...app.prefs import dfm_params
from ..rule_cards import (
    COLLAPSE_ALL_SVG, EXPAND_ALL_SVG, GROUP_SVG, MARKERS_SVG, SORT_SVG, apply_svg_icon,
)

# A callout card with a leader line: the info cards in the 3D view.
ANNOTATIONS_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    b'<rect x="9" y="3" width="12" height="9" rx="1.5"/><path d="M12 6.5h6M12 9h4"/>'
    b'<path d="M9 10.5L4.5 17"/><circle cx="4" cy="18.5" r="1.6"/></svg>'
)

# Icon buttons follow the theme: neutral hover and checked backgrounds, and
# monochrome icons drawn in the theme's text color (see apply_svg_icon).
_ICON_BUTTON_CSS = (
    "QToolButton { border: none; border-radius: 3px; padding: 2px; background: transparent; }"
    "QToolButton:hover { background: rgba(127, 127, 127, 45); }"
    "QToolButton:pressed, QToolButton:checked { background: rgba(127, 127, 127, 80); }"
    "QToolButton::menu-indicator { image: none; width: 0px; }"
)


class GroupMode(Enum):
    """How findings are organised in the results tree."""

    CRITICALITY_RULE = 0  # Criticality → Rule → Finding  (default)
    RULE_ONLY = 1  # Rule → Finding
    GEOMETRY = 2  # Face / Edge → Finding
    FLAT = 3  # Single flat list


class SortMode(Enum):
    """How findings are ordered within their parent group."""

    SEVERITY = 0  # Worst severity first  (default)
    GEOMETRY_INDEX = 1  # Face / Edge index ascending
    RULE_NAME = 2  # Alphabetical by rule label
    MEASURED_VALUE = 3  # Worst measured deviation first


class ResultsToolbar(QtWidgets.QWidget):
    """Compact single-row toolbar sitting above the results tree.

    Emits *settings_changed* whenever any grouping, sort, or filter control
    is toggled so the tree can be rebuilt.  *expand_all_requested* and
    *collapse_all_requested* are fired by the two utility buttons.
    """

    settings_changed = QtCore.Signal()
    markers_toggled = QtCore.Signal(bool)
    annotations_toggled = QtCore.Signal(bool)
    expand_all_requested = QtCore.Signal()
    collapse_all_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    # ── UI construction ──────────────────────────────────

    def _build_ui(self):
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(4)

        # ── Group dropdown button ──
        self.btn_group, self._group_ag = self._make_menu_button(
            svg=GROUP_SVG,
            tooltip="Grouping mode",
            items=[
                ("Criticality → Rule", GroupMode.CRITICALITY_RULE),
                ("Rule only", GroupMode.RULE_ONLY),
                ("Geometry", GroupMode.GEOMETRY),
                ("Flat", GroupMode.FLAT),
            ],
        )

        # ── Sort dropdown button ──
        self.btn_sort, self._sort_ag = self._make_menu_button(
            svg=SORT_SVG,
            tooltip="Sort order",
            items=[
                ("Severity", SortMode.SEVERITY),
                ("Face / Edge", SortMode.GEOMETRY_INDEX),
                ("Rule name", SortMode.RULE_NAME),
                ("Measured", SortMode.MEASURED_VALUE),
            ],
        )

        # ── Filter toggles ──
        self.btn_errors = self._make_toggle(
            "E", "#E24B4A", "Show / hide errors", checked=True
        )
        self.btn_warnings = self._make_toggle(
            "W", "#D4900A", "Show / hide warnings", checked=True
        )
        self.btn_ignored = self._make_toggle(
            "I", "#666666", "Show / hide ignored findings", checked=True
        )

        # ── Marker toggle ──
        _default_markers = dfm_params().GetBool("ResultsShowMarkersDefault", False)
        self.btn_markers = self._make_icon_button(MARKERS_SVG, "Show / hide viewport markers")
        self.btn_markers.setCheckable(True)
        self.btn_markers.setChecked(_default_markers)

        # ── Annotation toggle: the info cards pointing at the geometry ──
        self.btn_annotations = self._make_icon_button(
            ANNOTATIONS_SVG, "Show / hide the info card pointing at the selected finding")
        self.btn_annotations.setCheckable(True)
        self.btn_annotations.setChecked(dfm_params().GetBool("ResultsShowAnnotations", True))

        # ── Count label ──
        self.lbl_count = QtWidgets.QLabel()
        self.lbl_count.setStyleSheet("color: #888888;")
        font = self.lbl_count.font()
        font.setPointSizeF(font.pointSizeF() * 0.85)
        self.lbl_count.setFont(font)

        # ── Expand / Collapse buttons ──
        self.btn_expand = self._make_icon_button(EXPAND_ALL_SVG, "Expand all")
        self.btn_collapse = self._make_icon_button(COLLAPSE_ALL_SVG, "Collapse all")

        # ── Assemble ──
        row.addWidget(self.btn_group)
        row.addWidget(self.btn_sort)
        row.addWidget(self._make_separator())
        row.addWidget(self.btn_errors)
        row.addWidget(self.btn_warnings)
        row.addWidget(self.btn_ignored)
        row.addWidget(self._make_separator())
        row.addWidget(self.btn_markers)
        row.addWidget(self.btn_annotations)
        row.addWidget(self._make_separator())
        row.addWidget(self.lbl_count)
        row.addStretch(1)
        row.addWidget(self.btn_expand)
        row.addWidget(self.btn_collapse)

        # ── Connections ──
        self._group_ag.triggered.connect(
            lambda a: self._on_menu_changed(self.btn_group, a)
        )
        self._sort_ag.triggered.connect(
            lambda a: self._on_menu_changed(self.btn_sort, a)
        )
        self.btn_errors.toggled.connect(self.settings_changed)
        self.btn_warnings.toggled.connect(self.settings_changed)
        self.btn_ignored.toggled.connect(self.settings_changed)
        self.btn_markers.toggled.connect(self.markers_toggled)
        self.btn_annotations.toggled.connect(self._on_annotations_toggled)
        self.btn_expand.clicked.connect(self.expand_all_requested)
        self.btn_collapse.clicked.connect(self.collapse_all_requested)

    # ── Widget factories ─────────────────────────────────

    @staticmethod
    def _make_separator() -> QtWidgets.QWidget:
        """Thin vertical divider between toolbar sections."""
        sep = QtWidgets.QFrame()
        sep.setFixedSize(1, 20)
        sep.setStyleSheet("QFrame { background: rgba(127, 127, 127, 110); border: none; }")
        return sep

    @staticmethod
    def _make_icon_button(svg: bytes, tooltip: str) -> QtWidgets.QToolButton:
        """Monochrome icon button that follows the theme's text color."""
        btn = QtWidgets.QToolButton()
        btn.setFixedSize(26, 24)
        btn.setToolTip(tooltip)
        btn.setAccessibleName(tooltip)
        btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(_ICON_BUTTON_CSS)
        apply_svg_icon(btn, svg, 18)
        return btn

    def _make_menu_button(
        self,
        svg: bytes,
        tooltip: str,
        items: list[tuple[str, Enum]],
    ) -> tuple[QtWidgets.QToolButton, QtGui.QActionGroup]:
        """Icon button with a dropdown menu of mutually-exclusive options."""
        btn = self._make_icon_button(svg, f"{tooltip}: {items[0][0]}")
        btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup
        )

        menu = QtWidgets.QMenu(self)
        ag = QtGui.QActionGroup(self)
        ag.setExclusive(True)
        for i, (label, mode) in enumerate(items):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setData(mode.value)
            ag.addAction(action)
            if i == 0:
                action.setChecked(True)
        btn.setMenu(menu)
        return btn, ag

    def _make_toggle(
        self, text: str, active_color: str, tooltip: str, *, checked: bool
    ) -> QtWidgets.QToolButton:
        btn = QtWidgets.QToolButton()
        btn.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly
        )
        btn.setText(text)
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.setToolTip(tooltip)
        btn.setFixedSize(24, 24)
        self._apply_toggle_style(btn, active_color, checked)
        btn.toggled.connect(
            lambda on, b=btn, c=active_color: self._apply_toggle_style(b, c, on)
        )
        return btn

    @staticmethod
    def _apply_toggle_style(
        btn: QtWidgets.QToolButton, color: str, active: bool
    ):
        if active:
            btn.setStyleSheet(
                f"QToolButton {{ background:{color}; color:white; border:none;"
                f" border-radius:3px; font-weight:bold; font-size:11px;"
                f" padding:0px; margin:0px; }}"
            )
        else:
            btn.setStyleSheet(
                "QToolButton { background:transparent; color:#666;"
                " border:1px solid #555; border-radius:3px; font-size:11px;"
                " padding:0px; margin:0px; }"
            )

    # ── Callbacks ────────────────────────────────────────

    def _on_menu_changed(
        self, btn: QtWidgets.QToolButton, action: QtGui.QAction
    ):
        """Update the button tooltip to reflect the new selection."""
        base = btn.toolTip().split(":")[0]
        btn.setToolTip(f"{base}: {action.text()}")
        self.settings_changed.emit()

    # ── Public API ───────────────────────────────────────

    @property
    def group_mode(self) -> GroupMode:
        checked = self._group_ag.checkedAction()
        if checked is not None:
            return GroupMode(checked.data())
        return GroupMode.CRITICALITY_RULE

    @property
    def sort_mode(self) -> SortMode:
        checked = self._sort_ag.checkedAction()
        if checked is not None:
            return SortMode(checked.data())
        return SortMode.SEVERITY

    @property
    def show_errors(self) -> bool:
        return self.btn_errors.isChecked()

    @property
    def show_warnings(self) -> bool:
        return self.btn_warnings.isChecked()

    @property
    def show_ignored(self) -> bool:
        return self.btn_ignored.isChecked()

    def _on_annotations_toggled(self, on: bool) -> None:
        dfm_params().SetBool("ResultsShowAnnotations", on)  # remembered next time
        self.annotations_toggled.emit(on)

    @property
    def show_annotations(self) -> bool:
        return self.btn_annotations.isChecked()

    @property
    def show_markers(self) -> bool:
        return self.btn_markers.isChecked()

    @show_markers.setter
    def show_markers(self, value: bool):
        self.btn_markers.setChecked(value)

    def update_count(self, shown: int, total: int):
        """Update the 'X of Y' count label."""
        if shown == total:
            self.lbl_count.setText(str(total))
        else:
            self.lbl_count.setText(f"{shown} of {total}")
