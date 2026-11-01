# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Compact header for the results panel.

A verdict banner (a tinted strip with the status icon, the verdict in its
color and the counts) and one line underneath with the model, the process
and the material switcher::

    | x Failed   1 error, 2 warnings                |
    Model Body   Process FDM     Material [PLA   v]

It replaces the four labelled rows of the .ui file, which takes about
90 px less height and leaves more room for the findings tree.
"""

import html
import re
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .tabs.common import MutedLabel

_STATUS_ICONS = {
    "Failed": ":/icons/dfm_error.svg",
    "Warning": ":/icons/dfm_warning.svg",
    "Successful": ":/icons/dfm_success.svg",
}

_VERDICT = re.compile(r"^\s*([^(]+?)\s*\((.*)\)\s*$")


class ElidedLabel(QtWidgets.QLabel):
    """A label that shortens its text with "…" when narrow; the full text is in the tooltip."""

    def __init__(self, text: str = "", parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._full = ""
        # Natural width when there's room, shrinking (with "…") when there isn't.
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self._full = text or ""
        self.setToolTip(self._full)
        self.updateGeometry()
        self._elide()

    def fullText(self) -> str:  # noqa: N802 (Qt naming)
        return self._full

    def sizeHint(self) -> QtCore.QSize:
        hint = super().sizeHint()
        return QtCore.QSize(self.fontMetrics().horizontalAdvance(self._full) + 2, hint.height())

    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(self.fontMetrics().horizontalAdvance("W…"), super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, QtCore.Qt.TextElideMode.ElideRight,
                                              max(self.width(), 1))
        super().setText(shown)


class ResultsHeader(QtWidgets.QWidget):
    """Verdict banner and the model / process / material line."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # -- Verdict banner ------------------------------------------------
        self.banner = QtWidgets.QFrame()
        self.banner.setObjectName("dfmVerdictBanner")
        self.banner.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QtWidgets.QHBoxLayout(self.banner)
        row.setContentsMargins(8, 6, 10, 6)
        row.setSpacing(8)
        self._icon = QtWidgets.QLabel()
        self._icon.setFixedSize(16, 16)
        row.addWidget(self._icon)
        self._verdict = QtWidgets.QLabel()
        self._verdict.setTextFormat(QtCore.Qt.TextFormat.RichText)
        font = self._verdict.font()
        font.setBold(True)
        self._verdict.setFont(font)
        row.addWidget(self._verdict)
        self._detail = ElidedLabel()
        row.addWidget(self._detail, 1)
        root.addWidget(self.banner)

        # -- Model · process · material ------------------------------------
        meta = QtWidgets.QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(6)
        self._model = ElidedLabel()
        self._process = ElidedLabel()
        self._material_label = MutedLabel("Material")
        meta.addWidget(MutedLabel("Model"))
        meta.addWidget(self._model)
        meta.addSpacing(8)
        meta.addWidget(MutedLabel("Process"))
        meta.addWidget(self._process)
        meta.addStretch(1)
        meta.addSpacing(8)
        meta.addWidget(self._material_label)
        self._material_slot = QtWidgets.QHBoxLayout()
        self._material_slot.setContentsMargins(0, 0, 0, 0)
        meta.addLayout(self._material_slot)
        self._meta = meta
        root.addLayout(meta)

        self.set_verdict("", "#639922")

    # ------------------------------------------------------------------

    def set_material_combo(self, combo: QtWidgets.QComboBox) -> None:
        """Put the panel's material switcher at the end of the meta line."""
        combo.setToolTip("Material. Changing it re-runs the checks.")
        combo.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
        combo.setMinimumWidth(100)
        self._material_label.setToolTip(combo.toolTip())
        self._material_slot.addWidget(combo)

    def set_model(self, name: str) -> None:
        self._model.setText(name)
        self._model.setToolTip(f"Target model: {name}")

    def set_process(self, name: str) -> None:
        self._process.setText(name)
        self._process.setToolTip(f"Manufacturing process: {name}")
        # The model name gives way first; the process keeps up to 160 px.
        self._process.setMinimumWidth(min(self._process.fontMetrics().horizontalAdvance(name) + 2, 160))

    def set_verdict(self, text: str, color: str) -> None:
        """Show a verdict such as "Failed (1 error, 2 warnings)" in *color*."""
        m = _VERDICT.match(text or "")
        word, detail = (m.group(1), m.group(2)) if m else ((text or "").strip(), "")
        if word == "Successful" and not detail:
            detail = "No errors or warnings"
        self._verdict.setText(f'<span style="color: {color}">{html.escape(word)}</span>')
        self._detail.setText(detail)
        icon = _STATUS_ICONS.get(word)
        if icon:
            ratio = self.devicePixelRatioF() or 1.0
            pixmap = QtGui.QIcon(icon).pixmap(QtCore.QSize(16, 16), ratio)
            self._icon.setPixmap(pixmap)
            self._icon.show()
        else:
            self._icon.hide()
        c = QtGui.QColor(color)
        self.banner.setStyleSheet(
            "QFrame#dfmVerdictBanner {"
            f" background: rgba({c.red()}, {c.green()}, {c.blue()}, 40);"
            f" border: none; border-left: 4px solid {color}; border-radius: 3px; }}"
            " QFrame#dfmVerdictBanner QLabel { background: transparent; }"
        )
        self.banner.setAccessibleName(f"Verdict: {text}")
