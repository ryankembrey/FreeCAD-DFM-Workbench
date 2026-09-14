# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


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


_VIRIDIS_STOPS = [
    (0.00, (0.267, 0.005, 0.329)),
    (0.10, (0.283, 0.131, 0.449)),
    (0.20, (0.263, 0.242, 0.521)),
    (0.30, (0.220, 0.343, 0.549)),
    (0.40, (0.177, 0.438, 0.558)),
    (0.50, (0.143, 0.523, 0.556)),
    (0.60, (0.120, 0.607, 0.540)),
    (0.70, (0.166, 0.690, 0.497)),
    (0.80, (0.319, 0.770, 0.411)),
    (0.90, (0.526, 0.833, 0.288)),
    (1.00, (0.993, 0.906, 0.144)),
]

_CIVIDIS_STOPS = [
    (0.00, (0.000, 0.135, 0.305)),
    (0.10, (0.000, 0.187, 0.403)),
    (0.20, (0.225, 0.245, 0.394)),
    (0.30, (0.322, 0.306, 0.400)),
    (0.40, (0.402, 0.364, 0.416)),
    (0.50, (0.479, 0.423, 0.430)),
    (0.60, (0.557, 0.487, 0.427)),
    (0.70, (0.645, 0.557, 0.410)),
    (0.80, (0.742, 0.629, 0.376)),
    (0.90, (0.846, 0.703, 0.321)),
    (1.00, (0.995, 0.910, 0.218)),
]

_PLASMA_STOPS = [
    (0.00, (0.050, 0.030, 0.528)),
    (0.20, (0.358, 0.001, 0.645)),
    (0.40, (0.611, 0.091, 0.620)),
    (0.60, (0.799, 0.278, 0.470)),
    (0.80, (0.930, 0.472, 0.326)),
    (1.00, (0.940, 0.975, 0.131)),
]

_INFERNO_STOPS = [
    (0.00, (0.001, 0.000, 0.014)),
    (0.20, (0.230, 0.036, 0.371)),
    (0.40, (0.502, 0.132, 0.428)),
    (0.60, (0.775, 0.246, 0.320)),
    (0.80, (0.955, 0.478, 0.148)),
    (1.00, (0.988, 0.998, 0.645)),
]

_MAGMA_STOPS = [
    (0.00, (0.001, 0.000, 0.014)),
    (0.20, (0.199, 0.048, 0.396)),
    (0.40, (0.482, 0.146, 0.507)),
    (0.60, (0.780, 0.243, 0.451)),
    (0.80, (0.972, 0.447, 0.360)),
    (1.00, (0.987, 0.991, 0.749)),
]


def viridis(t):
    return _lerp_stops(t, _VIRIDIS_STOPS)


def cividis(t):
    return _lerp_stops(t, _CIVIDIS_STOPS)


def plasma(t):
    return _lerp_stops(t, _PLASMA_STOPS)


def inferno(t):
    return _lerp_stops(t, _INFERNO_STOPS)


def magma(t):
    return _lerp_stops(t, _MAGMA_STOPS)


def grayscale(t):
    t = _clamp(t)
    return (t, t, t)


_COOLWARM_STOPS = [
    (0.00, (0.230, 0.299, 0.754)),
    (0.50, (0.865, 0.865, 0.865)),
    (1.00, (0.706, 0.016, 0.150)),
]

# matplotlib RdBu: red (low) - white - blue (high). Good for stress/pressure.
_RDBU_STOPS = [
    (0.00, (0.404, 0.000, 0.122)),
    (0.10, (0.698, 0.094, 0.168)),
    (0.20, (0.839, 0.376, 0.302)),
    (0.30, (0.957, 0.647, 0.510)),
    (0.40, (0.992, 0.859, 0.780)),
    (0.50, (0.969, 0.969, 0.969)),
    (0.60, (0.820, 0.898, 0.941)),
    (0.70, (0.573, 0.773, 0.871)),
    (0.80, (0.262, 0.576, 0.765)),
    (0.90, (0.129, 0.400, 0.674)),
    (1.00, (0.020, 0.188, 0.380)),
]

# matplotlib BrBG: brown (low) - white - teal/green (high). Colorblind-safe.
_BRBG_STOPS = [
    (0.00, (0.329, 0.188, 0.020)),
    (0.10, (0.549, 0.318, 0.039)),
    (0.20, (0.749, 0.506, 0.176)),
    (0.30, (0.875, 0.761, 0.490)),
    (0.40, (0.965, 0.910, 0.765)),
    (0.50, (0.961, 0.961, 0.961)),
    (0.60, (0.780, 0.918, 0.898)),
    (0.70, (0.502, 0.804, 0.757)),
    (0.80, (0.208, 0.592, 0.561)),
    (0.90, (0.004, 0.400, 0.369)),
    (1.00, (0.000, 0.235, 0.188)),
]


def coolwarm(t):
    """Smooth blue-gray-red (Moreland), blue low and red high."""
    return _lerp_stops(t, _COOLWARM_STOPS)


def rdbu(t):
    return _lerp_stops(t, _RDBU_STOPS)


