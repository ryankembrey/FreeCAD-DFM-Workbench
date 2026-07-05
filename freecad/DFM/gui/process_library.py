# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from pathlib import Path
from typing import Optional

from PySide6 import QtWidgets, QtCore, QtGui

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ..core.processes.process import Material, Process, RuleFeedback, RuleLimit
from ..core.registries.process_registry import ProcessRegistry
from ..core.rules import Criticality, Rulebook

from .rule_cards import CARD_STYLE, BaseRuleCard, build_card

from . import DFM_rc  # noqa: F401


TREE_FRAME_STYLE = """
QFrame#fTreeHeader {
    border-bottom: 1px solid palette(mid);
}
QTreeWidget#twProcess {
    border: none;
}
QToolButton {
    background: transparent;
    border: none;
    border-radius: 3px;
}
QToolButton:hover {
    background: rgba(127, 127, 127, 40);
}
"""


# =============================================================================


class ProcessLibraryModel:
    """Application state, data persistence, and active selections."""

    def __init__(self):
        self.registry = ProcessRegistry.get_instance()
        self.registry.discover_processes()
        self.is_dirty = False
        self.active_process: Optional[Process] = None
        self.active_material: Optional[Material] = None

    def mark_dirty(self):
        self.is_dirty = True

    def save(self):
        self.registry.save_all_processes()
        self.is_dirty = False

    def get_categorized_processes(self) -> dict:
        return {
            cat: self.registry.get_processes_for_category(cat)
            for cat in self.registry.get_categories()
        }

    def set_active_process(self, name: str) -> Optional[Process]:
        self.active_process = self.registry.get_process_by_name(name)
        self.active_material = None
        return self.active_process

    def set_active_material(self, name: str) -> Optional[Material]:
        if self.active_process and name in self.active_process.materials:
            self.active_material = self.active_process.materials[name]
        else:
            self.active_material = None
        return self.active_material

    def update_rule_feedback(self, rule: Rulebook, warning_msg: str, error_msg: str):
        if self.active_process:
            self.active_process.rule_feedback[rule] = RuleFeedback(warning_msg, error_msg)
            self.mark_dirty()

    def add_process(self, name: str, category: str) -> bool:
        if self.registry.get_process_by_name(name):
            return False
        new_process = Process(
            name=name,
            category=category,
            description="New manufacturing process.",
            active_rules=[],
            materials={
                "Default": Material(
                    name="Default", category="Default", is_active=True, rule_limits={}
                )
            },
        )
        self.registry.add_process(new_process)
        self.mark_dirty()
        return True

    def add_material(self, name: str) -> bool:
        if not self.active_process or name in self.active_process.materials:
            return False
        self.active_process.materials[name] = Material(
            name=name, category="", is_active=True, rule_limits={}
        )
        self.mark_dirty()
        return True

    def delete_material(self, name: str) -> bool:
        if (
            not self.active_process
            or name not in self.active_process.materials
            or name == "Default"
        ):
            return False
        del self.active_process.materials[name]
        if self.active_material and self.active_material.name == name:
            self.active_material = None
        self.mark_dirty()
        return True

    def update_rule_limit(self, rule: Rulebook, attr: str, value: str):
        if not self.active_material:
            return
        if rule not in self.active_material.rule_limits:
            self.active_material.rule_limits[rule] = RuleLimit()
        setattr(self.active_material.rule_limits[rule], attr, value)
        self.mark_dirty()

    def reset_rule_limit(self, rule: Rulebook):
        if self.active_material and rule in self.active_material.rule_limits:
            del self.active_material.rule_limits[rule]
            self.mark_dirty()

    def update_active_rules(self, rules: list):
        if self.active_process:
            self.active_process.active_rules = rules
            self.mark_dirty()

    def update_rule_criticality(self, rule: Rulebook, criticality: Criticality):
        if self.active_process:
            self.active_process.rule_criticality[rule] = criticality
            self.mark_dirty()

    def is_builtin(self, process_name: str) -> bool:
        return self.registry.is_builtin(process_name)

    def has_user_override(self, process_name: str) -> bool:
        return self.registry.has_user_override(process_name)

    def restore_default(self, process_name: str) -> bool:
        success = self.registry.restore_default(process_name)
        if success:
            self.active_process = self.registry.get_process_by_name(process_name)
            self.active_material = None
            self.is_dirty = False
        return success

    def delete_process(self, process_name: str) -> bool:
        success = self.registry.delete_custom_process(process_name)
        if success and self.active_process and self.active_process.name == process_name:
            self.active_process = None
            self.active_material = None
        return success

    def import_process(self, filepath: str) -> tuple[bool, str]:
        success, result = self.registry.import_process_from_file(Path(filepath))
        if success:
            self.mark_dirty()
        return success, result


