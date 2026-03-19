#  ***************************************************************************
#  *   Copyright (c) 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>              *
#  *                                                                         *
#  *   This file is part of the FreeCAD CAx development system.              *
#  *                                                                         *
#  *   This library is free software; you can redistribute it and/or         *
#  *   modify it under the terms of the GNU Library General Public           *
#  *   License as published by the Free Software Foundation; either          *
#  *   version 2 of the License, or (at your option) any later version.      *
#  *                                                                         *
#  *   This library  is distributed in the hope that it will be useful,      *
#  *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
#  *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
#  *   GNU Library General Public License for more details.                  *
#  *                                                                         *
#  *   You should have received a copy of the GNU Library General Public     *
#  *   License along with this library; see the file COPYING.LIB. If not,    *
#  *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
#  *   Suite 330, Boston, MA  02111-1307, USA                                *
#  *                                                                         *
#  ***************************************************************************

from typing import Optional

from PySide6 import QtWidgets, QtCore, QtGui

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from dfm.processes.process import Material, Process, RuleLimit, RuleFeedback
from dfm.registries.process_registry import ProcessRegistry
from dfm.rules import Rulebook

from . import DFM_rc


# =============================================================================


class ProcessLibraryModel:
    """Manages the application state, data persistence, and active selections."""

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
        data = {}
        for cat in self.registry.get_categories():
            data[cat] = self.registry.get_processes_for_category(cat)
        return data

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

    def add_material(self, name: str, category: str) -> bool:
        if not self.active_process or name in self.active_process.materials:
            return False

        self.active_process.materials[name] = Material(
            name=name, category=category, is_active=True, rule_limits={}
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

    def update_material_category(self, mat_name: str, new_category: str):
        if self.active_process and mat_name in self.active_process.materials:
            self.active_process.materials[mat_name].category = new_category
            self.mark_dirty()

    def update_rule_limit(self, rule, attr: str, value: str):
        if not self.active_material:
            return

        if rule not in self.active_material.rule_limits:
            self.active_material.rule_limits[rule] = RuleLimit()

        setattr(self.active_material.rule_limits[rule], attr, value)
        self.mark_dirty()

    def update_active_rules(self, rules: list):
        if self.active_process:
            self.active_process.active_rules = rules
            self.mark_dirty()


# =============================================================================


class NumericValidationDelegate(QtWidgets.QStyledItemDelegate):
    """Validates that input is numeric or N/A, and manages UI unit stripping."""

    def createEditor(self, parent, option, index):
        if index.column() not in [1, 2]:
            return super().createEditor(parent, option, index)

        editor = QtWidgets.QLineEdit(parent)
        regex = QtCore.QRegularExpression(r"^-?\d*\.?\d+$|^(?i)N/A$")
        editor.setValidator(QtGui.QRegularExpressionValidator(regex, editor))
        return editor

    def setEditorData(self, editor, index):
        text = index.model().data(index, QtCore.Qt.ItemDataRole.EditRole)
        rule = index.model().index(index.row(), 0).data(QtCore.Qt.ItemDataRole.UserRole)

        if rule and getattr(rule, "unit", "") and text:
            text = str(text).replace(rule.unit, "").strip()

        editor.setText(text)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text(), QtCore.Qt.ItemDataRole.EditRole)


# =============================================================================


class ProcessTreeView(QtCore.QObject):
    selection_changed = QtCore.Signal(str)

    def __init__(self, tree: QtWidgets.QTreeWidget):
        super().__init__()
        self.tree = tree
        self.tree.setHeaderLabels(["Manufacturing Processes"])
        self.tree.setIndentation(20)
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
                process_node = QtWidgets.QTreeWidgetItem(cat_node)
                process_node.setText(0, process.name)
                process_node.setData(0, QtCore.Qt.ItemDataRole.UserRole, process.name)

        self.tree.expandAll()
        self.tree.blockSignals(False)

    def select_process(self, name: str):
        items = self.tree.findItems(name, QtCore.Qt.MatchFlag.MatchRecursive)
        if items:
            self.tree.setCurrentItem(items[0])

    def _on_selection(self):
        selected = self.tree.selectedItems()
        if selected:
            val = selected[0].data(0, QtCore.Qt.ItemDataRole.UserRole)
            if val:
                self.selection_changed.emit(val)