def brbg(t):
    return _lerp_stops(t, _BRBG_STOPS)


_TURBO_STOPS = [
    (0.000, (0.190, 0.072, 0.232)),
    (0.125, (0.257, 0.395, 0.877)),
    (0.250, (0.213, 0.618, 0.994)),
    (0.375, (0.105, 0.831, 0.799)),
    (0.500, (0.363, 0.973, 0.470)),
    (0.625, (0.737, 0.985, 0.223)),
    (0.750, (0.981, 0.759, 0.187)),
    (0.875, (0.932, 0.383, 0.086)),
    (1.000, (0.480, 0.016, 0.011)),
]


def turbo(t):
    return _lerp_stops(t, _TURBO_STOPS)


def jet(t):
    t = _clamp(t)
    r = _clamp(1.5 - abs(4.0 * t - 3.0))
    g = _clamp(1.5 - abs(4.0 * t - 2.0))
    b = _clamp(1.5 - abs(4.0 * t - 1.0))
    return (r, g, b)


COLORMAPS: dict = {
    # Sequential (magnitude data, e.g. thickness)
    "Viridis": viridis,
    "Cividis": cividis,  # colorblind-safe
    "Plasma": plasma,  # thermal
    "Inferno": inferno,
    "Magma": magma,
    "Grayscale": grayscale,
    # Diverging (signed data, e.g. draft)
    "Cool-Warm": coolwarm,
    "RdBu": rdbu,  # stresses / pressure
    "BrBG": brbg,  # colorblind-safe
    # Rainbow (legacy)
    "Turbo": turbo,
    "Jet": jet,
}

DEFAULT_COLORMAP = "Cool-Warm"


def get_colormap(name: str) -> Callable[[float], tuple]:
    return COLORMAPS.get(name, viridis)


def normalize(value: float, vmin: float, vmax: float) -> float:
    if vmax - vmin < 1e-12:
        return 0.5
    return _clamp((value - vmin) / (vmax - vmin))


def quantize(value: float, step: float) -> float:
    """Snap a value to the center of its band, or return it unchanged if step <= 0."""
    if step <= 0.0:
        return value
    return (round(value / step)) * step


# Highlight modes.
HIGHLIGHT_OFF = "off"
HIGHLIGHT_ISOLATE = "isolate"  # in-band: colormap; out-of-band: grayed
HIGHLIGHT_FLAG = "flag"  # in-band: flag color; out-of-band: base color

_GRAY_DESAT = 0.82  # how far out-of-band colors are pulled toward gray (0..1)


class HighlightSpec:
    """A raw-value band and a mode for emphasising part of a contour, for DFM
    "isolate/flag a range" overlays.

    The band [lo, hi] is in the measure's own raw units (mm, degrees), and is
    independent of the legend's low/high display window -- either bound may be
    None for an open side (e.g. lo=None, hi=2.0 means "at or below 2").

    Modes:
      isolate -- in-band keeps the real colormap; out-of-band is desaturated to
                 gray but keeps its luminance, so muted geometry still shades.
      flag    -- in-band is a solid flag color; out-of-band a solid base color.
                 (No colormap; a pass/fail style view.)
    """

    def __init__(self, mode=HIGHLIGHT_OFF, lo=None, hi=None,
                 flag_color=(0.85, 0.15, 0.15), base_color=(0.30, 0.65, 0.30)):
        self.mode = mode
        self.lo = lo
        self.hi = hi
        self.flag_color = tuple(flag_color)
        self.base_color = tuple(base_color)

    @property
    def active(self):
        return self.mode in (HIGHLIGHT_ISOLATE, HIGHLIGHT_FLAG)

    def in_band(self, value: float) -> bool:
        if self.lo is not None and value < self.lo:
            return False
        if self.hi is not None and value > self.hi:
            return False
        return True


def _to_gray(rgb, amount=_GRAY_DESAT):
    """Pull an rgb toward its own gray (equal-luminance) by `amount`, so
    out-of-band regions read as muted but still shaded, not flat."""
    lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    return tuple(c + (lum - c) * amount for c in rgb)


def value_to_color(
    value: float,
    vmin: float,
    vmax: float,
    colormap: str = DEFAULT_COLORMAP,
    band_step: float = 0.0,
    highlight: "HighlightSpec | None" = None,
) -> tuple:
    """Map a value to an rgb. With an active HighlightSpec, the raw value is
    classified against its band first: isolate mode grays out-of-band colors,
    flag mode replaces the colormap entirely with flag/base colors. The band
    test uses the raw value, before quantization/normalization, so it's
    independent of the legend window."""
    if highlight is not None and highlight.active:
        inside = highlight.in_band(value)
        if highlight.mode == HIGHLIGHT_FLAG:
            return highlight.flag_color if inside else highlight.base_color
        # isolate
        v = quantize(value, band_step)
        base = get_colormap(colormap)(normalize(v, vmin, vmax))
        return base if inside else _to_gray(base)

    v = quantize(value, band_step)
    return get_colormap(colormap)(normalize(v, vmin, vmax))