# =============================================================================


class ProcessTreeView(QtCore.QObject):
    selection_changed = QtCore.Signal(dict)

    def __init__(self, tree: QtWidgets.QTreeWidget):
        super().__init__()
        self.tree = tree
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(15)
        self.tree.itemSelectionChanged.connect(self._on_selection)

    def populate(self, categories_dict: dict):
        self.tree.blockSignals(True)
        self.tree.clear()

        for cat_name, processes in categories_dict.items():
            cat_node = QtWidgets.QTreeWidgetItem(self.tree)
            cat_node.setText(0, cat_name)
            cat_node.setFlags(cat_node.flags() & ~QtCore.Qt.ItemFlag.ItemIsSelectable)
            font = cat_node.font(0)
            font.setBold(True)
            cat_node.setFont(0, font)

            for process in processes:
                p_node = QtWidgets.QTreeWidgetItem(cat_node)
                p_node.setText(0, process.name)
                p_node.setData(
                    0,
                    QtCore.Qt.ItemDataRole.UserRole,
                    {"type": "process", "name": process.name},
                )

                mats = sorted(
                    process.materials.keys(),
                    key=lambda name: (name != "Default", name),
                )
                for m_name in mats:
                    m_node = QtWidgets.QTreeWidgetItem(p_node)
                    m_node.setText(0, m_name)
                    m_node.setData(
                        0,
                        QtCore.Qt.ItemDataRole.UserRole,
                        {"type": "material", "name": m_name, "process": process.name},
                    )

        self.tree.expandAll()
        self.tree.blockSignals(False)

    def _iter_nodes(self):
        it = QtWidgets.QTreeWidgetItemIterator(self.tree)
        while it.value():
            yield it.value()
            it += 1

    def select_process(self, name: str):
        for node in self._iter_nodes():
            data = node.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if data and data.get("type") == "process" and data.get("name") == name:
                self.tree.setCurrentItem(node)
                return

    def select_material(self, process_name: str, mat_name: str):
        for node in self._iter_nodes():
            data = node.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if (
                data
                and data.get("type") == "material"
                and data.get("process") == process_name
                and data.get("name") == mat_name
            ):
                self.tree.setCurrentItem(node)
                return

    def _on_selection(self):
        selected = self.tree.selectedItems()
        if not selected:
            return
        payload = selected[0].data(0, QtCore.Qt.ItemDataRole.UserRole)
        if payload:
            self.selection_changed.emit(payload)


# =============================================================================


class ParameterEdit(QtWidgets.QPlainTextEdit):
    """Text editor that suggests {placeholders} when '{' is typed."""

    def __init__(self, placeholders, parent=None):
        super().__init__(parent)
        self.completer = QtWidgets.QCompleter(placeholders, self)
        self.completer.setWidget(self)
        self.completer.setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)
        self.completer.activated.connect(self.insert_completion)

    def insert_completion(self, completion):
        tc = self.textCursor()
        tc.insertText(completion + "}")
        self.setTextCursor(tc)

    def keyPressEvent(self, event):
        if self.completer.popup().isVisible() and event.key() in (
            QtCore.Qt.Key.Key_Enter,
            QtCore.Qt.Key.Key_Return,
            QtCore.Qt.Key.Key_Escape,
            QtCore.Qt.Key.Key_Tab,
        ):
            event.ignore()
            return

        super().keyPressEvent(event)

        if event.text() == "{":
            cr = self.cursorRect()
            cr.setWidth(
                self.completer.popup().sizeHintForColumn(0)
                + self.completer.popup().verticalScrollBar().sizeHint().width()
            )
            self.completer.complete(cr)


