# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtSvg import QSvgRenderer

from ..core.processes.process import ProcessArchetype, RuleLimit
from ..core.rules import RuleDependency, Rulebook, RuleShape


OVERRIDE_COLOR = "#e8890c"

CARD_STYLE = f"""
QFrame#RuleCard {{
    border: 1px solid rgba(127, 127, 127, 90);
    border-radius: 6px;
}}
QFrame#RuleCard:hover {{
    background-color: rgba(127, 127, 127, 30);
}}

QFrame#RuleCard .QWidget {{
    background-color: transparent;
}}

QFrame#RuleCard QLabel,
QFrame#RuleCard QLabel:enabled,
QFrame#RuleCard QLabel:disabled {{
    background-color: transparent;
    border: none;
}}

/* Overrides are marked by the border only; the text keeps the theme
   color so values stay readable. */
QComboBox[override="true"] {{
    border: 1px solid {OVERRIDE_COLOR};
}}

QLabel[fieldLabel="true"] {{
    font-size: 10px;
    letter-spacing: 0.5px;
}}
QLabel[unitSuffix="true"] {{
    font-size: 11px;
    font-style: italic;
}}

QWidget#UnitInput {{
    border: 0.5px solid palette(mid);
    border-radius: 3px;
}}
QWidget#UnitInput[override="true"] {{
    border: 1px solid {OVERRIDE_COLOR};
}}

QWidget#UnitInput QLineEdit,
QWidget#UnitInput QLineEdit:enabled,
QWidget#UnitInput QLineEdit:focus,
QWidget#UnitInput QLineEdit:hover {{
    background-color: transparent;
    border: none;
    padding: 2px 4px;
}}

QLabel[unitBadge="true"] {{
    font-size: 10px;
    font-style: italic;
}}
QWidget#UnitInput QLabel[unitBadge="true"],
QWidget#UnitInput QLabel[unitBadge="true"]:enabled {{
    background-color: transparent;
    border: none;
}}
"""


# -- Scope badge styles (M / P / S) ------------------------------------------

BADGE_STYLES = {
    "M": {
        "tooltip": "Material: threshold varies per material",
        "style": (
            "color: #c89b2b; background: rgba(200,155,43,0.12);"
            " border: 1px solid rgba(200,155,43,0.35);"
        ),
    },
    "P": {
        "tooltip": "Process: same threshold for all materials",
        "style": (
            "color: #5b9bd5; background: rgba(91,155,213,0.12);"
            " border: 1px solid rgba(91,155,213,0.35);"
        ),
    },
    "S": {
        "tooltip": "Setup: derived from analysis task configuration",
        "style": (
            "color: #8a8a8a; background: rgba(138,138,138,0.12);"
            " border: 1px solid rgba(138,138,138,0.35);"
        ),
    },
}


# =============================================================================


def _resolve_badge(
    rule: Rulebook,
    archetype: Optional[ProcessArchetype],
) -> str:
    """Determine the M/P/S badge letter for a rule card."""
    if archetype is not None and rule in archetype.derived_rules:
        return "S"
    if not rule.per_material:
        return "P"
    return "M"


def _make_scope_badge(badge_type: str) -> QtWidgets.QLabel:
    """Create a small coloured scope badge (M, P or S)."""
    info = BADGE_STYLES.get(badge_type, BADGE_STYLES["M"])
    lbl = QtWidgets.QLabel(badge_type)
    lbl.setFixedSize(18, 18)
    lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(f"font-size: 11px; font-weight: bold; border-radius: 3px; {info['style']}")
    lbl.setToolTip(info["tooltip"])
    return lbl


# -- Feedback icon (SVG that follows the palette text colour) ----------------

