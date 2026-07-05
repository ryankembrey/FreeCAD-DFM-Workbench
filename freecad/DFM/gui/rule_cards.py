# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..core.processes.process import RuleLimit
from ..core.rules import Rulebook, RuleShape


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

QComboBox[override="true"] {{
    border: 1px solid {OVERRIDE_COLOR};
    color: {OVERRIDE_COLOR};
    font-weight: 500;
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
    border: 0.5px solid {OVERRIDE_COLOR};
}}

QWidget#UnitInput QLineEdit,
QWidget#UnitInput QLineEdit:enabled,
QWidget#UnitInput QLineEdit:focus,
QWidget#UnitInput QLineEdit:hover {{
    background-color: transparent;
    border: none;
    padding: 2px 4px;
}}
QWidget#UnitInput[override="true"] QLineEdit {{
    color: {OVERRIDE_COLOR};
    font-weight: 500;
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
QWidget#UnitInput[override="true"] QLabel[unitBadge="true"] {{
    color: {OVERRIDE_COLOR};
    font-weight: 500;
}}
"""


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

    def __init__(self, unit: str, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setObjectName("UnitInput")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 6, 2)
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

    def text(self) -> str:
        return self._edit.text()

    def set_text(self, value: str) -> None:
        self._edit.setText(value)

    def line_edit(self) -> QtWidgets.QLineEdit:
        return self._edit

    def set_override(self, is_override: bool) -> None:
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
    reset_requested = QtCore.Signal()
    active_toggled = QtCore.Signal(bool)
    feedback_requested = QtCore.Signal()

    def __init__(
        self,
        rule: Rulebook,
        material_limit: Optional[RuleLimit],
        default_limit: Optional[RuleLimit],
        is_default_material: bool,
        is_active: bool,
        parent: Optional[QtWidgets.QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("RuleCard")
        self.rule = rule
        self._material_limit = material_limit
        self._default_limit = default_limit
        self._is_default = is_default_material
        self._is_active = is_active
        self._loading = False
        self._opacity_effect: Optional[QtWidgets.QGraphicsOpacityEffect] = None

        self._build_layout()
        self._build_fields()
        self._load_values()
        self._refresh_active_style()
        self._refresh_reset_state()

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
        self._refresh_reset_state()

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
        self._name_lbl = QtWidgets.QLabel(self.rule.label)
        self._name_lbl.setStyleSheet("font-weight: 500;")
        name_col.addWidget(self._name_lbl)

        if self.rule.description:
            self._desc_lbl = QtWidgets.QLabel(self.rule.description)
            self._desc_lbl.setStyleSheet("font-size: 11px;")
            self._desc_lbl.setWordWrap(True)
            name_col.addWidget(self._desc_lbl)
        outer.addLayout(name_col, stretch=1)

        self._fields_host = QtWidgets.QWidget()
        self._fields_row = QtWidgets.QHBoxLayout(self._fields_host)
        self._fields_row.setContentsMargins(0, 0, 0, 0)
        self._fields_row.setSpacing(10)
        outer.addWidget(self._fields_host)

        self._feedback_btn = self._make_icon_button(
            QtWidgets.QStyle.StandardPixmap.SP_FileDialogContentsView,
            "Edit feedback messages and criticality",
            self.feedback_requested.emit,
        )
        outer.addWidget(self._feedback_btn)

        self._reset = self._make_icon_button(
            QtWidgets.QStyle.StandardPixmap.SP_BrowserReload,
            "Reset to Default material's values",
            self.reset_requested.emit,
        )
        outer.addWidget(self._reset)

    def _make_icon_button(
        self,
        pixmap: QtWidgets.QStyle.StandardPixmap,
        tooltip: str,
        slot,
    ) -> QtWidgets.QToolButton:
        btn = QtWidgets.QToolButton()
        btn.setAutoRaise(True)
        btn.setIcon(self.style().standardIcon(pixmap))
        btn.setIconSize(QtCore.QSize(18, 18))
        btn.setFixedSize(28, 28)
        btn.setToolTip(tooltip)
        btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

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

    def _on_field_changed(self, field: UnitInput, attr: str) -> None:
        if self._loading:
            return
        value = field.text().strip()
        self.value_changed.emit(attr, value)
        self._refresh_override_state(field, attr, value)
        self._refresh_reset_state()

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

    def _has_overrides(self) -> bool:
        for field in self.findChildren(UnitInput):
            if field.is_override():
                return True
        for combo in self.findChildren(QtWidgets.QComboBox):
            if combo.property("override") == "true":
                return True
        return False

    def _refresh_reset_state(self) -> None:
        self._reset.setEnabled(self._is_active and self._has_overrides())

    def _refresh_active_style(self) -> None:
        self.setProperty("active", "true" if self._is_active else "false")
        self._name_lbl.setEnabled(self._is_active)
        if self._desc_lbl is not None:
            self._desc_lbl.setEnabled(self._is_active)
        self._fields_host.setEnabled(self._is_active)
        self._feedback_btn.setEnabled(self._is_active)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_editable(self, editable: bool) -> None:
        """Disable inputs; the theme's disabled palette provides the dimming."""
        self._check.setEnabled(editable)
        self._feedback_btn.setEnabled(editable)
        if editable:
            self._refresh_active_style()
            self._refresh_reset_state()
        else:
            self._name_lbl.setEnabled(False)
            if self._desc_lbl is not None:
                self._desc_lbl.setEnabled(False)
            self._fields_host.setEnabled(False)
            self._reset.setEnabled(False)

    def _apply_opacity(self, opacity: float) -> None:
        if self._opacity_effect is None:
            self._opacity_effect = QtWidgets.QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(self._opacity_effect)
        self._opacity_effect.setOpacity(opacity)

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


# =============================================================================


class TargetOnlyCard(BaseRuleCard):
    def _build_fields(self) -> None:
        col, self._target_field = self._make_field(self.rule.field_labels[0])
        self._fields_row.addLayout(col)
        self._wire_field(self._target_field, "target")

    def _load_values(self) -> None:
        self._load_field(self._target_field, "target")


# =============================================================================


class LimitOnlyCard(BaseRuleCard):
    def _build_fields(self) -> None:
        col, self._limit_field = self._make_field(self.rule.field_labels[0])
        self._fields_row.addLayout(col)
        self._wire_field(self._limit_field, "limit")

    def _load_values(self) -> None:
        self._load_field(self._limit_field, "limit")


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
        self._combo.setFixedWidth(110)
        self._combo.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        col.addWidget(self._combo)

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
        self._combo.setProperty("override", "true" if is_override else "false")
        self._combo.style().unpolish(self._combo)
        self._combo.style().polish(self._combo)

    def _on_severity_changed(self, text: str) -> None:
        if self._loading:
            return
        self.value_changed.emit("binary_severity", text)
        self._refresh_combo_override(text)
        self._refresh_reset_state()


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
) -> BaseRuleCard:
    card_cls = _CARD_CLASSES[rule.shape]
    return card_cls(rule, material_limit, default_limit, is_default_material, is_active, parent)
