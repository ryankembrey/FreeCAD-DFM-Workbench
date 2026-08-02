# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


from PySide6 import QtCore, QtWidgets

from ...app.contour.meshing import (
    RESOLUTION_DIVISORS,
    DEFAULT_RESOLUTION,
    MIN_ELEMENT_SIZE,
    TRIANGLE_HARD_CAP,
    element_size_for,
    estimate_triangle_count,
)

_CUSTOM = "Custom"


class ResolutionField(QtWidgets.QWidget):
    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._shape = None
        self._safe = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.combo = QtWidgets.QComboBox()
        self.combo.addItems(list(RESOLUTION_DIVISORS.keys()) + [_CUSTOM])
        self.combo.setCurrentText(DEFAULT_RESOLUTION)
        self.combo.setToolTip("Element size preset, scaled to the part. Custom lets you type mm.")
        self.combo.currentIndexChanged.connect(self._on_combo)
        layout.addWidget(self.combo)

        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setRange(MIN_ELEMENT_SIZE, 100000.0)
        self.spin.setDecimals(3)
        self.spin.setSingleStep(0.5)
        self.spin.setSuffix(" mm")
        self.spin.setToolTip("Explicit element size. Smaller is finer and slower.")
        self.spin.valueChanged.connect(self._on_spin)
        self.spin.hide()
        layout.addWidget(self.spin)

    def set_shape(self, shape):
        self._shape = shape
        if (
            self.combo.currentText() == _CUSTOM
            and self._shape is not None
            and self.spin.value() <= MIN_ELEMENT_SIZE
        ):
            self.spin.blockSignals(True)
            self.spin.setValue(element_size_for(shape, DEFAULT_RESOLUTION))
            self.spin.blockSignals(False)
        self._refresh_safe()

    def element_size(self):
        if self.combo.currentText() == _CUSTOM:
            return self.spin.value()
        if self._shape is None:
            return None
        return element_size_for(self._shape, self.combo.currentText())

    def is_safe(self):
        return self._safe

    def state(self):
        return self.combo.currentText(), self.element_size()

    def set_state(self, resolution, element_size=None):
        items = [self.combo.itemText(i) for i in range(self.combo.count())]
        self.combo.blockSignals(True)
        if resolution in items:
            self.combo.setCurrentText(resolution)
        self.combo.blockSignals(False)
        custom = self.combo.currentText() == _CUSTOM
        self.spin.setVisible(custom)
        if custom and element_size:
            self.spin.blockSignals(True)
            self.spin.setValue(element_size)
            self.spin.blockSignals(False)
        self._refresh_safe()

    def _on_combo(self):
        custom = self.combo.currentText() == _CUSTOM
        self.spin.setVisible(custom)
        if custom and self._shape is not None:
            self.spin.blockSignals(True)
            self.spin.setValue(element_size_for(self._shape, DEFAULT_RESOLUTION))
            self.spin.blockSignals(False)
        self._refresh_safe()
        self.changed.emit()

    def _on_spin(self):
        self._refresh_safe()
        self.changed.emit()

    def _refresh_safe(self):
        size = self.element_size()
        if self._shape is None or size is None:
            self._safe = False
            return
        count = estimate_triangle_count(self._shape, size)
        self._safe = count is not None and count <= TRIANGLE_HARD_CAP