_FEEDBACK_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    b'<path d="M4,4 C2.9,4 2,4.9 2,6 V16 C2,17.1 2.9,18 4,18 H10 L12,22'
    b' L14,18 H20 C21.1,18 22,17.1 22,16 V6 C22,4.9 21.1,4 20,4 Z"'
    b' fill="none" stroke="COLOR" stroke-width="2" stroke-linejoin="round"/>'
    b'<rect x="5.5" y="6" width="5" height="2" fill="COLOR"/>'
    b'<rect x="5.5" y="10" width="5" height="2" fill="COLOR"/>'
    b'<rect x="5.5" y="14" width="6.5" height="2" fill="COLOR"/>'
    b'<polygon points="13,15 14,11 18,7 21,10 17,14"'
    b' fill="none" stroke="COLOR" stroke-width="2" stroke-linejoin="round"/>'
    b'</svg>'
)

_RESET_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    b'<polyline points="1 4 1 10 7 10"'
    b' fill="none" stroke="COLOR" stroke-width="2"'
    b' stroke-linecap="round" stroke-linejoin="round"/>'
    b'<path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"'
    b' fill="none" stroke="COLOR" stroke-width="2"'
    b' stroke-linecap="round" stroke-linejoin="round"/>'
    b'</svg>'
)

# Same arrow drawn bolder, for the small reset button inside overridden
# fields; at 14 px the regular stroke is too thin to read.
_FIELD_RESET_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-1 -1 26 26">'
    b'<polyline points="1 4 1 10 7 10"'
    b' fill="none" stroke="COLOR" stroke-width="3.5"'
    b' stroke-linecap="round" stroke-linejoin="round"/>'
    b'<path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"'
    b' fill="none" stroke="COLOR" stroke-width="3.5"'
    b' stroke-linecap="round" stroke-linejoin="round"/>'
    b'</svg>'
)


# -- Toolbar icons (same stroke style) ----------------------------------------

ADD_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    b'<line x1="12" y1="5" x2="12" y2="19"'
    b' stroke="COLOR" stroke-width="2" stroke-linecap="round"/>'
    b'<line x1="5" y1="12" x2="19" y2="12"'
    b' stroke="COLOR" stroke-width="2" stroke-linecap="round"/>'
    b'</svg>'
)

REMOVE_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    b'<line x1="5" y1="12" x2="19" y2="12"'
    b' stroke="COLOR" stroke-width="2" stroke-linecap="round"/>'
    b'</svg>'
)

IMPORT_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    b'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"'
    b' fill="none" stroke="COLOR" stroke-width="2"'
    b' stroke-linecap="round" stroke-linejoin="round"/>'
    b'<polyline points="14 2 14 8 20 8"'
    b' fill="none" stroke="COLOR" stroke-width="2"'
    b' stroke-linecap="round" stroke-linejoin="round"/>'
    b'<line x1="12" y1="18" x2="12" y2="12"'
    b' stroke="COLOR" stroke-width="2" stroke-linecap="round"/>'
    b'<polyline points="9 15 12 18 15 15"'
    b' fill="none" stroke="COLOR" stroke-width="2"'
    b' stroke-linecap="round" stroke-linejoin="round"/>'
    b'</svg>'
)


# -- Monochrome, theme-aware icons -------------------------------------------
#
# Icons are drawn in the theme's text color, like FreeCAD's own toolbar icons,
# so they read in light and dark themes alike. The state is shown by opacity:
# full on hover, slightly softened at rest, and faded when disabled.

_ICON_OPACITY = {
    QtGui.QIcon.Mode.Normal: 0.85,
    QtGui.QIcon.Mode.Active: 1.0,     # hover on auto-raise tool buttons
    QtGui.QIcon.Mode.Selected: 1.0,
    QtGui.QIcon.Mode.Disabled: 0.28,
}


def render_svg_icon(
    svg_template: bytes,
    color: QtGui.QColor,
    size: int = 20,
    device_ratio: float = 1.0,
    opacities: Optional[dict] = None,
) -> QtGui.QIcon:
    """Render an SVG template (with ``COLOR`` placeholders) into a QIcon.

    The icon holds a pixmap for each mode (normal, hover, selected and
    disabled), drawn in *color* at the opacity for that mode and at
    *device_ratio* so it stays sharp on high-DPI screens.
    """
    data = svg_template.replace(b"COLOR", color.name().encode())
    renderer = QSvgRenderer(QtCore.QByteArray(data))
    icon = QtGui.QIcon()
    for mode, opacity in (opacities or _ICON_OPACITY).items():
        px = QtGui.QPixmap(round(size * device_ratio), round(size * device_ratio))
        px.setDevicePixelRatio(device_ratio)
        px.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(px)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setOpacity(opacity)
        renderer.render(painter, QtCore.QRectF(0, 0, size, size))
        painter.end()
        icon.addPixmap(px, mode)
    return icon


