# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Text badges for the 3D view: paint with Qt, hand to Coin as an image."""

import math
from typing import Optional

from PySide6 import QtCore, QtGui


def paint_badge(
    text: str,
    dpr: float,
    *,
    font_pt: float,
    pad_x: float,
    pad_y: float,
    margin: float = 0.0,
    radius: float = 0.0,
    fill: Optional[QtGui.QColor] = None,
    shadow: bool = False,
    text_color: Optional[QtGui.QColor] = None,
):
    """Paint a bold text badge into a transparent QImage.

    ``fill`` draws a rounded background (omit for text only) and ``shadow`` adds a soft
    drop shadow under it. ``margin`` is transparent padding around the badge.

    Returns ``(image, badge_w, badge_h)`` where the sizes are the badge rectangle in
    logical pixels, excluding ``margin``.
    """
    font = QtGui.QFont()
    font.setPointSizeF(font_pt)
    font.setBold(True)

    metrics = QtGui.QFontMetricsF(font)
    badge_w = metrics.horizontalAdvance(text) + (pad_x * 2.0)
    badge_h = metrics.height() + (pad_y * 2.0)
    img_w = badge_w + (margin * 2.0)
    img_h = badge_h + (margin * 2.0)

    image = QtGui.QImage(
        int(math.ceil(img_w * dpr)),
        int(math.ceil(img_h * dpr)),
        QtGui.QImage.Format.Format_RGBA8888,
    )
    image.setDevicePixelRatio(dpr)
    image.fill(QtCore.Qt.GlobalColor.transparent)

    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)

    rect = QtCore.QRectF(margin, margin, badge_w, badge_h)

    if fill is not None:
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        if shadow:
            painter.setBrush(QtGui.QColor(0, 0, 0, 70))
            painter.drawRoundedRect(rect.translated(0.0, 1.0), radius, radius)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, radius, radius)

    painter.setPen(text_color if text_color is not None else QtGui.QColor(255, 255, 255))
    painter.setFont(font)
    painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, text)
    painter.end()

    return image, badge_w, badge_h


def flipped_rgba(image: QtGui.QImage):
    """Convert to the bottom-up RGBA8888 layout Coin expects.

    Returns ``(width_px, height_px, bytes)``.
    """
    flipped = image.mirrored(False, True).convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    buffer = bytes(flipped.constBits())[: flipped.sizeInBytes()]
    return flipped.width(), flipped.height(), buffer


def qimage_to_soimage(image: QtGui.QImage, dpr: float, h_align: str = "center"):
    """Wrap ``image`` in a Coin ``SoImage`` node sized in logical pixels.

    ``h_align`` is ``"center"`` or ``"left"``. Returns ``(node, logical_w, logical_h)``.
    """
    from pivy import coin

    logical_w = image.width() / dpr
    logical_h = image.height() / dpr
    width_px, height_px, buffer = flipped_rgba(image)

    node = coin.SoImage()
    node.image.setValue(coin.SbVec2s(width_px, height_px), 4, buffer)
    node.width.setValue(int(round(logical_w)))
    node.height.setValue(int(round(logical_h)))
    node.vertAlignment = coin.SoImage.HALF
    node.horAlignment = coin.SoImage.LEFT if h_align == "left" else coin.SoImage.CENTER
    return node, logical_w, logical_h
