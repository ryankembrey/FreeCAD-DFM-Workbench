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


def value_to_color(
    value: float,
    vmin: float,
    vmax: float,
    colormap: str = DEFAULT_COLORMAP,
    band_step: float = 0.0,
) -> tuple:
    v = quantize(value, band_step)
    return get_colormap(colormap)(normalize(v, vmin, vmax))
