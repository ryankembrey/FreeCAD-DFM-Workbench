# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Small reusable Qt event filters."""

from PySide6 import QtCore


class EscapeFilter(QtCore.QObject):
    """Calls ``callback()`` when Escape is pressed.

    If the callback returns a truthy value the key press is swallowed, otherwise it
    continues to the widget as normal.
    """

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self._callback = callback

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.KeyPress:
            if event.key() == QtCore.Qt.Key.Key_Escape and self._callback():
                return True
        return False


class EnterKeyFilter(QtCore.QObject):
    """Commit a spin box on Enter: interpret the typed text, then drop focus."""

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.KeyPress:
            if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                if hasattr(obj, "interpretText"):
                    obj.interpretText()
                obj.clearFocus()
                return True
        return False
