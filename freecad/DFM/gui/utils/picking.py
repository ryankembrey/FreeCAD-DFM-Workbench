# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Helpers for "click something in the 3D view" pick modes."""

from PySide6 import QtCore, QtWidgets

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore


PICK_BUTTON_STYLE = """
QPushButton:checked {
    border: 2px solid palette(highlight);
    font-weight: bold;
}
"""


class CrossCursorOverride:
    """Shows the cross cursor while picking, and always restores it exactly once.

    Qt's override cursor is a stack, so pushing twice without popping leaves a stuck
    cursor. ``push``/``pop`` are idempotent to avoid that.
    """

    def __init__(self):
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def push(self) -> None:
        if self._active:
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.CrossCursor)
        self._active = True

    def pop(self) -> None:
        if not self._active:
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        self._active = False


def first_selected_subelement():
    """Return ``(sub_shape, sub_name)`` for the first selected sub-element, or None.

    ``sub_name`` falls back to ``"Selected"`` if FreeCAD reports no element name.
    """
    sel = Gui.Selection.getSelectionEx()
    if not sel or not sel[0].SubObjects:
        return None
    sub = sel[0].SubObjects[0]
    name = sel[0].SubElementNames[0] if sel[0].SubElementNames else "Selected"
    return sub, name


def resolve_subshape(doc_name: str, obj_name: str, sub_name: str):
    """Resolve a (document, object, sub-element name) triple to a shape, or None."""
    if not sub_name:
        return None
    try:
        obj = App.getDocument(doc_name).getObject(obj_name)
        return obj.Shape.getElement(sub_name)
    except Exception:
        return None