class _ThemedSvgIcon(QtCore.QObject):
    """Keeps a button's SVG icon in the button's current text color.

    FreeCAD themes are stylesheets, which only reach a widget's palette once
    it is polished, so the color is read when the button is shown and again
    whenever its style or palette changes.
    """

    def __init__(
        self,
        button: QtWidgets.QAbstractButton,
        svg_template: bytes,
        size: int,
        color: Optional[QtGui.QColor] = None,
        opacities: Optional[dict] = None,
    ):
        super().__init__(button)
        self._opacities = opacities
        self._button = button
        self._svg = svg_template
        self._size = size
        self._color = color
        self._key: Optional[tuple] = None
        button.installEventFilter(self)
        self.refresh()

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() in (
            QtCore.QEvent.Type.Show,
            QtCore.QEvent.Type.StyleChange,
            QtCore.QEvent.Type.PaletteChange,
            QtCore.QEvent.Type.ScreenChangeInternal,
        ):
            self.refresh()
        return False

    def refresh(self) -> None:
        btn = self._button
        color = self._color or self._text_color()
        ratio = btn.devicePixelRatioF()
        key = (color.rgb(), ratio)
        if key == self._key:
            return
        self._key = key
        btn.setIcon(render_svg_icon(self._svg, color, self._size, ratio, self._opacities))

    def _text_color(self) -> QtGui.QColor:
        """Return the color the theme uses for ordinary text next to the button.

        A button's own ``ButtonText`` color is not reliable: themes often set
        it for filled push buttons (for example white text on a colored
        button), which would make icons on a light background white. Instead,
        read the color a plain label gets in the same place, which is the
        text color the user sees around the icon.
        """
        host = self._button.parentWidget() or self._button
        # Parented so it inherits the same stylesheets, but never shown: this
        # can run while the host is being shown, which would otherwise show
        # the probe on top of the icons.
        probe = QtWidgets.QLabel(host)
        probe.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen)
        probe.hide()
        probe.ensurePolished()
        color = probe.palette().color(QtGui.QPalette.ColorRole.WindowText)
        probe.setParent(None)
        probe.deleteLater()
        return color


def apply_svg_icon(
    button: QtWidgets.QAbstractButton,
    svg_template: bytes,
    size: int = 20,
    color: Optional[QtGui.QColor] = None,
    opacities: Optional[dict] = None,
) -> None:
    """Give *button* a monochrome SVG icon.

    The icon follows the theme's text color, unless a fixed *color* is given.
    """
    button.setIconSize(QtCore.QSize(size, size))
    _ThemedSvgIcon(button, svg_template, size, color, opacities)


class _FieldResetButton(QtWidgets.QAbstractButton):
    """Small orange reset button shown next to a value that overrides Default.

    It paints only its icon, never a background, so theme styles for tool
    buttons cannot give it a panel that differs from the field behind it.
    The icon is brighter on hover and faded when disabled.

    It keeps its space while hidden, so fields line up whether or not they
    are overridden.
    """

    _SIZE = 16
    _ICON = 14

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(self._SIZE, self._SIZE)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Reset to the Default material's value")
        self.setAccessibleName("Reset to the Default material's value")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover)
        # Full strength at rest: the softened default would wash out the orange.
        full = {**_ICON_OPACITY, QtGui.QIcon.Mode.Normal: 1.0}
        apply_svg_icon(self, _FIELD_RESET_SVG, self._ICON, QtGui.QColor(OVERRIDE_COLOR), full)
        policy = self.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.setSizePolicy(policy)
        self.hide()

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(self._SIZE, self._SIZE)

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        if not self.isEnabled():
            mode = QtGui.QIcon.Mode.Disabled
        elif self.underMouse() or self.isDown():
            mode = QtGui.QIcon.Mode.Active
        else:
            mode = QtGui.QIcon.Mode.Normal
        painter = QtGui.QPainter(self)
        offset = (self._SIZE - self._ICON) / 2
        self.icon().paint(
            painter,
            QtCore.QRect(round(offset), round(offset), self._ICON, self._ICON),
            QtCore.Qt.AlignmentFlag.AlignCenter,
            mode,
        )
        painter.end()


