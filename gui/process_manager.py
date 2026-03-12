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

from dfm.processes.process import Material, Process
from dfm.registries.process_registry import ProcessRegistry
from dfm.rules import Rulebook

from . import DFM_rc


class ProcessManager(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent or Gui.getMainWindow())
        self.form = Gui.PySideUic.loadUi(":/ui/process_manager.ui")  # type: ignore
        self.setWindowTitle("DFM Process Manager")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.form)
        layout.setContentsMargins(0, 0, 0, 0)
        self.resize(1000, 800)
        self.form.hSplitter.setStretchFactor(1, 3)

        self.registry = ProcessRegistry.get_instance()
        self.registry.discover_processes()

        self.is_dirty = False

        self.form.lProcess.setText("Select a Process")

        # Controllers no longer need the registry reference
        self.tree_ctrl = ProcessTreeController(self.form.twProcess, self.registry)
        self.rules_ctrl = RuleListController(self.form.lwRules)
        self.mat_ctrl = MaterialController(self.form.twMaterials)
        self.edit_ctrl = MaterialEditController(self.form.twMaterialEdit)

        self.form.twProcess.itemSelectionChanged.connect(self.on_process_selected)
        self.form.lwRules.itemChanged.connect(self.on_rule_toggled)
        self.form.twMaterials.itemSelectionChanged.connect(self.on_material_selected)
        self.form.pbMaterialAdd.clicked.connect(self.on_material_add)
        self.form.pbMaterialDelete.clicked.connect(self.on_material_delete)
        self.form.pbNewProcess.clicked.connect(self.on_process_add)
        self.form.twMaterialEdit.cellChanged.connect(self.mark_dirty)
        self.form.twMaterials.itemChanged.connect(self.mark_dirty)
        self.form.twMaterials.itemChanged.connect(self.on_material_category_changed)
        self.form.buttonBox.accepted.connect(self.on_save)
        self.form.buttonBox.rejected.connect(self.reject)

    def mark_dirty(self):
        if not self.is_dirty:
            self.is_dirty = True
            if not self.windowTitle().endswith(" *"):
                self.setWindowTitle(self.windowTitle() + " *")

    def on_save(self):
        """Finalize changes and write to disk via the registry."""
        try:
            self.registry.save_all_processes()

            # --- Reset Dirty State ---
            self.is_dirty = False
            # Remove the asterisk from the title
            title = self.windowTitle()
            if title.endswith(" *"):
                self.setWindowTitle(title[:-2])

            self.accept()

        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, "Save Error", f"Failed to save process data: {str(e)}"
            )

    def reject(self):
        """Overrides the default close behavior to check for unsaved changes."""
        if self.is_dirty:
            confirm = QtWidgets.QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes. Are you sure you want to discard them?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,  # Default to No for safety
            )

            if confirm == QtWidgets.QMessageBox.StandardButton.No:
                return

        super().reject()

    def on_process_selected(self):
        process_name = self.tree_ctrl.get_selected_name()
        if not process_name:
            return

        self.form.lProcess.setText(process_name)

        process = self.registry.get_process_by_name(process_name)
        if process:
            # Revert: Populate rules globally for the process
            self.rules_ctrl.populate(process)
            self.mat_ctrl.populate(process)

            if hasattr(self.form, "lblDescription"):
                self.form.lblDescription.setText(process.description)

    def on_process_add(self):
        """Creates a new process and adds it to the registry."""
        categories = self.registry.get_categories()
        dlg = AddProcessDialog(categories, self)

        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            name, category = dlg.get_data()

            if not name or not category:
                QtWidgets.QMessageBox.warning(
                    self, "Invalid Input", "Both name and category are required."
                )
                return

            # Check for existing process to avoid overwriting
            if self.registry.get_process_by_name(name):
                QtWidgets.QMessageBox.warning(
                    self, "Duplicate", f"Process '{name}' already exists."
                )
                return

            # Create the new process object
            # Note: You'll need to ensure 'Process' is imported or defined
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

            # Add to registry and refresh the tree
            self.mark_dirty()
            self.registry.add_process(new_process)
            self.tree_ctrl.populate()

            # Optionally select the new process in the tree
            items = self.form.twProcess.findItems(name, QtCore.Qt.MatchFlag.MatchRecursive)
            if items:
                self.form.twProcess.setCurrentItem(items[0])

    def on_rule_toggled(self, item: QtWidgets.QListWidgetItem):
        self.mark_dirty()
        process_name = self.tree_ctrl.get_selected_name()
        if not process_name:
            return
        process = self.registry.get_process_by_name(process_name)

        if process:
            # Sync back to Process dataclass
            process.active_rules = self.rules_ctrl.get_active_rules()
            # Refresh the edit table for the currently selected material
            self.on_material_selected()

    def on_material_selected(self):
        items = self.form.twMaterials.selectedItems()
        if not items:
            self.form.twMaterialEdit.setRowCount(0)
            return

        row = items[0].row()
        mat_name = self.form.twMaterials.item(row, 0).text()
        self.form.gbMaterialEdit.setTitle(f"Editing {mat_name}")

        process_name = self.tree_ctrl.get_selected_name()
        if not process_name:
            return

        process = self.registry.get_process_by_name(process_name)
        # Check if process exists before accessing .materials
        if process and mat_name in process.materials:
            material = process.materials[mat_name]

            # Use a safe fallback for default_material to avoid errors on empty dicts
            mats = list(process.materials.values())
            default_material = mats[0] if mats else material

            self.edit_ctrl.populate(material, default_material, process.active_rules)

    def on_material_category_changed(self, item):
        """Handles manual edits to the material category column."""
        process_name = self.tree_ctrl.get_selected_name()
        if not process_name:
            return

        process = self.registry.get_process_by_name(process_name)
        if process:
            # Sync the text back to the dataclass
            self.mat_ctrl.sync_material_category(item, process)
            # Mark the window as dirty so we can save
            self.mark_dirty()

    def on_material_add(self):
        process_name = self.tree_ctrl.get_selected_name()
        if not process_name:
            QtWidgets.QMessageBox.warning(self, "No Process", "Please select a process first.")
            return

        process = self.registry.get_process_by_name(process_name)
        if not process:
            return

        # Get unique existing categories for the dropdown
        existing_cats = [m.category for m in process.materials.values()]

        dlg = AddMaterialDialog(existing_cats, self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            name, category = dlg.get_data()

            if not name or not category:
                QtWidgets.QMessageBox.warning(
                    self, "Invalid Input", "Name and Category are required."
                )
                return

            if name in process.materials:
                QtWidgets.QMessageBox.warning(
                    self, "Duplicate", f"Material '{name}' already exists."
                )
                return

            # Create the material with the user's data
            new_mat = Material(
                name=name,
                category=category,
                is_active=True,
                rule_limits={},
            )

            process.materials[name] = new_mat
            self.mat_ctrl.populate(process)
            self.mark_dirty()

            # Select the newly added material in the table
            items = self.form.twMaterials.findItems(name, QtCore.Qt.MatchFlag.MatchExactly)
            if items:
                self.form.twMaterials.setCurrentItem(items[0])

    def on_material_delete(self):
        items = self.form.twMaterials.selectedItems()
        if not items:
            return

        row = items[0].row()
        mat_name = self.form.twMaterials.item(row, 0).text()

        if mat_name == "Default":
            QtWidgets.QMessageBox.critical(
                self, "Error", "The 'Default' material cannot be deleted."
            )
            return

        confirm = QtWidgets.QMessageBox.question(
            self,
            "Delete Material",
            f"Are you sure you want to delete '{mat_name}'?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )

        if confirm == QtWidgets.QMessageBox.StandardButton.Yes:
            process_name = self.tree_ctrl.get_selected_name()
            if process_name:  # Guard against Optional[str]
                process = self.registry.get_process_by_name(process_name)
                if process and mat_name in process.materials:
                    del process.materials[mat_name]
                    self.mark_dirty()
                    self.mat_ctrl.populate(process)
                    self.form.twMaterialEdit.setRowCount(0)


class AddProcessDialog(QtWidgets.QDialog):
    def __init__(self, categories: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Process")
        self.setMinimumWidth(350)

        layout = QtWidgets.QFormLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        # Height for better visibility
        widget_height = 32

        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setMinimumHeight(widget_height)

        # Editable ComboBox replaces the 'Custom' logic
        self.cat_combo = QtWidgets.QComboBox()
        self.cat_combo.setEditable(True)
        self.cat_combo.setMinimumHeight(widget_height)
        self.cat_combo.setPlaceholderText("Select or type a category...")

        # Populate with existing categories
        self.cat_combo.addItems(sorted(list(set(categories))))

        layout.addRow("Process Name:", self.name_edit)
        layout.addRow("Category:", self.cat_combo)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )

        # Match button height to inputs
        for button in self.buttons.buttons():
            button.setMinimumHeight(widget_height)

        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)

    def get_data(self) -> tuple[str, str]:
        """Returns the trimmed name and category text."""
        name = self.name_edit.text().strip()
        category = self.cat_combo.currentText().strip()
        return name, category


class ProcessTreeController:
    def __init__(self, tree: QtWidgets.QTreeWidget, registry: ProcessRegistry):
        self.tree = tree
        self.registry = registry
        self.tree.setHeaderLabels(["Manufacturing Processes"])
        self.tree.setIndentation(20)
        self.populate()

    def populate(self):
        self.tree.clear()
        categories = self.registry.get_categories()
        for cat_name in categories:
            cat_node = QtWidgets.QTreeWidgetItem(self.tree)
            cat_node.setText(0, cat_name)
            cat_node.setFlags(cat_node.flags() & ~QtCore.Qt.ItemFlag.ItemIsSelectable)

            font = cat_node.font(0)
            font.setBold(True)
            cat_node.setFont(0, font)

            processes = self.registry.get_processes_for_category(cat_name)
            for process in processes:
                process_node = QtWidgets.QTreeWidgetItem(cat_node)
                process_node.setText(0, process.name)
                process_node.setData(0, QtCore.Qt.ItemDataRole.UserRole, process.name)
        self.tree.expandAll()

    def get_selected_name(self) -> Optional[str]:
        selected = self.tree.selectedItems()
        return selected[0].data(0, QtCore.Qt.ItemDataRole.UserRole) if selected else None


class RuleListController:
    def __init__(self, rlist: QtWidgets.QListWidget):
        self.rlist = rlist

    def populate(self, process: Process):
        # Blocking signals prevents on_rule_toggled from firing while we populate
        self.rlist.blockSignals(True)
        self.rlist.clear()
        for rule in Rulebook:
            item = QtWidgets.QListWidgetItem(self.rlist)
            item.setText(rule.label)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, rule)

            state = (
                QtCore.Qt.CheckState.Checked
                if rule in process.active_rules
                else QtCore.Qt.CheckState.Unchecked
            )
            item.setCheckState(state)
        self.rlist.blockSignals(False)

    def get_active_rules(self) -> list[Rulebook]:
        active = []
        for i in range(self.rlist.count()):
            item = self.rlist.item(i)
            if item.checkState() == QtCore.Qt.CheckState.Checked:
                active.append(item.data(QtCore.Qt.ItemDataRole.UserRole))
        return active

    def populate_for_material(self, material):
        self.rlist.blockSignals(True)
        self.rlist.clear()

        # Always loop through the FULL Rulebook
        for rule in Rulebook:
            item = QtWidgets.QListWidgetItem(self.rlist)
            item.setText(rule.label)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, rule)

            # Set the checkbox state based on this material's active rules
            # Make sure it is explicitly UserCheckable
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)

            is_active = rule in material.active_rules
            state = QtCore.Qt.CheckState.Checked if is_active else QtCore.Qt.CheckState.Unchecked
            item.setCheckState(state)

        self.rlist.blockSignals(False)


