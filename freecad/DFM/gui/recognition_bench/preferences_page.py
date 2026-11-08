# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Preferences > DFM > Feature Recognition: every recognition threshold.

Built from the fields of core.recognition.RecognitionOptions (their
labels, units, ranges and tooltips), so a threshold added there appears
here without changes to this page.
"""

from __future__ import annotations

from PySide6 import QtGui, QtWidgets

from ...app.recognition_service import clear_cache, read_options, write_options
from ...core.recognition import RecognitionOptions


class DFMPreferencesRecognition:
    def __init__(self):
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Feature Recognition")
        self.form.setWindowIcon(QtGui.QIcon(":/icons/dfm_analysis.svg"))
        outer = QtWidgets.QVBoxLayout(self.form)
        intro = QtWidgets.QLabel(
            "Thresholds the feature recognition uses to decide what is a hole, boss, rib, slot, "
            "fillet, ... Changes apply to the next recognition.")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(inner)
        self.widgets: dict[str, QtWidgets.QWidget] = {}
        groups: dict[str, QtWidgets.QFormLayout] = {}
        defaults = RecognitionOptions()
        for f in RecognitionOptions.preference_fields():
            meta = f.metadata
            group = meta.get("group", "General")
            if group not in groups:
                box = QtWidgets.QGroupBox(group)
                groups[group] = QtWidgets.QFormLayout(box)
                layout.addWidget(box)
            default = getattr(defaults, f.name)
            if isinstance(default, bool):
                w = QtWidgets.QCheckBox(meta["label"])
                groups[group].addRow(w)
            elif isinstance(default, int):
                w = QtWidgets.QSpinBox()
                w.setRange(int(meta["min"]), int(meta["max"]))
                if meta.get("unit"):
                    w.setSuffix(f" {meta['unit']}")
                groups[group].addRow(meta["label"], w)
            else:
                w = QtWidgets.QDoubleSpinBox()
                w.setDecimals(int(meta.get("decimals", 2)))
                w.setRange(float(meta["min"]), float(meta["max"]))
                w.setSingleStep(10 ** -max(0, int(meta.get("decimals", 2)) - 1))
                if meta.get("unit"):
                    w.setSuffix(f" {meta['unit']}" if meta["unit"] != "°" else "°")
                groups[group].addRow(meta["label"], w)
            tip = meta.get("tooltip", "")
            w.setToolTip(f"{tip}\nDefault: {default}" if tip else f"Default: {default}")
            self.widgets[f.name] = w
        layout.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        reset = QtWidgets.QPushButton("Restore defaults")
        reset.clicked.connect(lambda: self._show(RecognitionOptions()))
        row = QtWidgets.QHBoxLayout()
        row.addStretch()
        row.addWidget(reset)
        outer.addLayout(row)

    def _show(self, options: RecognitionOptions) -> None:
        for name, w in self.widgets.items():
            value = getattr(options, name)
            if isinstance(w, QtWidgets.QCheckBox):
                w.setChecked(bool(value))
            elif isinstance(w, QtWidgets.QSpinBox):
                w.setValue(int(value))
            else:
                w.setValue(float(value))

    def options(self) -> RecognitionOptions:
        values = {}
        for name, w in self.widgets.items():
            if isinstance(w, QtWidgets.QCheckBox):
                values[name] = w.isChecked()
            else:
                values[name] = w.value()
        return RecognitionOptions.from_dict(values)

    def loadSettings(self):  # noqa: N802 (FreeCAD API)
        self._show(read_options())

    def saveSettings(self):  # noqa: N802 (FreeCAD API)
        write_options(self.options())
        clear_cache()
