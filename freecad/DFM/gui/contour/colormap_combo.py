# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


from PySide6 import QtCore, QtGui, QtWidgets

from ...app.contour.colormap import get_colormap

_SWATCH_SIZE = 16  # logical px; square
_GRADIENT_STEPS = 24


def _to_qcolor(rgb) -> QtGui.QColor:
    r, g, b = rgb
    return QtGui.QColor.fromRgbF(
        max(0.0, min(1.0, r)), max(0.0, min(1.0, g)), max(0.0, min(1.0, b))
    )


def colormap_pixmap(name: str, size: int = _SWATCH_SIZE, dpr: float = 1.0) -> QtGui.QPixmap:
    """Square pixmap of the color map, low values on the left, high on the right."""
    px = max(1, int(round(size * dpr)))
    pm = QtGui.QPixmap(px, px)
    pm.setDevicePixelRatio(dpr)
    pm.fill(QtCore.Qt.GlobalColor.transparent)

    cmap = get_colormap(name)
    grad = QtGui.QLinearGradient(0.0, 0.0, float(size), 0.0)
    for i in range(_GRADIENT_STEPS + 1):
        t = i / _GRADIENT_STEPS
        grad.setColorAt(t, _to_qcolor(cmap(t)))

    p = QtGui.QPainter(pm)
    try:
        rect = QtCore.QRectF(0.0, 0.0, float(size), float(size))
        p.fillRect(rect, QtGui.QBrush(grad))
        pen = QtGui.QPen(QtGui.QColor(127, 127, 127, 190))
        pen.setWidthF(1.0)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
    finally:
        p.end()
    return pm


def colormap_icon(name: str, size: int = _SWATCH_SIZE, dpr: float = 1.0) -> QtGui.QIcon:
    pm = colormap_pixmap(name, size, dpr)
    icon = QtGui.QIcon()
    for mode in (QtGui.QIcon.Mode.Normal, QtGui.QIcon.Mode.Active, QtGui.QIcon.Mode.Selected):
        icon.addPixmap(pm, mode, QtGui.QIcon.State.Off)
        icon.addPixmap(pm, mode, QtGui.QIcon.State.On)
    return icon


def populate_colormap_combo(combo: QtWidgets.QComboBox, names, size: int = _SWATCH_SIZE):
    """Fill `combo` with one item per color map name, each with a gradient swatch."""
    dpr = max(1.0, float(combo.devicePixelRatioF()))
    combo.setIconSize(QtCore.QSize(size, size))
    for name in names:
        combo.addItem(colormap_icon(name, size, dpr), name)
