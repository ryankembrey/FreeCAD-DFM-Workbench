# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Results toolbar — grouping, sorting, and filtering controls for the findings tree."""

from enum import Enum

from PySide6 import QtCore, QtGui, QtWidgets

from ...app.prefs import dfm_params


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
    expand_all_requested = QtCore.Signal()
    collapse_all_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Docked toolbar look: subtle background with bottom border.
        self.setStyleSheet(
            "ResultsToolbar {"
            "  background: #2b2b2b;"
            "  border-bottom: 1px solid #555555;"
            "}"
        )
        self._build_ui()

    # ── UI construction ──────────────────────────────────

    def _build_ui(self):
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(4)

        # ── Group dropdown button ──
        self.btn_group, self._group_ag = self._make_menu_button(
            icon=self._paint_group_icon(),
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
            icon=self._paint_sort_icon(),
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
        self.btn_markers = self._make_toggle(
            "●",  # ● filled circle
            "#378ADD",
            "Show / hide viewport markers",
            checked=_default_markers,
        )

        # ── Count label ──
        self.lbl_count = QtWidgets.QLabel()
        self.lbl_count.setStyleSheet("color: #888888;")
        font = self.lbl_count.font()
        font.setPointSizeF(font.pointSizeF() * 0.85)
        self.lbl_count.setFont(font)

        # ── Expand / Collapse buttons ──
        self.btn_expand = self._make_action_button(
            expand=True, tooltip="Expand all"
        )
        self.btn_collapse = self._make_action_button(
            expand=False, tooltip="Collapse all"
        )

        # ── Assemble ──
        row.addWidget(self.btn_group)
        row.addWidget(self.btn_sort)
        row.addWidget(self._make_separator())
        row.addWidget(self.btn_errors)
        row.addWidget(self.btn_warnings)
        row.addWidget(self.btn_ignored)
        row.addWidget(self._make_separator())
        row.addWidget(self.btn_markers)
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
        self.btn_expand.clicked.connect(self.expand_all_requested)
        self.btn_collapse.clicked.connect(self.collapse_all_requested)

    # ── Widget factories ─────────────────────────────────

    @staticmethod
    def _make_separator() -> QtWidgets.QWidget:
        """Thin vertical divider between toolbar sections."""
        sep = QtWidgets.QWidget()
        sep.setFixedSize(1, 20)
        sep.setStyleSheet("background: #555555;")
        return sep

    def _make_menu_button(
        self,
        icon: QtGui.QIcon,
        tooltip: str,
        items: list[tuple[str, Enum]],
    ) -> tuple[QtWidgets.QToolButton, QtGui.QActionGroup]:
        """Icon button with a dropdown menu of mutually-exclusive options."""
        btn = QtWidgets.QToolButton()
        btn.setIcon(icon)
        btn.setIconSize(QtCore.QSize(18, 18))
        btn.setToolTip(f"{tooltip}: {items[0][0]}")
        btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup
        )
        btn.setFixedHeight(24)
        btn.setStyleSheet(
            "QToolButton { border:1px solid #555; border-radius:3px;"
            " padding:1px 2px; }"
            "QToolButton::menu-indicator {"
            "  image: none; width:8px; subcontrol-position:right center; }"
            "QToolButton:pressed { background:rgba(128,128,128,0.25); }"
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

    @staticmethod
    def _make_action_button(
        *, expand: bool, tooltip: str
    ) -> QtWidgets.QToolButton:
        btn = QtWidgets.QToolButton()
        btn.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly
        )
        btn.setText("+" if expand else "−")  # + or −
        btn.setToolTip(tooltip)
        btn.setFixedSize(24, 24)
        btn.setStyleSheet(
            "QToolButton { border:1px solid #555; border-radius:3px;"
            " color:#cccccc; font-size:16px; font-weight:bold;"
            " padding:0px; margin:0px; }"
            "QToolButton:pressed { background:rgba(128,128,128,0.25); }"
        )
        return btn

    # ── Icon painters ────────────────────────────────────

    @staticmethod
    def _paint_group_icon() -> QtGui.QIcon:
        """Tree hierarchy icon for the grouping dropdown."""
        pm = QtGui.QPixmap(16, 16)
        pm.fill(QtCore.Qt.GlobalColor.transparent)

        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        color = QtGui.QColor("#cccccc")

        # Parent node (small rounded rect, top-left).
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QtCore.QRectF(0.5, 0.5, 5, 3.5), 1, 1)

        # Vertical trunk line.
        pen = QtGui.QPen(color, 1.6)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QtCore.QPointF(3, 4), QtCore.QPointF(3, 13))

        # Horizontal branches.
        p.drawLine(QtCore.QPointF(3, 6.5), QtCore.QPointF(6.5, 6.5))
        p.drawLine(QtCore.QPointF(3, 11.5), QtCore.QPointF(6.5, 11.5))

        # Child nodes (two rounded rects on the right).
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawRoundedRect(QtCore.QRectF(6.5, 4.5, 9, 4), 1.5, 1.5)
        p.drawRoundedRect(QtCore.QRectF(6.5, 9.5, 9, 4), 1.5, 1.5)

        p.end()
        return QtGui.QIcon(pm)

    @staticmethod
    def _paint_sort_icon() -> QtGui.QIcon:
        """Sliders / equaliser icon for the sorting dropdown."""
        pm = QtGui.QPixmap(16, 16)
        pm.fill(QtCore.Qt.GlobalColor.transparent)

        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        color = QtGui.QColor("#cccccc")
        pen = QtGui.QPen(color, 1.6)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen)

        # Two horizontal rails.
        p.drawLine(QtCore.QPointF(1.5, 5.5), QtCore.QPointF(14.5, 5.5))
        p.drawLine(QtCore.QPointF(1.5, 10.5), QtCore.QPointF(14.5, 10.5))

        # Filled knobs at different positions.
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QtCore.QPointF(10, 5.5), 2.6, 2.6)
        p.drawEllipse(QtCore.QPointF(6, 10.5), 2.6, 2.6)

        p.end()
        return QtGui.QIcon(pm)

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