class _SvgToolButton(QtWidgets.QToolButton):
    """Auto-raise tool button with a monochrome, theme-aware SVG icon."""

    def __init__(
        self,
        svg_template: bytes,
        tooltip: str,
        parent: Optional[QtWidgets.QWidget] = None,
    ):
        super().__init__(parent)
        self.setAutoRaise(True)
        self.setFixedSize(30, 30)
        self.setToolTip(tooltip)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        apply_svg_icon(self, svg_template)


# =============================================================================


class _NumericLineEdit(QtWidgets.QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._original_text = ""

    def focusInEvent(self, event: QtGui.QFocusEvent) -> None:
        self._original_text = self.text()
        super().focusInEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            super().keyPressEvent(event)
            self.clearFocus()
            event.accept()
            return
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.setText(self._original_text)
            self.clearFocus()
            event.accept()
            return
        super().keyPressEvent(event)


# =============================================================================


class UnitInput(QtWidgets.QWidget):
    """A numeric input paired with a right-aligned unit label, styled as one field."""

    editing_finished = QtCore.Signal()
    reset_clicked = QtCore.Signal()

    def __init__(self, unit: str, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setObjectName("UnitInput")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 3, 2)
        layout.setSpacing(6)

        self._edit = _NumericLineEdit()
        self._edit.setValidator(
            QtGui.QRegularExpressionValidator(
                QtCore.QRegularExpression(r"^-?\d*\.?\d*$"), self._edit
            )
        )
        self._edit.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        self._edit.editingFinished.connect(self.editing_finished.emit)
        layout.addWidget(self._edit, stretch=1)

        if unit:
            unit_lbl = QtWidgets.QLabel(unit)
            unit_lbl.setProperty("unitBadge", True)
            unit_lbl.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(unit_lbl)

        # Shown only while the value overrides the Default material.
        self._reset_btn = _FieldResetButton(self)
        self._reset_btn.clicked.connect(self.reset_clicked.emit)
        layout.addWidget(self._reset_btn)

    def text(self) -> str:
        return self._edit.text()

    def set_text(self, value: str) -> None:
        self._edit.setText(value)

    def line_edit(self) -> QtWidgets.QLineEdit:
        return self._edit

    def set_override(self, is_override: bool) -> None:
        self._reset_btn.setVisible(is_override)
        self.setProperty("override", "true" if is_override else "false")
        self.style().unpolish(self)
        self.style().polish(self)

        for child in self.findChildren(QtWidgets.QWidget):
            child.style().unpolish(child)
            child.style().polish(child)

    def is_override(self) -> bool:
        return self.property("override") == "true"


# =============================================================================


class BaseRuleCard(QtWidgets.QFrame):
    value_changed = QtCore.Signal(str, str)
    field_reset_requested = QtCore.Signal(str)  # field attribute, e.g. "limit"
    active_toggled = QtCore.Signal(bool)
    feedback_requested = QtCore.Signal()
    dep_link_clicked = QtCore.Signal(object)  # emits the target Rulebook member

    def __init__(
        self,
        rule: Rulebook,
        material_limit: Optional[RuleLimit],
        default_limit: Optional[RuleLimit],
        is_default_material: bool,
        is_active: bool,
        badge_type: str = "M",
        parent: Optional[QtWidgets.QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("RuleCard")
        self.rule = rule
        self._material_limit = material_limit
        self._default_limit = default_limit
        self._is_default = is_default_material
        self._is_active = is_active
        self._badge_type = badge_type
        self._loading = False
        self._opacity_effect: Optional[QtWidgets.QGraphicsOpacityEffect] = None

        # Dependency display state
        self._dep_info: Optional[RuleDependency] = rule.dependency
        self._dep_value: str = ""

        self._desc_lbl: Optional[QtWidgets.QLabel] = None

        self._build_layout()
        self._build_fields()
        self._load_values()
        self._refresh_active_style()

    def refresh(
        self,
        material_limit: Optional[RuleLimit],
        default_limit: Optional[RuleLimit],
        is_default_material: bool,
        is_active: bool,
    ) -> None:
        """Update the card's state without rebuilding widgets."""
        self._material_limit = material_limit
        self._default_limit = default_limit
        self._is_default = is_default_material
        self._is_active = is_active

        self._loading = True
        self._check.setChecked(is_active)
        self._loading = False

        self._load_values()
        self._refresh_active_style()

    def _build_layout(self) -> None:
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(12, 8, 10, 8)
        outer.setSpacing(12)

        self._check = QtWidgets.QCheckBox()
        self._check.setChecked(self._is_active)
        self._check.toggled.connect(self._on_check_toggled)
        outer.addWidget(self._check)

        name_col = QtWidgets.QVBoxLayout()
        name_col.setSpacing(2)

        # Title row: scope badge + rule name
        title_row = QtWidgets.QHBoxLayout()
        title_row.setSpacing(6)
        self._badge_lbl = _make_scope_badge(self._badge_type)
        title_row.addWidget(self._badge_lbl)
        self._name_lbl = QtWidgets.QLabel(self.rule.label)
        self._name_lbl.setStyleSheet("font-weight: 500;")
        title_row.addWidget(self._name_lbl)
        title_row.addStretch()
        name_col.addLayout(title_row)

        # Description (with optional inline dependency link)
        self._desc_lbl = QtWidgets.QLabel()
        self._desc_lbl.setStyleSheet("font-size: 11px;")
        self._desc_lbl.setWordWrap(True)
        if self._dep_info:
            self._desc_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
            self._desc_lbl.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.LinksAccessibleByMouse
            )
            self._desc_lbl.linkActivated.connect(self._on_dep_link_activated)
            self._desc_lbl.linkHovered.connect(self._on_dep_link_hovered)
            self._refresh_dep_description()
        else:
            self._desc_lbl.setText(self.rule.description or "")
        name_col.addWidget(self._desc_lbl)

        outer.addLayout(name_col, stretch=1)

        self._fields_host = QtWidgets.QWidget()
        self._fields_row = QtWidgets.QHBoxLayout(self._fields_host)
        self._fields_row.setContentsMargins(0, 0, 0, 0)
        self._fields_row.setSpacing(10)
        outer.addWidget(self._fields_host)

        self._feedback_btn = _SvgToolButton(
            _FEEDBACK_SVG,
            "Rule details, criticality and feedback messages",
            self,
        )
        self._feedback_btn.clicked.connect(self.feedback_requested.emit)
        outer.addWidget(self._feedback_btn)

    def _on_check_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        self.active_toggled.emit(checked)

    def _make_field(self, label_text: str) -> tuple[QtWidgets.QVBoxLayout, UnitInput]:
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(3)
        col.setContentsMargins(0, 0, 0, 0)

        lbl = QtWidgets.QLabel(label_text)
        lbl.setProperty("fieldLabel", True)
        col.addWidget(lbl)

        field = UnitInput(self.rule.unit or "")
        field.setFixedWidth(110)
        col.addWidget(field)

        if self.rule.unit_suffix:
            suffix = QtWidgets.QLabel(self.rule.unit_suffix)
            suffix.setProperty("unitSuffix", True)
            col.addWidget(suffix)

        return col, field

    def _wire_field(self, field: UnitInput, attr: str) -> None:
        field.editing_finished.connect(lambda f=field, a=attr: self._on_field_changed(f, a))
        field.reset_clicked.connect(lambda a=attr: self.field_reset_requested.emit(a))

    def _on_field_changed(self, field: UnitInput, attr: str) -> None:
        if self._loading:
            return
        value = field.text().strip()
        self.value_changed.emit(attr, value)
        self._refresh_override_state(field, attr, value)

    def _refresh_override_state(self, field: UnitInput, attr: str, value: str) -> None:
        def_val = getattr(self._default_limit, attr, "") if self._default_limit else ""
        is_override = bool(value and value != str(def_val) and not self._is_default)
        field.set_override(is_override)

    def _load_field(self, field: UnitInput, attr: str) -> None:
        val = getattr(self._material_limit, attr, "") if self._material_limit else ""
        def_val = getattr(self._default_limit, attr, "") if self._default_limit else ""
        display = val if (val or self._is_default) else def_val

        self._loading = True
        field.set_text(str(display))
        self._loading = False
        self._refresh_override_state(field, attr, str(display))

    def _refresh_active_style(self) -> None:
        self.setProperty("active", "true" if self._is_active else "false")
        self._name_lbl.setEnabled(self._is_active)
        if self._desc_lbl is not None:
            self._desc_lbl.setEnabled(self._is_active)
        self._fields_host.setEnabled(self._is_active)
        # The rule details button stays enabled while the rule is off, so its
        # diagram and feedback messages can still be viewed.
        self.style().unpolish(self)
        self.style().polish(self)
        if self._dep_info:
            self._refresh_dep_description()

    def set_editable(self, editable: bool) -> None:
        """Disable inputs; the theme's disabled palette provides the dimming."""
        self._check.setEnabled(editable)
        self._feedback_btn.setEnabled(editable)
        if editable:
            self._refresh_active_style()
        else:
            self._name_lbl.setEnabled(False)
            if self._desc_lbl is not None:
                self._desc_lbl.setEnabled(False)
            self._fields_host.setEnabled(False)

    def _apply_opacity(self, opacity: float) -> None:
        if self._opacity_effect is None:
            self._opacity_effect = QtWidgets.QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(self._opacity_effect)
        self._opacity_effect.setOpacity(opacity)

    # -- Inline dependency display --------------------------------------------

    def _refresh_dep_description(self) -> None:
        """Rebuild the description HTML with the current dependency value."""
        if not self._dep_info or self._desc_lbl is None:
            return

        val = self._dep_value
        unit = self._dep_info.unit
        display = f"{val}{unit}" if val else "—"

        before = self._dep_info.desc_before
        after = self._dep_info.desc_after
        source_name = self._dep_info.source_rule

        link_html = (
            f'<a href="dep:{source_name}" '
            f'style="color:#5b9bd5; font-weight:bold; font-style:italic; '
            f'text-decoration:none">{display}</a>'
        )
        self._desc_lbl.setText(f"{before}{link_html}{after}")

    def _on_dep_link_activated(self, url: str) -> None:
        if url.startswith("dep:"):
            rule_name = url[4:]
            try:
                target_rule = Rulebook[rule_name]
                self.dep_link_clicked.emit(target_rule)
            except KeyError:
                pass

    def _on_dep_link_hovered(self, url: str) -> None:
        if self._desc_lbl is None:
            return
        if url and self._dep_info:
            try:
                source = Rulebook[self._dep_info.source_rule]
                field_label = source.field_label_for(self._dep_info.source_field)
                self._desc_lbl.setToolTip(f"{source.label} → {field_label}")
            except KeyError:
                self._desc_lbl.setToolTip("")
        else:
            self._desc_lbl.setToolTip("")

    def update_dep_value(self, value: str) -> None:
        """Update the inline dependency value in the description."""
        self._dep_value = value
        self._refresh_dep_description()

    # -- Field value access (for dependency wiring) ---------------------------

    def get_field_value(self, attr: str) -> str:
        """Return the current display value of a field by attribute name.

        Overridden by subclasses that have editable inputs.
        """
        return ""

    def get_field_widget(self, attr: str) -> Optional["UnitInput"]:
        """Return the UnitInput widget for a field by attribute name.

        Overridden by subclasses that have editable inputs.
        """
        return None

    def _build_fields(self) -> None:
        raise NotImplementedError

    def _load_values(self) -> None:
        raise NotImplementedError


# =============================================================================


class TargetAndLimitCard(BaseRuleCard):
    def _build_fields(self) -> None:
        labels = self.rule.field_labels
        target_col, self._target_field = self._make_field(labels[0])
        limit_col, self._limit_field = self._make_field(labels[1])
        self._fields_row.addLayout(target_col)
        self._fields_row.addLayout(limit_col)
        self._wire_field(self._target_field, "target")
        self._wire_field(self._limit_field, "limit")

    def _load_values(self) -> None:
        self._load_field(self._target_field, "target")
        self._load_field(self._limit_field, "limit")

    def get_field_value(self, attr: str) -> str:
        if attr == "target":
            return self._target_field.text()
        if attr == "limit":
            return self._limit_field.text()
        return ""

    def get_field_widget(self, attr: str) -> Optional[UnitInput]:
        if attr == "target":
            return self._target_field
        if attr == "limit":
            return self._limit_field
        return None


# =============================================================================


class TargetOnlyCard(BaseRuleCard):
    def _build_fields(self) -> None:
        col, self._target_field = self._make_field(self.rule.field_labels[0])
        self._fields_row.addLayout(col)
        self._wire_field(self._target_field, "target")

    def _load_values(self) -> None:
        self._load_field(self._target_field, "target")

    def get_field_value(self, attr: str) -> str:
        if attr == "target":
            return self._target_field.text()
        return ""

    def get_field_widget(self, attr: str) -> Optional[UnitInput]:
        if attr == "target":
            return self._target_field
        return None


# =============================================================================


class LimitOnlyCard(BaseRuleCard):
    def _build_fields(self) -> None:
        col, self._limit_field = self._make_field(self.rule.field_labels[0])
        self._fields_row.addLayout(col)
        self._wire_field(self._limit_field, "limit")

    def _load_values(self) -> None:
        self._load_field(self._limit_field, "limit")

    def get_field_value(self, attr: str) -> str:
        if attr == "limit":
            return self._limit_field.text()
        return ""

    def get_field_widget(self, attr: str) -> Optional[UnitInput]:
        if attr == "limit":
            return self._limit_field
        return None


# =============================================================================


class MinAndMaxCard(BaseRuleCard):
    def _build_fields(self) -> None:
        labels = self.rule.field_labels
        min_col, self._min_field = self._make_field(labels[0])
        max_col, self._max_field = self._make_field(labels[1])
        self._fields_row.addLayout(min_col)
        self._fields_row.addLayout(max_col)
        self._wire_field(self._min_field, "min_value")
        self._wire_field(self._max_field, "max_value")

    def _load_values(self) -> None:
        self._load_field(self._min_field, "min_value")
        self._load_field(self._max_field, "max_value")

    def get_field_value(self, attr: str) -> str:
        if attr == "min_value":
            return self._min_field.text()
        if attr == "max_value":
            return self._max_field.text()
        return ""

    def get_field_widget(self, attr: str) -> Optional[UnitInput]:
        if attr == "min_value":
            return self._min_field
        if attr == "max_value":
            return self._max_field
        return None


# =============================================================================


class BinaryCard(BaseRuleCard):
    def _build_fields(self) -> None:
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(3)
        col.setContentsMargins(0, 0, 0, 0)

        lbl = QtWidgets.QLabel(self.rule.field_labels[0])
        lbl.setProperty("fieldLabel", True)
        col.addWidget(lbl)

        self._combo = QtWidgets.QComboBox()
        self._combo.addItems(["ERROR", "WARNING"])
        # Combo plus its reset button take the width of a value field when the
        # text allows, so columns line up; it never shrinks below its text.
        self._combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._combo.setMinimumWidth(
            max(110 - _FieldResetButton._SIZE - 3, self._combo.sizeHint().width())
        )
        self._combo.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

        combo_row = QtWidgets.QHBoxLayout()
        combo_row.setContentsMargins(0, 0, 0, 0)
        combo_row.setSpacing(3)
        combo_row.addWidget(self._combo)
        self._combo_reset = _FieldResetButton(self)
        self._combo_reset.clicked.connect(
            lambda: self.field_reset_requested.emit("binary_severity")
        )
        combo_row.addWidget(self._combo_reset)
        col.addLayout(combo_row)

        self._fields_row.addStretch()
        self._fields_row.addLayout(col)
        self._combo.currentTextChanged.connect(self._on_severity_changed)

    def _load_values(self) -> None:
        def_val = self._severity_default()
        override_val = (
            getattr(self._material_limit, "binary_severity", None) if self._material_limit else None
        )
        display = def_val if self._is_default else (override_val or def_val)

        self._loading = True
        idx = self._combo.findText(display)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        self._loading = False
        self._refresh_combo_override(display)

    def _severity_default(self) -> str:
        if self._default_limit:
            return getattr(self._default_limit, "binary_severity", "ERROR") or "ERROR"
        return "ERROR"

    def _refresh_combo_override(self, value: str) -> None:
        def_val = self._severity_default()
        is_override = bool(value and value != def_val and not self._is_default)
        self._combo_reset.setVisible(is_override)
        self._combo.setProperty("override", "true" if is_override else "false")
        self._combo.style().unpolish(self._combo)
        self._combo.style().polish(self._combo)

    def _on_severity_changed(self, text: str) -> None:
        if self._loading:
            return
        self.value_changed.emit("binary_severity", text)
        self._refresh_combo_override(text)

    def get_field_value(self, attr: str) -> str:
        if attr == "binary_severity":
            return self._combo.currentText()
        return ""


# =============================================================================


class DerivedRuleCard(BaseRuleCard):
    """Card for rules whose thresholds are computed by the archetype at runtime.

    Shows an italic hint instead of editable input fields.
    """

    def __init__(
        self,
        rule: Rulebook,
        material_limit: Optional[RuleLimit],
        default_limit: Optional[RuleLimit],
        is_default_material: bool,
        is_active: bool,
        hint: str = "",
        badge_type: str = "S",
        parent: Optional[QtWidgets.QWidget] = None,
    ):
        self._hint = hint
        super().__init__(
            rule,
            material_limit,
            default_limit,
            is_default_material,
            is_active,
            badge_type,
            parent,
        )

    def _build_fields(self) -> None:
        lbl = QtWidgets.QLabel(self._hint)
        lbl.setStyleSheet("font-style: italic; font-size: 11px; color: palette(mid);")
        lbl.setWordWrap(True)
        lbl.setMinimumWidth(180)
        self._fields_row.addWidget(lbl)

    def _load_values(self) -> None:
        # No editable fields to load
        pass


# =============================================================================


_CARD_CLASSES: dict[RuleShape, type[BaseRuleCard]] = {
    RuleShape.TARGET_AND_LIMIT: TargetAndLimitCard,
    RuleShape.TARGET_ONLY: TargetOnlyCard,
    RuleShape.LIMIT_ONLY: LimitOnlyCard,
    RuleShape.MIN_AND_MAX: MinAndMaxCard,
    RuleShape.BINARY: BinaryCard,
}


def build_card(
    rule: Rulebook,
    material_limit: Optional[RuleLimit],
    default_limit: Optional[RuleLimit],
    is_default_material: bool,
    is_active: bool,
    parent: Optional[QtWidgets.QWidget] = None,
    archetype: Optional[ProcessArchetype] = None,
) -> BaseRuleCard:
    badge_type = _resolve_badge(rule, archetype)

    # If the archetype derives this rule at runtime, show a hint instead of inputs
    if archetype is not None:
        derived = archetype.derived_rules
        if rule in derived:
            return DerivedRuleCard(
                rule,
                material_limit,
                default_limit,
                is_default_material,
                is_active,
                hint=derived[rule],
                badge_type=badge_type,
                parent=parent,
            )

    card_cls = _CARD_CLASSES[rule.shape]
    return card_cls(
        rule,
        material_limit,
        default_limit,
        is_default_material,
        is_active,
        badge_type,
        parent,
    )