# =============================================================================


class RuleListView(QtCore.QObject):
    rules_changed = QtCore.Signal(list)
    edit_requested = QtCore.Signal(object)

    def __init__(self, list_widget: QtWidgets.QListWidget, search_field: QtWidgets.QLineEdit):
        super().__init__()
        self.rlist = list_widget
        self.search_field = search_field
        self._is_populating = False

        self.search_field.setClearButtonEnabled(True)
        self.search_field.setPlaceholderText("Search rules...")
        self.search_field.textChanged.connect(self.filter_rules)

    def populate(self, active_rules: list):
        self._is_populating = True
        self.rlist.clear()

        for rule in Rulebook:
            item = QtWidgets.QListWidgetItem(self.rlist)
            item.setSizeHint(QtCore.QSize(0, 36))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, rule)

            widget = RuleItemWidget(rule, rule in active_rules)

            widget.checkbox.toggled.connect(self._on_check_toggled)
            widget.edit_btn.clicked.connect(
                lambda checked=False, r=rule: self.edit_requested.emit(r)
            )

            self.rlist.setItemWidget(item, widget)

        self._is_populating = False
        self.filter_rules(self.search_field.text())

    def filter_rules(self, text: str):
        """Hides items that don't match the search string."""
        search_term = text.lower()

        for i in range(self.rlist.count()):
            item = self.rlist.item(i)
            rule = item.data(QtCore.Qt.ItemDataRole.UserRole)

            match = search_term in rule.label.lower()
            item.setHidden(not match)

    def _on_check_toggled(self):
        if self._is_populating:
            return
        active = []
        for i in range(self.rlist.count()):
            item = self.rlist.item(i)
            widget = self.rlist.itemWidget(item)
            if widget and widget.checkbox.isChecked():  # type: ignore
                active.append(item.data(QtCore.Qt.ItemDataRole.UserRole))
        self.rules_changed.emit(active)


# =============================================================================


class RuleItemWidget(QtWidgets.QWidget):
    """Updated with spacing/padding fix."""

    def __init__(self, rule, is_active: bool, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)

        layout.setSpacing(10)
        layout.setContentsMargins(10, 2, 10, 2)

        self.checkbox = QtWidgets.QCheckBox()
        self.checkbox.setChecked(is_active)

        self.label = QtWidgets.QLabel(rule.label)

        self.edit_btn = QtWidgets.QPushButton("Edit")
        self.edit_btn.setFixedWidth(65)
        self.edit_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

        layout.addWidget(self.checkbox)
        layout.addWidget(self.label)
        layout.addStretch()
        layout.addWidget(self.edit_btn)


# =============================================================================