# =============================================================================


class EditFeedbackDialog(QtWidgets.QDialog):
    """Warning and error message editor with placeholder autocompletion."""

    ALLOWED_TAGS = {"measured", "limit", "target"}

    def __init__(
        self,
        process_name: str,
        rule_name: str,
        feedback: RuleFeedback,
        criticality: Criticality,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Edit Feedback: {rule_name}")
        self.setMinimumWidth(480)
        self.setMinimumHeight(450)

        layout = QtWidgets.QVBoxLayout(self)

        layout.addWidget(self._build_header(process_name, rule_name, criticality))

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        layout.addWidget(line)
        layout.addSpacing(10)

        params = list(self.ALLOWED_TAGS)
        self.warn_edit = self._build_message_field(
            layout,
            ":/icons/dfm_warning.svg",
            "Warning Message",
            feedback.warning_msg,
            params,
            "e.g., Feature is too small ({measured}), minimum required is {limit}",
        )
        layout.addSpacing(10)
        self.err_edit = self._build_message_field(
            layout,
            ":/icons/dfm_error.svg",
            "Error Message",
            feedback.error_msg,
            params,
            "e.g., Critical failure: {measured} is far below the {limit} limit.",
        )

        layout.addSpacing(10)
        layout.addWidget(self._build_help_panel())

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _build_header(
        self, process_name: str, rule_name: str, criticality: Criticality
    ) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 10)

        title = QtWidgets.QLabel(f"{rule_name} ({process_name})")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        vbox.addWidget(title)

        crit_row = QtWidgets.QHBoxLayout()
        crit_row.addWidget(QtWidgets.QLabel("Criticality"))
        self.crit_combo = QtWidgets.QComboBox()
        for c in Criticality:
            self.crit_combo.addItem(c.label, c)
        self.crit_combo.setCurrentIndex(criticality.value)
        self.crit_combo.setFixedWidth(110)
        crit_row.addWidget(self.crit_combo)
        crit_row.addStretch()
        vbox.addLayout(crit_row)
        return container

    def _build_message_field(
        self,
        parent_layout: QtWidgets.QLayout,
        icon_path: str,
        title: str,
        initial_text: str,
        placeholders: list,
        placeholder_hint: str,
    ) -> ParameterEdit:
        header = QtWidgets.QHBoxLayout()
        icon = QtWidgets.QLabel()
        icon.setPixmap(QtGui.QIcon(icon_path).pixmap(16, 16))
        header.addWidget(icon)
        lbl = QtWidgets.QLabel(title)
        lbl.setStyleSheet("font-weight: bold;")
        header.addWidget(lbl)
        header.addStretch()
        parent_layout.addLayout(header)

        edit = ParameterEdit(placeholders, self)
        edit.setPlainText(initial_text)
        edit.setPlaceholderText(placeholder_hint)
        parent_layout.addWidget(edit)
        return edit

    def _build_help_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        panel.setObjectName("HelpContainer")
        panel.setStyleSheet(
            "#HelpContainer { border: 1px solid palette(mid); border-radius: 4px; }"
        )
        grid = QtWidgets.QGridLayout(panel)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setSpacing(6)

        title = QtWidgets.QLabel("Available Parameters")
        title.setStyleSheet("font-weight: bold; margin-bottom: 2px;")
        grid.addWidget(title, 0, 0, 1, 2)

        rows = [
            ("<b>{measured}</b>", "Current measured value"),
            ("<b>{limit}</b>", "The threshold value"),
            ("<b>{target}</b>", "The ideal target value"),
        ]
        for i, (code, desc) in enumerate(rows, start=1):
            grid.addWidget(QtWidgets.QLabel(code), i, 0)
            grid.addWidget(QtWidgets.QLabel(desc), i, 1)
        grid.setColumnStretch(1, 1)
        return panel

    def get_data(self) -> tuple[str, str, Criticality]:
        return (
            self.warn_edit.toPlainText().strip(),
            self.err_edit.toPlainText().strip(),
            self.crit_combo.currentData(),
        )

    def accept(self):
        import re

        warn, err, _ = self.get_data()
        found_tags = re.findall(r"\{(.*?)\}", warn + err)
        for tag in found_tags:
            if tag not in self.ALLOWED_TAGS:
                QtWidgets.QMessageBox.warning(
                    self, "Invalid Tag", f"The tag {{{tag}}} is not recognized."
                )
                return
        super().accept()


