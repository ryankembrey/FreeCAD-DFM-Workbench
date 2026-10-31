# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Render SVG diagram templates into QPixmap using palette-derived colours.

The renderer substitutes placeholder tokens in the SVG bytes with colours
derived from the active QPalette, so diagrams follow the FreeCAD theme.

Outlines use ``WindowText`` because the panel paints the diagram directly on
the ``Window`` colour. The part body is a blend of the two rather than a
fixed palette role such as ``Mid``, which some themes set almost equal to
the window colour. The blend always sits between the background and the
outlines, so the body stays visible in light, dark and custom themes.
"""

from PySide6 import QtCore, QtGui
from PySide6.QtSvg import QSvgRenderer

from ...core.rules import Rulebook
from ._diagrams import DIAGRAMS

# Base annotation colours. The accent matches link text and icons elsewhere.
# ``themed_svg`` adjusts their lightness when a theme would make them hard to
# read, keeping the hue.
_ACCENT = b"#5b9bd5"
_BAD = b"#d9534f"
_GOOD = b"#4caf50"

# Share of the foreground colour mixed into the background for the part body.
_BODY_BLEND = 0.28

DEFAULT_WIDTH = 340


def _blend(bg: QtGui.QColor, fg: QtGui.QColor, t: float) -> QtGui.QColor:
    """Return *bg* moved towards *fg* by the fraction *t*."""
    return QtGui.QColor.fromRgbF(
        bg.redF() + (fg.redF() - bg.redF()) * t,
        bg.greenF() + (fg.greenF() - bg.greenF()) * t,
        bg.blueF() + (fg.blueF() - bg.blueF()) * t,
    )


def _luminance(c: QtGui.QColor) -> float:
    """Relative luminance as defined by WCAG 2."""

    def channel(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(c.redF())
        + 0.7152 * channel(c.greenF())
        + 0.0722 * channel(c.blueF())
    )


def _contrast(a: QtGui.QColor, b: QtGui.QColor) -> float:
    """WCAG contrast ratio between two colours (1.0 to 21.0)."""
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _legible(
    colour: bytes,
    toward: QtGui.QColor,
    against: tuple[tuple[QtGui.QColor, float], ...],
) -> bytes:
    """Return *colour*, adjusted only as far as needed to be legible.

    The hue and saturation are kept. Lightness is moved towards *toward*
    (the theme's text colour, which always contrasts with the background)
    until *colour* meets every ``(background, minimum ratio)`` pair in
    *against*. In typical light and dark themes the colour changes little
    or not at all; in low-contrast themes it shifts further.
    """
    c = QtGui.QColor(colour.decode())

    def ok(col: QtGui.QColor) -> bool:
        return all(_contrast(col, bg) >= ratio for bg, ratio in against)

    if ok(c):
        return colour

    h, s, lightness, _ = c.getHslF()
    step = 0.01 if toward.lightnessF() > lightness else -0.01
    while 0.0 <= lightness + step <= 1.0:
        lightness += step
        c = QtGui.QColor.fromHslF(h, s, lightness)
        if ok(c):
            break
    return c.name().encode()


def themed_svg(svg: bytes, palette: QtGui.QPalette) -> bytes:
    """Return *svg* with every colour placeholder replaced for *palette*."""
    window = palette.color(QtGui.QPalette.ColorRole.Window)
    text = palette.color(QtGui.QPalette.ColorRole.WindowText)
    if _contrast(text, window) < 3.0:
        # The palette does not describe what is drawn (for example a
        # stylesheet that was not applied to it). Fall back to whichever of
        # black or white reads best on the background.
        black, white = QtGui.QColor("#000000"), QtGui.QColor("#ffffff")
        text = black if _contrast(black, window) >= _contrast(white, window) else white
    body = _blend(window, text, _BODY_BLEND)

    # Annotations sit on both the background and the part body.
    accent = _legible(_ACCENT, text, ((window, 3.0), (body, 2.0)))
    bad = _legible(_BAD, text, ((window, 3.0), (body, 1.5)))
    good = _legible(_GOOD, text, ((window, 3.0),))

    return (
        svg
        .replace(b"__STROKE__", text.name().encode())
        .replace(b"__BODY__", body.name().encode())
        .replace(b"__ACCENT__", accent)
        .replace(b"__BAD__", bad)
        .replace(b"__GOOD__", good)
    )


def render_diagram(
    rule: Rulebook,
    palette: QtGui.QPalette,
    width: int = DEFAULT_WIDTH,
) -> QtGui.QPixmap | None:
    """Render the SVG for *rule* into a QPixmap at the given *width*.

    Returns ``None`` when no diagram is registered for the rule.
    Height is determined by the SVG viewBox aspect ratio.
    """
    diag = DIAGRAMS.get(rule)
    if diag is None:
        return None

    renderer = QSvgRenderer(QtCore.QByteArray(themed_svg(diag.svg, palette)))
    if not renderer.isValid():
        return None

    vbox = renderer.viewBoxF()
    if vbox.isEmpty():
        height = width
    else:
        height = int(width * vbox.height() / vbox.width())

    screen = QtGui.QGuiApplication.primaryScreen()
    device_ratio = screen.devicePixelRatio() if screen else 1.0

    px = QtGui.QPixmap(
        int(width * device_ratio),
        int(height * device_ratio),
    )
    px.setDevicePixelRatio(device_ratio)
    px.fill(QtCore.Qt.GlobalColor.transparent)

    painter = QtGui.QPainter(px)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QtCore.QRectF(0, 0, width, height))
    painter.end()

    return px
