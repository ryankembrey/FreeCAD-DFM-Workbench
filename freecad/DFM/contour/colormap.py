# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Value-to-color mapping for contour visualisations.

Each colormap is a function taking a normalized parameter t in [0, 1] and
returning an (r, g, b) tuple of floats in [0, 1]. Use `value_to_color` to map a
raw value against a [vmin, vmax] display range, optionally quantized into bands.
"""

from typing import Callable


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _lerp_stops(t: float, stops: list) -> tuple:
    """Piecewise-linear interpolation across a list of (pos, (r, g, b)) stops."""
    t = _clamp(t)
    if t <= stops[0][0]:
        return stops[0][1]
    if t >= stops[-1][0]:
        return stops[-1][1]
    for i in range(len(stops) - 1):
        p0, c0 = stops[i]
        p1, c1 = stops[i + 1]
        if p0 <= t <= p1:
            f = (t - p0) / (p1 - p0) if p1 > p0 else 0.0
            return (
                c0[0] + (c1[0] - c0[0]) * f,
                c0[1] + (c1[1] - c0[1]) * f,
                c0[2] + (c1[2] - c0[2]) * f,
            )
    return stops[-1][1]


def jet(t: float) -> tuple:
    """Classic FEM rainbow: blue (low) through green to red (high)."""
    t = _clamp(t)
    r = _clamp(1.5 - abs(4.0 * t - 3.0))
    g = _clamp(1.5 - abs(4.0 * t - 2.0))
    b = _clamp(1.5 - abs(4.0 * t - 1.0))
    return (r, g, b)


_TURBO_STOPS = [
    (0.00, (0.190, 0.072, 0.232)),
    (0.25, (0.100, 0.620, 0.930)),
    (0.50, (0.400, 0.940, 0.300)),
    (0.75, (0.980, 0.720, 0.160)),
    (1.00, (0.730, 0.010, 0.010)),
]


def turbo(t: float) -> tuple:
    return _lerp_stops(t, _TURBO_STOPS)


_COOLWARM_STOPS = [
    (0.00, (0.230, 0.300, 0.750)),
    (0.50, (0.870, 0.870, 0.870)),
    (1.00, (0.710, 0.020, 0.150)),
]


def coolwarm(t: float) -> tuple:
    """Diverging blue-to-red, neutral pale gray at the center."""
    return _lerp_stops(t, _COOLWARM_STOPS)


_VIRIDIS_STOPS = [
    (0.00, (0.267, 0.005, 0.329)),
    (0.25, (0.229, 0.322, 0.545)),
    (0.50, (0.128, 0.567, 0.551)),
    (0.75, (0.369, 0.789, 0.383)),
    (1.00, (0.993, 0.906, 0.144)),
]


def viridis(t: float) -> tuple:
    return _lerp_stops(t, _VIRIDIS_STOPS)


_PLASMA_STOPS = [
    (0.00, (0.050, 0.030, 0.528)),
    (0.25, (0.417, 0.000, 0.658)),
    (0.50, (0.692, 0.165, 0.564)),
    (0.75, (0.881, 0.392, 0.383)),
    (1.00, (0.940, 0.975, 0.131)),
]


def plasma(t: float) -> tuple:
    return _lerp_stops(t, _PLASMA_STOPS)


_INFERNO_STOPS = [
    (0.00, (0.001, 0.000, 0.014)),
    (0.25, (0.258, 0.039, 0.406)),
    (0.50, (0.578, 0.148, 0.404)),
    (0.75, (0.865, 0.317, 0.226)),
    (1.00, (0.988, 0.998, 0.645)),
]


def inferno(t: float) -> tuple:
    return _lerp_stops(t, _INFERNO_STOPS)


def grayscale(t: float) -> tuple:
    t = _clamp(t)
    return (t, t, t)


_DRAFT_STOPS = [
    (0.00, (0.020, 0.190, 0.780)),
    (0.30, (0.150, 0.650, 0.900)),
    (0.48, (0.980, 0.950, 0.250)),
    (0.50, (1.000, 0.900, 0.150)),
    (0.52, (0.980, 0.950, 0.250)),
    (0.70, (0.980, 0.520, 0.180)),
    (1.00, (0.780, 0.050, 0.050)),
]


def draft_diverging(t: float) -> tuple:
    return _lerp_stops(t, _DRAFT_STOPS)


COLORMAPS: dict = {
    "Jet": jet,
    "Turbo": turbo,
    "Cool-Warm": coolwarm,
    "Viridis": viridis,
    "Plasma": plasma,
    "Inferno": inferno,
    "Grayscale": grayscale,
    "Draft (center highlight)": draft_diverging,
}

DEFAULT_COLORMAP = "Turbo"


def get_colormap(name: str) -> Callable[[float], tuple]:
    return COLORMAPS.get(name, jet)


def normalize(value: float, vmin: float, vmax: float) -> float:
    if vmax - vmin < 1e-12:
        return 0.5
    return _clamp((value - vmin) / (vmax - vmin))


def quantize(value: float, step: float) -> float:
    """Snap a value to the center of its band, or return it unchanged if step <= 0."""
    if step <= 0.0:
        return value
    return (round(value / step)) * step


def value_to_color(
    value: float,
    vmin: float,
    vmax: float,
    colormap: str = DEFAULT_COLORMAP,
    band_step: float = 0.0,
) -> tuple:
    v = quantize(value, band_step)
    return get_colormap(colormap)(normalize(v, vmin, vmax))