# =============================================================================


class MaterialEditView(QtCore.QObject):
    limit_changed = QtCore.Signal(object, str, str)
    reset_requested = QtCore.Signal(object)
    rule_active_toggled = QtCore.Signal(object, bool)
    feedback_requested = QtCore.Signal(object)

    def __init__(self, scroll_area: QtWidgets.QScrollArea):
        super().__init__()
        self.scroll = scroll_area
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._container = QtWidgets.QWidget()
        self._container.setObjectName("CardContainer")
        self._container.setStyleSheet("QWidget#CardContainer { background: transparent; }")

        self._layout = QtWidgets.QVBoxLayout(self._container)
        self._layout.setContentsMargins(4, 4, 12, 4)
        self._layout.setSpacing(8)

        self.scroll.setWidget(self._container)
        self._cards: dict[Rulebook, BaseRuleCard] = {}
        self._material: Optional[Material] = None
        self._default_material: Optional[Material] = None
        self._active_rules: list = []
        self._editable: bool = True

    def populate(
        self,
        material: Material,
        default_material: Optional[Material],
        active_rules: list,
        editable: bool = True,
    ) -> None:
        """Full rebuild. Used when switching material or process."""
        scroll_pos = self.scroll.verticalScrollBar().value()

        self._material = material
        self._default_material = default_material
        self._active_rules = list(active_rules)
        self._editable = editable

        self._clear()

        for rule in self._sorted_rules(active_rules):
            is_active = rule in active_rules
            card = build_card(
                rule=rule,
                material_limit=material.rule_limits.get(rule),
                default_limit=(
                    default_material.rule_limits.get(rule) if default_material else None
                ),
                is_default_material=(material.name == "Default"),
                is_active=is_active,
                parent=self._container,
            )
            card.set_editable(editable)
            if editable:
                self._wire_card(card, rule)
            self._layout.addWidget(card)
            self._cards[rule] = card

        self._layout.addStretch(1)

        QtCore.QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(scroll_pos))

    def refresh_active_rules(self, active_rules: list) -> None:
        """Update card active state in place, no rebuild.

        Called when a rule's checkbox is toggled. Reorders cards if needed.
        """
        if not self._material:
            return

        self._active_rules = list(active_rules)
        active_set = set(active_rules)

        for rule, card in self._cards.items():
            card.refresh(
                material_limit=self._material.rule_limits.get(rule),
                default_limit=(
                    self._default_material.rule_limits.get(rule) if self._default_material else None
                ),
                is_default_material=(self._material.name == "Default"),
                is_active=rule in active_set,
            )

        self._reorder_cards()

    def refresh_rule(self, rule: Rulebook) -> None:
        """Update a single card's values in place. Called after reset."""
        if not self._material:
            return
        card = self._cards.get(rule)
        if not card:
            return
        card.refresh(
            material_limit=self._material.rule_limits.get(rule),
            default_limit=(
                self._default_material.rule_limits.get(rule) if self._default_material else None
            ),
            is_default_material=(self._material.name == "Default"),
            is_active=rule in self._active_rules,
        )

    def clear(self) -> None:
        self._clear()

    def filter_rules(self, text: str) -> None:
        term = text.lower().strip()
        for card in self._cards.values():
            label_match = term in card.rule.label.lower()
            desc_match = term in (card.rule.description or "").lower()
            card.setVisible(label_match or desc_match)

    def _sorted_rules(self, active_rules) -> list:
        return sorted(
            Rulebook,
            key=lambda r: (r not in active_rules, r.label),
        )

    def _reorder_cards(self) -> None:
        """Move card widgets in the layout to match sorted order without rebuilding."""
        desired_order = self._sorted_rules(self._active_rules)

        for i, rule in enumerate(desired_order):
            card = self._cards.get(rule)
            if not card:
                continue
            current_index = self._layout.indexOf(card)
            if current_index != i:
                self._layout.removeWidget(card)
                self._layout.insertWidget(i, card)

    def _wire_card(self, card: BaseRuleCard, rule: Rulebook) -> None:
        card.value_changed.connect(lambda attr, val, r=rule: self.limit_changed.emit(r, attr, val))
        card.reset_requested.connect(lambda r=rule: self.reset_requested.emit(r))
        card.active_toggled.connect(lambda state, r=rule: self.rule_active_toggled.emit(r, state))
        card.feedback_requested.connect(lambda r=rule: self.feedback_requested.emit(r))

    def _clear(self) -> None:
        self._cards.clear()
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()