class ParameterEdit(QtWidgets.QPlainTextEdit):
    """A text editor that suggests {placeholders} when '{' is typed."""

    def __init__(self, placeholders, parent=None):
        super().__init__(parent)
        self.completer = QtWidgets.QCompleter(placeholders, self)
        self.completer.setWidget(self)
        self.completer.setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)
        self.completer.activated.connect(self.insert_completion)

    def insert_completion(self, completion):
        """Inserts the selected parameter and closes the brace."""
        tc = self.textCursor()
        tc.insertText(completion + "}")
        self.setTextCursor(tc)

    def keyPressEvent(self, event):
        if self.completer.popup().isVisible():
            if event.key() in (
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
    """Dialog to input custom Warning and Error messages with internal titles."""

    def __init__(self, process_name: str, rule_name: str, feedback: RuleFeedback, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit Feedback: {rule_name}")
        self.setMinimumWidth(480)
        self.setMinimumHeight(450)

        layout = QtWidgets.QVBoxLayout(self)
        style = self.style()

        header_container = QtWidgets.QWidget()
        header_layout = QtWidgets.QVBoxLayout(header_container)
        header_layout.setContentsMargins(0, 0, 0, 10)

        context_text = f"{rule_name} ({process_name})"
        process_lbl = QtWidgets.QLabel(context_text)
        process_lbl.setStyleSheet("font-size: 14px; font-weight: bold;")

        header_layout.addWidget(process_lbl)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        line.setStyleSheet("")

        layout.addWidget(header_container)
        layout.addWidget(line)
        layout.addSpacing(10)

        warn_header_layout = QtWidgets.QHBoxLayout()
        warn_icon = QtWidgets.QLabel()
        warn_icon.setPixmap(QtGui.QIcon(":/icons/dfm_warning.svg").pixmap(16, 16))
        warn_label = QtWidgets.QLabel("Warning Message")
        warn_label.setStyleSheet("font-weight: bold;")
        warn_header_layout.addWidget(warn_icon)
        warn_header_layout.addWidget(warn_label)
        warn_header_layout.addStretch()
        layout.addLayout(warn_header_layout)

        params = ["measured", "limit", "target"]

        self.warn_edit = ParameterEdit(params, self)
        self.warn_edit.setPlainText(feedback.warning_msg)
        self.warn_edit.setPlaceholderText(
            "e.g., Feature is too small ({measured}), minimum required is {limit}"
        )
        layout.addWidget(self.warn_edit)

        layout.addSpacing(10)

        err_header_layout = QtWidgets.QHBoxLayout()
        err_icon = QtWidgets.QLabel()
        err_icon.setPixmap(QtGui.QIcon(":/icons/dfm_error.svg").pixmap(16, 16))
        err_label = QtWidgets.QLabel("Error Message")
        err_label.setStyleSheet("font-weight: bold;")
        err_header_layout.addWidget(err_icon)
        err_header_layout.addWidget(err_label)
        err_header_layout.addStretch()
        layout.addLayout(err_header_layout)

        self.err_edit = ParameterEdit(params, self)
        self.err_edit.setPlainText(feedback.error_msg)
        self.err_edit.setPlaceholderText(
            "e.g., Critical failure: {measured} is far below the {limit} limit."
        )
        layout.addWidget(self.err_edit)

        help_container = QtWidgets.QWidget()
        help_container.setObjectName("HelpContainer")
        help_container.setStyleSheet("""
            #HelpContainer {
                border: 1px solid palette(mid);
                border-radius: 4px;
            }
        """)

        help_layout = QtWidgets.QGridLayout(help_container)
        help_layout.setContentsMargins(10, 10, 10, 10)
        help_layout.setSpacing(6)

        help_title = QtWidgets.QLabel("Available Parameters")
        help_title.setStyleSheet("font-weight: bold; margin-bottom: 2px;")
        help_layout.addWidget(help_title, 0, 0, 1, 2)

        placeholders = [
            ("<b>{measured}</b>", "Current measured value"),
            ("<b>{limit}</b>", "The threshold value"),
            ("<b>{target}</b>", "The ideal target value"),
        ]

        for i, (code, desc) in enumerate(placeholders):
            row = i + 1
            help_layout.addWidget(QtWidgets.QLabel(code), row, 0)
            help_layout.addWidget(QtWidgets.QLabel(desc), row, 1)

        help_layout.setColumnStretch(1, 1)
        layout.addSpacing(10)
        layout.addWidget(help_container)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def get_data(self) -> tuple[str, str]:
        return self.warn_edit.toPlainText().strip(), self.err_edit.toPlainText().strip()

    def accept(self):
        warn, err = self.get_data()
        allowed = {"measured", "limit", "target"}
        import re

        found_tags = re.findall(r"\{(.*?)\}", warn + err)

        for tag in found_tags:
            if tag not in allowed:
                QtWidgets.QMessageBox.warning(
                    self, "Invalid Tag", f"The tag {{{tag}}} is not recognized."
                )
                return
        super().accept()


# =============================================================================


class MaterialTableView(QtCore.QObject):
    selection_changed = QtCore.Signal(str)
    category_changed = QtCore.Signal(str, str)

    def __init__(self, table: QtWidgets.QTableWidget):
        super().__init__()
        self.table = table
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Material", "Category"])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)

        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.itemChanged.connect(self._on_item_changed)
        self._is_populating = False

    def populate(self, materials: dict):
        self._is_populating = True
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        for name, mat in materials.items():
            row = self.table.rowCount()
            self.table.insertRow(row)

            name_item = QtWidgets.QTableWidgetItem(name)
            name_item.setFlags(name_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            cat_item = QtWidgets.QTableWidgetItem(mat.category)

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, cat_item)

        self.table.blockSignals(False)
        self._is_populating = False

    def select_material(self, name: str):
        items = self.table.findItems(name, QtCore.Qt.MatchFlag.MatchExactly)
        if items:
            self.table.setCurrentItem(items[0])

    def get_selected_material(self) -> Optional[str]:
        items = self.table.selectedItems()
        return self.table.item(items[0].row(), 0).text() if items else None  # type: ignore

    def _on_selection(self):
        if self._is_populating:
            return
        mat_name = self.get_selected_material()
        self.selection_changed.emit(mat_name if mat_name else "")

    def _on_item_changed(self, item):
        if self._is_populating or item.column() != 1:
            return
        name_item = self.table.item(item.row(), 0)
        if name_item:
            self.category_changed.emit(name_item.text(), item.text().strip())


# =============================================================================


class VisualizerBar(QtWidgets.QFrame):
    """Paints three solid colour zones with boundary labels overlaid at the transitions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)
        self._left_label = ""
        self._right_label = ""
        self._left_label_color = "#E24B4A"
        self._right_label_color = "#639922"
        self._zones: list[tuple[str, str]] = []

    def set_zones(
        self,
        zones: list[tuple[str, str]],
        left_label: str,
        right_label: str,
    ):
        self._zones = zones
        self._left_label = left_label
        self._right_label = right_label
        self.update()

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QColor, QFont, QPen

        if not self._zones:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        zone_w = w / len(self._zones)

        # Draw solid colour zones
        for i, (label, color) in enumerate(self._zones):
            x = int(i * zone_w)
            rect = QtCore.QRect(x, 0, int(zone_w) + 1, h)
            painter.fillRect(rect, QColor(color))

            # Zone label centred
            painter.setPen(QPen(QColor(255, 255, 255, 200)))
            f = QFont(painter.font())
            f.setPointSize(8)
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, label)

        # Draw boundary dividers and labels overlaid at transitions
        for i in range(1, len(self._zones)):
            x = int(i * zone_w)
            label = self._left_label if i == 1 else self._right_label

            # Divider line
            painter.setPen(QPen(QColor(255, 255, 255, 80), 1))
            painter.drawLine(x, 0, x, h)

            # Label pill overlaid on the divider
            f2 = QFont(painter.font())
            f2.setPointSize(8)
            f2.setBold(True)
            painter.setFont(f2)
            fm = painter.fontMetrics()
            text_w = fm.horizontalAdvance(label) + 12
            text_h = 18
            pill_x = x - text_w // 2
            pill_y = (h - text_h) // 2
            pill_rect = QtCore.QRect(pill_x, pill_y, text_w, text_h)

            painter.setPen(QPen(QtCore.Qt.PenStyle.NoPen))
            painter.setBrush(QColor(0, 0, 0, 130))
            painter.drawRoundedRect(pill_rect, 4, 4)

            painter.setPen(QPen(QColor(255, 255, 255, 230)))
            painter.drawText(pill_rect, QtCore.Qt.AlignmentFlag.AlignCenter, label)

        painter.end()


# =============================================================================


class VisualizerView(QtCore.QObject):
    def __init__(self, container: QtWidgets.QWidget):
        super().__init__()
        self.container = container
        self._setup_ui()

    def _setup_ui(self):
        layout = self.container.layout()
        if layout is None:
            layout = QtWidgets.QVBoxLayout(self.container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

        self.bar = VisualizerBar(self.container)
        layout.addWidget(self.bar)
        self.container.setFixedHeight(32)

    def update_display(self, rule, target: Optional[float], limit: Optional[float]):
        if not rule or getattr(rule, "is_binary", False) or target is None or limit is None:
            self.container.hide()
            return

        self.container.show()
        unit = getattr(rule, "unit", "")
        is_min_rule = rule.comparison == "min"

        if is_min_rule:
            zones = [
                ("Error", "#E24B4A"),
                ("Warning", "#D4900A"),
                ("Success", "#639922"),
            ]
            self.bar.set_zones(zones, f"{limit}{unit}", f"{target}{unit}")
        else:
            zones = [
                ("Success", "#639922"),
                ("Warning", "#D4900A"),
                ("Error", "#E24B4A"),
            ]
            self.bar.set_zones(zones, f"{target}{unit}", f"{limit}{unit}")

    def hide(self):
        self.container.hide()


# =============================================================================


class MaterialEditView(QtCore.QObject):
    limit_changed = QtCore.Signal(object, str, str)
    selection_changed = QtCore.Signal(object, object, object)

    def __init__(self, table: QtWidgets.QTableWidget):
        super().__init__()
        self.table = table
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Rule", "Target", "Limit"])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)

        self.table.cellChanged.connect(self._on_cell_changed)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self.delegate = NumericValidationDelegate(self.table)
        self.table.setItemDelegate(self.delegate)

    def populate(self, material: Material, default_material: Material, active_rules: list):
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        for rule in active_rules:
            row = self.table.rowCount()
            self.table.insertRow(row)

            rule_item = QtWidgets.QTableWidgetItem(rule.label)
            rule_item.setData(QtCore.Qt.ItemDataRole.UserRole, rule)
            rule_item.setFlags(rule_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, rule_item)

            limit_data = material.rule_limits.get(rule)
            def_limit = default_material.rule_limits.get(rule) if default_material else None

            for col, attr in enumerate(["target", "limit"], start=1):
                if getattr(rule, "is_binary", False):
                    table_item = QtWidgets.QTableWidgetItem("N/A")
                    table_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
                    muted = self.table.palette().text().color()
                    muted.setAlphaF(0.4)
                    table_item.setForeground(QtGui.QBrush(muted))
                else:
                    val = getattr(limit_data, attr) if limit_data else ""
                    def_val = getattr(def_limit, attr) if def_limit else ""

                    is_custom = bool(val and val != def_val and material.name != "Default")
                    display_text = val if val or material.name == "Default" else def_val

                    if (
                        display_text
                        and str(display_text).upper() not in ["N/A", ""]
                        and getattr(rule, "unit", "")
                    ):
                        display_text = f"{display_text}{rule.unit}"

                    table_item = QtWidgets.QTableWidgetItem(str(display_text))
                    if is_custom:
                        table_item.setForeground(QtGui.QColor("#D4900A"))

                self.table.setItem(row, col, table_item)

            if not getattr(rule, "is_binary", False):
                self._validate_row_values(row, rule)

        self.table.blockSignals(False)
        self._on_selection_changed()

    def clear(self):
        self.table.setRowCount(0)

    def _on_cell_changed(self, row, col):
        if col not in [1, 2]:
            return

        item = self.table.item(row, col)
        rule_item = self.table.item(row, 0)
        if not item or not rule_item:
            return

        rule = rule_item.data(QtCore.Qt.ItemDataRole.UserRole)
        cleaned_val = item.text().replace(getattr(rule, "unit", ""), "").strip()

        attr = "target" if col == 1 else "limit"
        self.limit_changed.emit(rule, attr, cleaned_val)

        self.table.blockSignals(True)
        if cleaned_val and cleaned_val.upper() != "N/A" and getattr(rule, "unit", ""):
            item.setText(f"{cleaned_val}{rule.unit}")
        item.setForeground(QtGui.QColor("#ffaa00"))
        self.table.blockSignals(False)

        self._validate_row_values(row, rule)
        self._on_selection_changed()

    def _get_numeric_val(self, row, col, rule) -> Optional[float]:
        item = self.table.item(row, col)
        if not item or not item.text():
            return None
        try:
            return float(item.text().replace(getattr(rule, "unit", ""), "").strip())
        except ValueError:
            return None

    def _validate_row_values(self, row, rule):
        t = self._get_numeric_val(row, 1, rule)
        l = self._get_numeric_val(row, 2, rule)

        is_invalid = False
        if t is not None and l is not None:
            is_min_rule = rule.comparison == "min"
            if is_min_rule and l > t:
                is_invalid = True
            elif not is_min_rule and l < t:
                is_invalid = True

        color = QtGui.QColor("#442222") if is_invalid else QtGui.QColor(0, 0, 0, 0)
        for col_idx in range(self.table.columnCount()):
            item = self.table.item(row, col_idx)
            if item:
                item.setBackground(color)

    def _on_selection_changed(self):
        selected = self.table.selectedItems()
        if not selected:
            self.selection_changed.emit(None, None, None)
            return

        row = selected[0].row()
        rule_item = self.table.item(row, 0)
        if not rule_item:
            self.selection_changed.emit(None, None, None)
            return

        rule = rule_item.data(QtCore.Qt.ItemDataRole.UserRole)
        t = self._get_numeric_val(row, 1, rule)
        l = self._get_numeric_val(row, 2, rule)
        self.selection_changed.emit(rule, t, l)


# =============================================================================


class AddProcessDialog(QtWidgets.QDialog):
    def __init__(self, categories: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Process")
        self.setMinimumWidth(350)

        layout = QtWidgets.QFormLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setMinimumHeight(32)

        self.cat_combo = QtWidgets.QComboBox()
        self.cat_combo.setEditable(True)
        self.cat_combo.setMinimumHeight(32)
        self.cat_combo.setPlaceholderText("Select or type a category...")
        self.cat_combo.addItems(sorted(list(set(categories))))

        layout.addRow("Process Name:", self.name_edit)
        layout.addRow("Category:", self.cat_combo)

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


class AddMaterialDialog(QtWidgets.QDialog):
    def __init__(self, existing_categories: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Material")
        self.setMinimumWidth(350)

        layout = QtWidgets.QFormLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setMinimumHeight(32)
        self.name_edit.setPlaceholderText("e.g., Aluminum 6061")

        self.cat_combo = QtWidgets.QComboBox()
        self.cat_combo.setEditable(True)
        self.cat_combo.setMinimumHeight(32)

        unique_cats = sorted(list(set(existing_categories)))
        self.cat_combo.addItems(unique_cats)
        if not unique_cats:
            self.cat_combo.setPlaceholderText("Type a category...")

        layout.addRow("Material Name:", self.name_edit)
        layout.addRow("Category:", self.cat_combo)

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


class ProcessLibraryController(QtCore.QObject):
    def __init__(self, view: "ProcessLibrary", model: ProcessLibraryModel):
        super().__init__()
        self.view = view
        self.model = model
        self._connect_signals()
        self._refresh_process_tree()

    def _connect_signals(self):
        self.view.save_requested.connect(self.on_save)
        self.view.reject_requested.connect(self.on_reject)
        self.view.add_process_requested.connect(self.on_add_process)
        self.view.add_material_requested.connect(self.on_add_material)
        self.view.delete_material_requested.connect(self.on_delete_material)

        self.view.tree_view.selection_changed.connect(self.on_process_selected)
        self.view.rule_view.rules_changed.connect(self.on_rules_changed)
        self.view.material_view.selection_changed.connect(self.on_material_selected)
        self.view.material_view.category_changed.connect(self.on_material_category_changed)
        self.view.material_edit_view.limit_changed.connect(self.on_limit_changed)
        self.view.material_edit_view.selection_changed.connect(
            self.view.visualizer_view.update_display
        )
        self.view.rule_view.edit_requested.connect(self.on_edit_feedback)

    def _update_dirty_state(self):
        self.view.set_dirty_title(self.model.is_dirty)

    def _refresh_process_tree(self):
        self.view.tree_view.populate(self.model.get_categorized_processes())

    def on_process_selected(self, name: str):
        process = self.model.set_active_process(name)
        if not process:
            return

        self.view.set_process_label(process.name, process.description)
        self.view.rule_view.populate(process.active_rules)
        self.view.material_view.populate(process.materials)
        self.view.material_edit_view.clear()
        self.view.set_material_edit_title("Select a Material")

    def on_material_selected(self, name: str):
        if not name:
            self.view.material_edit_view.clear()
            self.view.set_material_edit_title("Select a Material")
            return

        material = self.model.set_active_material(name)
        if not material or not self.model.active_process:
            return

        self.view.set_material_edit_title(f"Editing {material.name}")
        mats = list(self.model.active_process.materials.values())
        default_material = mats[0] if mats else material

        self.view.material_edit_view.populate(
            material, default_material, self.model.active_process.active_rules
        )

    def on_rules_changed(self, active_rules: list):
        self.model.update_active_rules(active_rules)
        self._update_dirty_state()
        if self.model.active_material:
            self.on_material_selected(self.model.active_material.name)
        else:
            self.view.material_edit_view.clear()

    def on_edit_feedback(self, rule_enum: Rulebook):
        process = self.model.active_process
        if not process:
            return

        current_feedback = process.rule_feedback.get(rule_enum)

        if not current_feedback:
            current_feedback = RuleFeedback()
            process.rule_feedback[rule_enum] = current_feedback

        dlg = EditFeedbackDialog(
            process.name,
            rule_enum.label,
            current_feedback,
            self.view,
        )

        if dlg.exec():
            warn, err = dlg.get_data()

            current_feedback.warning_msg = warn
            current_feedback.error_msg = err

            self._update_dirty_state()

    def on_material_category_changed(self, mat_name: str, new_category: str):
        self.model.update_material_category(mat_name, new_category)
        self._update_dirty_state()

    def on_limit_changed(self, rule, attr: str, value: str):
        self.model.update_rule_limit(rule, attr, value)
        self._update_dirty_state()

    def on_add_process(self):
        categories = self.model.registry.get_categories()
        dlg = AddProcessDialog(categories, self.view)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            name, category = dlg.get_data()
            if not name or not category:
                QtWidgets.QMessageBox.warning(
                    self.view, "Invalid Input", "Both name and category are required."
                )
                return

            if self.model.add_process(name, category):
                self._update_dirty_state()
                self._refresh_process_tree()
                self.view.tree_view.select_process(name)
            else:
                QtWidgets.QMessageBox.warning(
                    self.view, "Duplicate", f"Process '{name}' already exists."
                )

    def on_add_material(self):
        process = self.model.active_process
        if not process:
            QtWidgets.QMessageBox.warning(self.view, "No Process", "Please select a process first.")
            return

        existing_cats = [m.category for m in process.materials.values()]
        dlg = AddMaterialDialog(existing_cats, self.view)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            name, category = dlg.get_data()
            if not name or not category:
                QtWidgets.QMessageBox.warning(
                    self.view, "Invalid Input", "Name and Category are required."
                )
                return

            if self.model.add_material(name, category):
                self._update_dirty_state()
                self.view.material_view.populate(process.materials)
                self.view.material_view.select_material(name)
            else:
                QtWidgets.QMessageBox.warning(
                    self.view, "Duplicate", f"Material '{name}' already exists."
                )

    def on_delete_material(self):
        mat_name = self.view.material_view.get_selected_material()
        if not mat_name:
            return

        if mat_name == "Default":
            QtWidgets.QMessageBox.critical(
                self.view, "Error", "The 'Default' material cannot be deleted."
            )
            return

        confirm = QtWidgets.QMessageBox.question(
            self.view,
            "Delete Material",
            f"Are you sure you want to delete '{mat_name}'?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )

        if confirm == QtWidgets.QMessageBox.StandardButton.Yes:
            if self.model.delete_material(mat_name):
                self._update_dirty_state()
                self.view.material_view.populate(self.model.active_process.materials)
                self.view.material_edit_view.clear()
                self.view.set_material_edit_title("Select a Material")

    def on_save(self):
        try:
            self.model.save()
            self._update_dirty_state()
            self.view.accept()
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self.view, "Save Error", f"Failed to save process data: {str(e)}"
            )

    def on_reject(self):
        if self.model.is_dirty:
            confirm = QtWidgets.QMessageBox.question(
                self.view,
                "Unsaved Changes",
                "You have unsaved changes. Are you sure you want to discard them?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if confirm == QtWidgets.QMessageBox.StandardButton.No:
                return
        self.view.super_reject()


# =============================================================================


class ProcessLibrary(QtWidgets.QDialog):
    """The main QDialog bridging FreeCAD into the MVC architecture."""

    save_requested = QtCore.Signal()
    reject_requested = QtCore.Signal()
    add_process_requested = QtCore.Signal()
    add_material_requested = QtCore.Signal()
    delete_material_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent or Gui.getMainWindow())
        self.form = Gui.PySideUic.loadUi(":/ui/process_library.ui")  # type: ignore
        self.setWindowTitle("DFM Process Library")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.form)
        layout.setContentsMargins(0, 0, 0, 0)
        self.resize(1100, 800)

        self.form.hSplitter.setStretchFactor(0, 1)
        self.form.hSplitter.setStretchFactor(1, 2)

        self.form.visualizer_container.setMinimumHeight(40)
        self.form.visualizer_container.setStyleSheet(
            "background-color: #1a1a1a; border-radius: 4px;"
        )
        self.form.lProcess.setText("Select a Process")

        self.tree_view = ProcessTreeView(self.form.twProcess)
        self.rule_view = RuleListView(self.form.lwRules, self.form.leSearchRules)
        self.material_view = MaterialTableView(self.form.twMaterials)
        self.material_edit_view = MaterialEditView(self.form.twMaterialEdit)
        self.visualizer_view = VisualizerView(self.form.visualizer_container)

        self.visualizer_view.hide()

        self.form.pbNewProcess.clicked.connect(self.add_process_requested.emit)
        self.form.pbMaterialAdd.clicked.connect(self.add_material_requested.emit)
        self.form.pbMaterialDelete.clicked.connect(self.delete_material_requested.emit)
        self.form.buttonBox.accepted.connect(self.save_requested.emit)
        self.form.buttonBox.rejected.connect(self.reject_requested.emit)

        self.model = ProcessLibraryModel()
        self.controller = ProcessLibraryController(self, self.model)

    def set_dirty_title(self, is_dirty: bool):
        title = self.windowTitle().replace(" *", "")
        if is_dirty:
            title += " *"
        self.setWindowTitle(title)

    def set_process_label(self, name: str, description: str):
        self.form.lProcess.setText(name if name else "Select a Process")
        if hasattr(self.form, "lblDescription"):
            self.form.lblDescription.setText(description)

    def set_material_edit_title(self, text: str):
        self.form.gbMaterialEdit.setTitle(text)

    def reject(self):
        self.reject_requested.emit()

    def super_reject(self):
        super().reject()


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
