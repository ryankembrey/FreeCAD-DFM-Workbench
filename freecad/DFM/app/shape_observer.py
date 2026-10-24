# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""
Document observer that checks for shape changes on document open.

When a document containing DFM Results objects is reopened, this module
compares the stored shape hash against the current shape.  If a change
is detected it offers to rerun the analysis with the same settings.
"""

from __future__ import annotations

import FreeCAD as App  # type: ignore

from ..app.document_object import (
    check_shape_changed,
    load_results_from_document,
    mark_stale,
)


def _is_dfm_results_object(obj) -> bool:
    """Check whether *obj* is a DFM Results FeaturePython object.

    Uses property detection rather than ``isinstance`` so it works
    reliably after document restore, where the Proxy class may have been
    imported through a different module path.
    """
    has_hash = hasattr(obj, "DFM_ShapeHash")
    has_json = hasattr(obj, "DFM_FindingsJSON")
    return has_hash and has_json


class DFMDocumentObserver:
    """FreeCAD document observer that fires on document open/restore."""

    def slotFinishRestoreDocument(self, doc):
        """Called after a document has been fully restored from disk."""
        for i, obj in enumerate(doc.Objects):
            try:
                self._check_for_stale_results(doc)
            except Exception as e:
                import traceback

                App.Console.PrintError(f"DFM shape-change check failed: {e}\n")
                App.Console.PrintError(traceback.format_exc())

    # ------------------------------------------------------------------

    def _check_for_stale_results(self, doc):
        """Walk the document looking for DFM Results objects and check staleness."""
        stale_objects: list[tuple] = []

        for obj in doc.Objects:
            if not _is_dfm_results_object(obj):
                continue

            data = load_results_from_document(obj)
            if data is None:
                App.Console.PrintWarning(
                    f"DFM DEBUG: load_results_from_document returned None for '{obj.Label}'\n"
                )
                continue

            target_label = data["target_label"]
            stored_hash = data.get("shape_hash", "N/A")

            # Find the target shape in the document
            target_obj = None
            for candidate in doc.Objects:
                if candidate.Label == target_label and hasattr(candidate, "Shape"):
                    target_obj = candidate
                    break

            if target_obj is None:
                App.Console.PrintWarning(
                    f"DFM DEBUG: Could NOT find target '{target_label}' with a Shape\n"
                )
                continue

            # Log shape details
            try:
                shape = target_obj.Shape
            except Exception as e:
                App.Console.PrintError(f"DFM DEBUG: Error reading shape: {e}\n")

            changed = check_shape_changed(obj, target_obj.Shape)

            if changed:
                mark_stale(obj, True)
                stale_objects.append((obj, data, target_obj))
            else:
                if not stale_objects:
                    return

        # Defer the dialog to avoid blocking the restore callback
        from PySide6 import QtCore

        QtCore.QTimer.singleShot(500, lambda: self._show_stale_dialog(stale_objects))

    def _show_stale_dialog(self, stale_objects):
        """Show a dialog informing the user about stale DFM results."""
        from PySide6.QtWidgets import QMessageBox

        names = ", ".join(data["target_label"] for _, data, _ in stale_objects)

        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("DFM: Shape Changed")
        msg.setText(
            f"The following shapes have changed since their last DFM analysis:\n\n"
            f"{names}\n\n"
            f"The saved DFM results may no longer be accurate. "
            f"Topological references (face/edge labels) may have shifted."
        )
        msg.setInformativeText("Would you like to rerun the analysis with the same settings?")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)

        result = msg.exec()
        if result == QMessageBox.StandardButton.Yes:
            self._rerun_stale_analyses(stale_objects)

    def _rerun_stale_analyses(self, stale_objects):
        """Launch the DFM analysis panel pre-filled with the stored settings."""
        import FreeCADGui as Gui  # type: ignore
        from ..gui.task_setup import TaskSetup

        # For simplicity, rerun the first stale object's analysis.
        # Multiple stale objects would need queuing which is out of scope.
        _, data, target_obj = stale_objects[0]

        # Select the target object and open the analysis with presets
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(target_obj)
        Gui.Control.closeDialog()
        task = TaskSetup(
            preset_process=data["process"],
            preset_material=data["material"],
            auto_run=True,
        )
        Gui.Control.showDialog(task)

        if len(stale_objects) > 1:
            App.Console.PrintWarning(
                f"DFM: {len(stale_objects)} shapes have changed. "
                f"Please rerun analysis for the remaining shapes manually.\n"
            )


# -- Singleton management --------------------------------------------------

_observer_instance: DFMDocumentObserver | None = None


def install_observer():
    """Install the DFM document observer (idempotent)."""
    global _observer_instance
    if _observer_instance is not None:
        return
    _observer_instance = DFMDocumentObserver()
    App.addDocumentObserver(_observer_instance)


def uninstall_observer():
    """Remove the DFM document observer."""
    global _observer_instance
    if _observer_instance is None:
        return
    try:
        App.removeDocumentObserver(_observer_instance)
    except Exception:
        pass
    _observer_instance = None