# =============================================================================


class _NamedCategoryDialog(QtWidgets.QDialog):
    """Shared dialog for name and category input."""

    def __init__(
        self,
        title: str,
        name_label: str,
        cat_label: str,
        categories: list[str],
        name_placeholder: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(350)

        layout = QtWidgets.QFormLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setMinimumHeight(32)
        if name_placeholder:
            self.name_edit.setPlaceholderText(name_placeholder)

        self.cat_combo = QtWidgets.QComboBox()
        self.cat_combo.setEditable(True)
        self.cat_combo.setMinimumHeight(32)

        unique = sorted(set(categories))
        self.cat_combo.addItems(unique)
        if unique:
            self.cat_combo.setPlaceholderText("Select or type a category...")
        else:
            self.cat_combo.setPlaceholderText("Type a category...")

        layout.addRow(name_label, self.name_edit)
        layout.addRow(cat_label, self.cat_combo)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        for button in self.buttons.buttons():
            button.setMinimumHeight(32)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)

    def get_data(self) -> tuple[str, str]:
        return self.name_edit.text().strip(), self.cat_combo.currentText().strip()


# =============================================================================


class AddProcessDialog(_NamedCategoryDialog):
    def __init__(self, categories: list[str], parent=None):
        super().__init__(
            "Add New Process",
            "Process Name:",
            "Category:",
            categories,
            parent=parent,
        )


# =============================================================================


class AddMaterialDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Material")
        self.setMinimumWidth(300)

        layout = QtWidgets.QFormLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setMinimumHeight(32)

        layout.addRow("Material Name:", self.name_edit)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        for button in self.buttons.buttons():
            button.setMinimumHeight(32)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)

    def get_data(self) -> str:
        return self.name_edit.text().strip()


# =============================================================================


