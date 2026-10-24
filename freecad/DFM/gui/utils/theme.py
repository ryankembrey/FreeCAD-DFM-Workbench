# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Colour helpers: luminance, contrast text colour, and FreeCAD's view colour preferences."""

from PySide6 import QtGui

import FreeCAD as App  # type: ignore

_VIEW_PARAMS = "User parameter:BaseApp/Preferences/View"


def relative_luminance(rgb) -> float:
    """Rec. 709 luma of an (r, g, b) tuple with components in 0..1."""
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def qcolor_luminance(color: QtGui.QColor) -> float:
    return relative_luminance((color.redF(), color.greenF(), color.blueF()))


def contrast_text_color(rgb, threshold: float = 0.6) -> QtGui.QColor:
    """Near-black text on light backgrounds, white on dark ones."""
    if relative_luminance(rgb) > threshold:
        return QtGui.QColor(25, 25, 25)
    return QtGui.QColor(255, 255, 255)


def pref_color(entry: str, default_rgb: tuple) -> tuple:
    """Read a packed 0xRRGGBBAA colour from FreeCAD's View preferences as (r, g, b) in 0..1."""
    try:
        param = App.ParamGet(_VIEW_PARAMS)
        packed = param.GetUnsigned(entry, 0)
        if packed:
            r = ((packed >> 24) & 0xFF) / 255.0
            g = ((packed >> 16) & 0xFF) / 255.0
            b = ((packed >> 8) & 0xFF) / 255.0
            return (r, g, b)
    except Exception:
        pass
    return default_rgb


def viewport_background_luminance() -> float:
    """Average luminance of the 3D viewport background (gradient-aware)."""
    try:
        p = App.ParamGet(_VIEW_PARAMS)
    except Exception:
        return 0.5

    try:
        simple = p.GetBool("Simple", False)
        gradient = p.GetBool("Gradient", True)
        use_mid = p.GetBool("UseBackgroundColorMid", False)
    except Exception:
        simple, gradient, use_mid = False, True, False

    if simple or not gradient:
        return relative_luminance(pref_color("BackgroundColor", (0.9, 0.9, 0.95)))

    top = pref_color("BackgroundColor2", (0.2, 0.3, 0.5))
    bottom = pref_color("BackgroundColor3", (0.6, 0.6, 0.7))
    stops = [top, bottom]
    if use_mid:
        stops.append(pref_color("BackgroundColor4", (0.4, 0.45, 0.6)))
    return sum(relative_luminance(s) for s in stops) / len(stops)