class MaterialController:
    def __init__(self, table: QtWidgets.QTableWidget):
        self.table = table
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Material", "Category"])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)

    def populate(self, process: Process):
        self.table.blockSignals(True)  # Prevent feedback loops
        self.table.setRowCount(0)
        for name, mat in process.materials.items():
            row = self.table.rowCount()
            self.table.insertRow(row)

            # Name Item (Non-editable for now to maintain dict keys)
            name_item = QtWidgets.QTableWidgetItem(name)
            name_item.setFlags(name_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)

            # Category Item (Editable)
            cat_item = QtWidgets.QTableWidgetItem(mat.category)

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, cat_item)
        self.table.blockSignals(False)

    def sync_material_category(self, item: QtWidgets.QTableWidgetItem, process: Process):
        """Updates the material's category in the process object when the table is edited."""
        if item.column() != 1:  # Only column 1 is the Category
            return

        row = item.row()
        name_item = self.table.item(row, 0)
        if not name_item:
            return

        mat_name = name_item.text()
        new_category = item.text().strip()

        if mat_name in process.materials:
            process.materials[mat_name].category = new_category


class MaterialEditController:
    def __init__(self, table: QtWidgets.QTableWidget):
        self.table = table
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Rule", "Target", "Warning", "Error"])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.cellChanged.connect(self.on_cell_changed)

        self.active_material: Optional[Material] = None
        self.default_material: Optional[Material] = None

    def on_cell_changed(self, row, column):
        # Only listen to Target, Warning, Error columns
        if column not in [1, 2, 3] or not self.active_material or not self.default_material:
            return

        item = self.table.item(row, column)
        rule_item = self.table.item(row, 0)
        if not item or not rule_item:
            return

        rule = rule_item.data(QtCore.Qt.ItemDataRole.UserRole)
        input_text = item.text().strip()

        # 1. Map column to attribute
        attr_map = {1: "target", 2: "warning", 3: "error"}
        attr_name = attr_map[column]

        # 2. Get/Create RuleLimit for the active material
        from dfm.processes.process import RuleLimit

        if rule not in self.active_material.rule_limits:
            self.active_material.rule_limits[rule] = RuleLimit()

        limit_obj = self.active_material.rule_limits[rule]

        # 3. Handle Empty / Inheritance Reversion
        if not input_text or input_text.upper() in ["N/A", "NONE", "-"]:
            setattr(limit_obj, attr_name, "")
            # If this is a specific material, it will now inherit from Default again
        else:
            # Add unit if missing for numeric rules
            if rule.unit and not any(input_text.endswith(u) for u in ["mm", "°", "%"]):
                input_text = f"{input_text}{rule.unit}"

            # Write to memory
            setattr(limit_obj, attr_name, input_text)

        # 4. Refresh View Logic
        self.table.blockSignals(True)

        # Determine inheritance status for coloring
        is_default_mat = self.active_material.name == "Default"

        # If we updated the 'Default' material, we should ideally refresh everything
        # so other materials see the change. For now, we update the current cell.
        default_limit = self.default_material.rule_limits.get(rule)
        default_val = getattr(default_limit, attr_name) if default_limit else ""

        # Update text in case we added units
        item.setText(input_text if input_text else default_val)

        # Coloring Logic
        if is_default_mat:
            item.setForeground(QtGui.QColor("#ffffff"))  # Always white
        elif input_text and input_text != default_val:
            item.setForeground(QtGui.QColor("#ffaa00"))  # Custom: Orange
        else:
            item.setForeground(QtGui.QColor("#ffffff"))  # Inherited: White

        self.table.blockSignals(False)

    def populate(self, material, default_material, active_rules: list[Rulebook]):
        self.active_material = material
        self.default_material = default_material

        self.table.blockSignals(True)
        self.table.setRowCount(0)

        for rule in active_rules:
            row = self.table.rowCount()
            self.table.insertRow(row)

            rule_item = QtWidgets.QTableWidgetItem(rule.label)
            rule_item.setData(QtCore.Qt.ItemDataRole.UserRole, rule)
            rule_item.setFlags(rule_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, rule_item)

            limit = material.rule_limits.get(rule)
            def_limit = default_material.rule_limits.get(rule) if default_material else None

            for col, attr in enumerate(["target", "warning", "error"], start=1):
                # --- Binary Rule Handling ---
                if rule.is_binary:
                    table_item = QtWidgets.QTableWidgetItem("N/A")
                    # Make it non-selectable and non-editable
                    table_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
                    table_item.setForeground(QtGui.QColor("#666666"))  # Dimmed color
                else:
                    val = getattr(limit, attr) if limit else ""
                    def_val = getattr(def_limit, attr) if def_limit else ""

                    # Inheritance Logic
                    is_default_mat = material.name == "Default"

                    if is_default_mat:
                        display_text = val
                        is_custom = False
                    else:
                        display_text = val if val else def_val
                        is_custom = bool(val and val != def_val)

                    table_item = QtWidgets.QTableWidgetItem(display_text)

                    # Visuals for numeric rules
                    if is_custom:
                        table_item.setForeground(QtGui.QColor("#ffaa00"))
                    else:
                        table_item.setForeground(QtGui.QColor("#ffffff"))

                self.table.setItem(row, col, table_item)

        self.table.blockSignals(False)


class AddMaterialDialog(QtWidgets.QDialog):
    def __init__(self, existing_categories: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Material")

        layout = QtWidgets.QFormLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        self.name_edit = QtWidgets.QLineEdit()
        # The key: an editable QComboBox
        self.cat_combo = QtWidgets.QComboBox()
        self.cat_combo.setEditable(True)
        self.cat_combo.addItems(sorted(list(set(existing_categories))))
        self.cat_combo.setPlaceholderText("Select or type a category...")

        layout.addRow("Material Name:", self.name_edit)
        layout.addRow("Category:", self.cat_combo)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self) -> tuple[str, str]:
        return self.name_edit.text().strip(), self.cat_combo.currentText().strip()


#
#
#
#
#
#
# -------------------------- Gui Command --------------------------------- #


class ProcessManagerCommand:
    def GetResources(self):
        return {
            "Pixmap": ":/icons/process_manager.svg",
            "MenuText": "Process Manager",
            "ToolTip": "Create, configure and save manufacturing processes.",
        }

    def Activated(self):
        dlg = ProcessManager()
        dlg.exec()

    def IsActive(self):
        return True


if App.GuiUp:
    Gui.addCommand("DFM_ProccessManager", ProcessManagerCommand())
