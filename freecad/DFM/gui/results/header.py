# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Compact header for the results panel.

A verdict banner (a tinted strip with the status icon, the verdict in its
color and the counts) and one line underneath with the model, the process
and the material switcher::

    | x Failed   1 error, 2 warnings                |
    Model Body   Process FDM     Material [PLA   v]

When that line doesn't fit (a long process name in a narrow task panel),
the model moves to a line of its own above, instead of shrinking to "B…"::

    Model Enclosure
    Process Plastic Injection Molding   Material [ABS v]

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
_FIXED = (QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Minimum)


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
        # The model pair lives in its own widget, so it can sit at the start of
        # the meta line or on a line of its own above it (see _fit_meta).
        self._model_label = MutedLabel("Model")
        self._model = ElidedLabel()
        self._model_box = QtWidgets.QWidget()
        model_row = QtWidgets.QHBoxLayout(self._model_box)
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(6)
        model_row.addWidget(self._model_label)
        model_row.addWidget(self._model, 1)

        self._model_line = QtWidgets.QHBoxLayout()  # used when the meta line is too narrow
        self._model_line.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self._model_line)

        meta = QtWidgets.QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(6)
        self._process_label = MutedLabel("Process")
        self._process = ElidedLabel()
        self._material_label = MutedLabel("Material")
        meta.addWidget(self._model_box)
        self._model_gap = 8
        meta.addSpacing(self._model_gap)
        meta.addWidget(self._process_label)
        meta.addWidget(self._process)
        meta.addStretch(1)
        meta.addSpacing(8)
        meta.addWidget(self._material_label)
        self._material_slot = QtWidgets.QHBoxLayout()
        self._material_slot.setContentsMargins(0, 0, 0, 0)
        meta.addLayout(self._material_slot)
        self._meta = meta
        self._combo: Optional[QtWidgets.QComboBox] = None
        self._model_on_own_line = False
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
        self._combo = combo
        combo.currentIndexChanged.connect(lambda _i: self._fit_meta())
        self._fit_meta()

    def set_model(self, name: str) -> None:
        self._model.setText(name)
        self._model.setToolTip(f"Target model: {name}")
        self._fit_meta()

    def set_process(self, name: str) -> None:
        self._process.setText(name)
        self._process.setToolTip(f"Manufacturing process: {name}")
        # When even the line without the model is too narrow, the process
        # keeps up to 160 px and the material switcher the rest.
        self._process.setMinimumWidth(min(self._process.fontMetrics().horizontalAdvance(name) + 2, 160))
        self._fit_meta()

    def model_on_own_line(self) -> bool:
        """True when the model is shown on a line of its own (the meta line was too narrow)."""
        return self._model_on_own_line

    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._fit_meta()

    def _meta_width(self) -> int:
        """Width the meta line needs to show everything on it in full."""
        spacing = self._meta.spacing()
        widgets = [self._model_label, self._model, self._process_label, self._process, self._material_label]
        width = sum(w.sizeHint().width() for w in widgets)
        if self._combo is not None:
            # The switcher's own size hint fits its longest material; it only
            # needs to show the current one in full.
            width += self._combo_width()
        # Gaps between the items, and the two fixed 8 px spacings.
        return width + spacing * (len(widgets) + 1) + self._model_gap + 8

    def _combo_width(self) -> int:
        """Width of the material switcher showing its current material in full."""
        combo = self._combo
        option = QtWidgets.QStyleOptionComboBox()
        combo.initStyleOption(option)
        text = QtCore.QSize(combo.fontMetrics().horizontalAdvance(combo.currentText()) + 4,
                            combo.fontMetrics().height())
        size = combo.style().sizeFromContents(QtWidgets.QStyle.ContentsType.CT_ComboBox, option, text, combo)
        return max(size.width(), combo.minimumWidth())

    def _fit_meta(self) -> None:
        """Put the model on a line of its own when the meta line can't show it in full.

        The width needed doesn't depend on where the model is, so moving it
        can't make the header flip back and forth.
        """
        own_line = self.width() < self._meta_width()
        # On the meta line the model keeps its full width (the line fits, so
        # the material switcher gives way); on its own line it may shorten.
        self._model.setMinimumWidth(0 if own_line else self._model.sizeHint().width())
        if own_line == self._model_on_own_line:
            return
        self._model_on_own_line = own_line
        if own_line:
            self._meta.removeWidget(self._model_box)
            self._gap().changeSize(0, 0, *_FIXED)  # the gap after the model
            self._model_line.addWidget(self._model_box)
        else:
            self._model_line.removeWidget(self._model_box)
            self._meta.insertWidget(0, self._model_box)
            self._gap().changeSize(self._model_gap, 0, *_FIXED)
        self._meta.invalidate()

    def _gap(self) -> QtWidgets.QSpacerItem:
        """The spacing after the model pair (the first spacer of the meta line)."""
        for i in range(self._meta.count()):
            spacer = self._meta.itemAt(i).spacerItem()
            if spacer is not None:
                return spacer
        raise RuntimeError("the meta line has no spacer")

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