class ProcessLibraryController(QtCore.QObject):
    def __init__(self, view: "ProcessLibrary", model: ProcessLibraryModel):
        super().__init__()
        self.view = view
        self.model = model
        self._connect_signals()
        self._refresh_process_tree()
        self._auto_select_first()

    def _connect_signals(self):
        f = self.view.form
        f.tbNew.clicked.connect(self.on_new_request)
        f.tbDelete.clicked.connect(self.on_delete_request)
        f.tbRestore.clicked.connect(self.on_restore_default)
        f.tbImport.clicked.connect(self.on_import_process)
        f.leSearchRules.textChanged.connect(self.view.material_edit_view.filter_rules)
        f.buttonBox.accepted.connect(self.on_save)
        f.buttonBox.rejected.connect(self.on_reject)

        tree = self.view.tree_view
        tree.selection_changed.connect(self.on_tree_selection_changed)
        tree.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        tree.tree.customContextMenuRequested.connect(self.on_tree_context_menu)

        edit = self.view.material_edit_view
        edit.limit_changed.connect(self.on_limit_changed)
        edit.reset_requested.connect(self.on_limit_reset)
        edit.rule_active_toggled.connect(self.on_rule_toggled)
        edit.feedback_requested.connect(self.on_edit_feedback)

    def _update_dirty_state(self):
        self.view.set_dirty_title(self.model.is_dirty)

    def _refresh_process_tree(self):
        self.view.tree_view.populate(self.model.get_categorized_processes())

    def _auto_select_first(self):
        for processes in self.model.get_categorized_processes().values():
            if processes:
                self.view.tree_view.select_material(processes[0].name, "Default")
                return

    def _reselect_current_material(self):
        if not (self.model.active_material and self.model.active_process):
            return
        self.on_tree_selection_changed(
            {
                "type": "material",
                "name": self.model.active_material.name,
                "process": self.model.active_process.name,
            }
        )

    def on_tree_selection_changed(self, payload: dict):
        node_type = payload.get("type")
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            return

        self.view.form.leSearchRules.clear()

        is_builtin = self.model.is_builtin(name) if node_type == "process" else False
        has_override = self.model.has_user_override(name) if node_type == "process" else False
        can_delete = (node_type == "material" and name != "Default") or (
            node_type == "process" and not is_builtin
        )
        self.view.form.tbDelete.setEnabled(can_delete)
        self.view.form.tbRestore.setEnabled(node_type == "process" and is_builtin and has_override)

        if node_type == "process":
            self._display_process_readonly(name)
        elif node_type == "material":
            p_name = payload.get("process")
            if p_name:
                self._display_material(p_name, name)

    def _display_process_readonly(self, name: str):
        process = self.model.set_active_process(name)
        self.view.set_material_edit_title("Select a Material")
        if not process:
            return
        self.view.set_process_label(process.name, process.description)
        default_mat = process.materials.get("Default")
        if default_mat:
            self.view.material_edit_view.populate(
                default_mat, default_mat, process.active_rules, editable=False
            )

    def _display_material(self, process_name: str, mat_name: str):
        process = self.model.set_active_process(process_name)
        material = self.model.set_active_material(mat_name)
        if not (process and material):
            return
        self.view.set_process_label(process.name, process.description)
        self.view.set_material_edit_title(f"Editing {material.name}")
        default_mat = process.materials.get("Default", material)
        self.view.material_edit_view.populate(
            material, default_mat, process.active_rules, editable=True
        )

    def on_new_request(self):
        menu = QtWidgets.QMenu(self.view.form.tbNew)

        action_process = menu.addAction("New Manufacturing Process...")
        action_process.triggered.connect(self.on_add_process)

        menu.addSeparator()

        action_material = menu.addAction("New Material...")
        active_p = self.model.active_process
        action_material.setEnabled(active_p is not None)
        if active_p:
            action_material.setText(f"New Material for {active_p.name}...")
            action_material.triggered.connect(lambda: self.on_add_material(active_p.name))

        btn = self.view.form.tbNew
        menu.exec(btn.mapToGlobal(QtCore.QPoint(0, btn.height())))

    def on_delete_request(self):
        item = self.view.tree_view.tree.currentItem()
        if not item:
            return
        payload = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not payload:
            return
        if payload["type"] == "process":
            self.on_delete_process(payload["name"])
        else:
            self.on_delete_material(payload["process"], payload["name"])

    def on_tree_context_menu(self, pos: QtCore.QPoint):
        item = self.view.tree_view.tree.itemAt(pos)
        if not item:
            return
        payload = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not payload:
            return

        menu = QtWidgets.QMenu()
        if payload["type"] == "process":
            act = menu.addAction("Add Material...")
            act.triggered.connect(lambda: self.on_add_material(payload["name"]))
        elif payload["type"] == "material" and payload["name"] != "Default":
            act = menu.addAction(f"Delete {payload['name']}")
            act.triggered.connect(
                lambda: self.on_delete_material(payload["process"], payload["name"])
            )
        menu.exec(self.view.tree_view.tree.viewport().mapToGlobal(pos))

    def on_add_process(self):
        dlg = AddProcessDialog(self.model.registry.get_categories(), self.view)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        name, category = dlg.get_data()
        if not name or not category:
            QtWidgets.QMessageBox.warning(
                self.view, "Invalid Input", "Both name and category are required."
            )
            return
        if not self.model.add_process(name, category):
            QtWidgets.QMessageBox.warning(
                self.view, "Duplicate", f"Process '{name}' already exists."
            )
            return
        self._update_dirty_state()
        self._refresh_process_tree()
        self.view.tree_view.select_process(name)

    def on_delete_process(self, process_name: str):
        if self.model.is_builtin(process_name):
            return
        confirm = QtWidgets.QMessageBox.question(
            self.view,
            "Delete Process",
            f"Permanently delete custom process '{process_name}'?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        if self.model.delete_process(process_name):
            self._update_dirty_state()
            self._refresh_process_tree()
            self._auto_select_first()

    def on_import_process(self):
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.view, "Import Process", "", "YAML Files (*.yaml *.yml)"
        )
        if not filepath:
            return
        success, result = self.model.import_process(filepath)
        if success:
            self._refresh_process_tree()
            self.view.tree_view.select_process(result)
            self._update_dirty_state()
        else:
            QtWidgets.QMessageBox.critical(self.view, "Import Failed", result)

    def on_restore_default(self):
        process = self.model.active_process
        if not process:
            return
        confirm = QtWidgets.QMessageBox.question(
            self.view,
            "Restore Default",
            f"Discard all local overrides for {process.name}?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        if self.model.restore_default(process.name):
            self._refresh_process_tree()
            self.view.tree_view.select_process(process.name)

    def on_add_material(self, process_name: Optional[str] = None):
        """Updated: Removed category gathering and processing."""
        p_name = process_name or (
            self.model.active_process.name if self.model.active_process else None
        )
        if not p_name:
            return

        if self.model.active_process is None or self.model.active_process.name != p_name:
            self.model.set_active_process(p_name)
        if not self.model.active_process:
            return

        dlg = AddMaterialDialog(self.view)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        name = dlg.get_data()
        if not name:
            QtWidgets.QMessageBox.warning(self.view, "Invalid Input", "Material name is required.")
            return

        if not self.model.add_material(name):
            QtWidgets.QMessageBox.warning(
                self.view, "Duplicate", f"Material '{name}' already exists."
            )
            return

        self._update_dirty_state()
        self._refresh_process_tree()
        self.view.tree_view.select_material(p_name, name)

    def on_delete_material(self, process_name: str, mat_name: str):
        if mat_name == "Default":
            return
        confirm = QtWidgets.QMessageBox.question(
            self.view,
            "Delete Material",
            f"Are you sure you want to delete '{mat_name}'?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.model.set_active_process(process_name)
        if not self.model.delete_material(mat_name):
            return
        self._update_dirty_state()

        self.view.tree_view.tree.blockSignals(True)
        self._refresh_process_tree()
        self.view.tree_view.select_process(process_name)
        self.view.tree_view.tree.blockSignals(False)

        self.on_tree_selection_changed({"type": "process", "name": process_name})

    def on_rule_toggled(self, rule: Rulebook, is_active: bool):
        process = self.model.active_process
        if not process:
            return
        rules = set(process.active_rules)
        if is_active:
            rules.add(rule)
        else:
            rules.discard(rule)
        self.model.update_active_rules(list(rules))
        self._update_dirty_state()
        self.view.material_edit_view.refresh_active_rules(process.active_rules)

    def on_edit_feedback(self, rule: Rulebook):
        process = self.model.active_process
        if not process:
            return
        feedback = process.rule_feedback.get(rule) or RuleFeedback()
        criticality = process.get_criticality(rule)

        dlg = EditFeedbackDialog(process.name, rule.label, feedback, criticality, self.view)
        if not dlg.exec():
            return
        warn, err, crit = dlg.get_data()
        process.rule_feedback[rule] = RuleFeedback(warn, err)
        self.model.update_rule_criticality(rule, crit)
        self._update_dirty_state()

    def on_limit_changed(self, rule: Rulebook, attr: str, value: str):
        self.model.update_rule_limit(rule, attr, value)
        self._update_dirty_state()

    def on_limit_reset(self, rule: Rulebook):
        self.model.reset_rule_limit(rule)
        self.view.material_edit_view.refresh_rule(rule)
        self._update_dirty_state()

    def on_save(self):
        try:
            self.model.save()
            self._update_dirty_state()
            self.view.accept()
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self.view, "Save Error", f"Failed to save process data: {e}"
            )

    def on_reject(self):
        if self.model.is_dirty:
            confirm = QtWidgets.QMessageBox.question(
                self.view,
                "Discard changes?",
                "You have unsaved changes. Discard them?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self.view.super_reject()


# =============================================================================


class ProcessLibrary(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent or Gui.getMainWindow())

        try:
            self.form = Gui.PySideUic.loadUi(":/ui/process_library.ui")  # type: ignore
        except Exception as e:
            App.Console.PrintError(f"DFM Error: Failed to load process_library.ui. {e}\n")
            return

        self.setWindowTitle("DFM Process Library")
        self.resize(1200, 850)
        self.form.installEventFilter(self)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.form)

        fc_style = Gui.getMainWindow().styleSheet()
        self.setStyleSheet(fc_style + "\n" + TREE_FRAME_STYLE + CARD_STYLE)

        self.tree_view = ProcessTreeView(self.form.twProcess)
        self.material_edit_view = MaterialEditView(self.form.twMaterialEdit)

        self._configure_static_widgets()
        self._configure_toolbar_icons()

        find_shortcut = QtGui.QShortcut(
            QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Find), self
        )
        find_shortcut.activated.connect(self._focus_search)

        self.model = ProcessLibraryModel()
        self.controller = ProcessLibraryController(self, self.model)

    def _configure_static_widgets(self):
        if hasattr(self.form, "lTitle"):
            self.form.lTitle.setText("Process Library")
        self.form.leSearchRules.setPlaceholderText("Search rules…")
        self.form.leSearchRules.setClearButtonEnabled(True)

    def _configure_toolbar_icons(self):
        std = self.style()
        self.form.tbNew.setIcon(Gui.getIcon("list-add"))
        self.form.tbDelete.setIcon(Gui.getIcon("list-remove"))
        self.form.tbImport.setIcon(Gui.getIcon("Std_Import"))
        self.form.tbRestore.setIcon(
            std.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload)
        )
        for btn in (
            self.form.tbNew,
            self.form.tbImport,
            self.form.tbRestore,
            self.form.tbDelete,
        ):
            btn.setIconSize(QtCore.QSize(20, 20))
            btn.setFixedSize(28, 28)

    def set_dirty_title(self, is_dirty: bool):
        self.setWindowTitle("DFM Process Library" + (" *" if is_dirty else ""))

    def set_process_label(self, name: str, description: str):
        if hasattr(self.form, "lTitle"):
            self.form.lTitle.setToolTip(description)
        if hasattr(self.form, "lblDescription"):
            self.form.lblDescription.setText(description)

    def set_material_edit_title(self, text: str):
        self.form.gbMaterialEdit.setTitle(text)

    def _focus_search(self) -> None:
        self.form.leSearchRules.setFocus()
        self.form.leSearchRules.selectAll()

    def eventFilter(self, obj, event):
        if obj is self.form and event.type() == QtCore.QEvent.Type.KeyPress:
            if event.key() == QtCore.Qt.Key.Key_Escape:
                if self.form.leSearchRules.hasFocus():
                    self.form.leSearchRules.clearFocus()
                    return True
                self.reject()
                return True
        return super().eventFilter(obj, event)

    def super_reject(self):
        super().reject()

    def reject(self):
        self.controller.on_reject()


# =============================================================================


class ProcessLibraryCommand:
    def GetResources(self):
        return {
            "Pixmap": ":/icons/process_library.svg",
            "MenuText": "Process Library",
            "ToolTip": "Create, configure and save manufacturing processes.",
        }

    def Activated(self):
        dlg = ProcessLibrary()
        dlg.exec()

    def IsActive(self):
        return True


if App.GuiUp:
    Gui.addCommand("DFM_ProcessLibrary", ProcessLibraryCommand())
