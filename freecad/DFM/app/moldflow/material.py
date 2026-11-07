# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Melt viscosity: the Cross-WLF model (as Moldflow and Moldex3D use).

    eta0(T) = D1 * exp(-A1 (T - D2) / (A2 + T - D2))
    eta(T, shear) = eta0 / (1 + (eta0 * shear / tau_star) ** (1 - n))

The generic grades are approximate (fitted by hand to typical viscosities);
for real work enter the supplier's coefficients as the Custom material in
the preferences (Edit > Preferences > DFM > Mold Flow).
"""

from dataclasses import dataclass, replace
from typing import Optional

import numpy as np


@dataclass
class Material:
    name: str
    n: float  # power-law index (shear thinning)
    tau_star: float  # Pa
    D1: float  # Pa.s
    D2: float  # K
    A1: float
    A2: float  # K
    melt_temp: float  # C, recommended
    mold_temp: float  # C, recommended
    no_flow_temp: Optional[float] = None  # C: below this the melt stops (frozen)
    diffusivity: float = 1.0e-7  # m^2/s, thermal diffusivity of the melt

    def zero_shear(self, temp_c):
        T = np.asarray(temp_c, float) + 273.15
        dT = T - self.D2
        return self.D1 * np.exp(-self.A1 * dT / (self.A2 + dT))

    def viscosity(self, shear_rate, temp_c=None):
        eta0 = self.zero_shear(self.melt_temp if temp_c is None else temp_c)
        g = np.maximum(np.asarray(shear_rate, float), 0.0)
        if self.n >= 1.0:
            return eta0 * np.ones_like(g)
        return eta0 / (1.0 + (eta0 * g / self.tau_star) ** (1.0 - self.n))

    def with_temperatures(self, melt=None, mold=None):
        """A copy run at other melt / mold temperatures."""
        return replace(self, melt_temp=self.melt_temp if melt is None else float(melt),
                       mold_temp=self.mold_temp if mold is None else float(mold))


def newtonian(viscosity, name="Newtonian"):
    """A constant viscosity, no cooling (for checking against closed-form answers)."""
    return Material(name, n=1.0, tau_star=1e30, D1=viscosity, D2=0.0, A1=0.0, A2=1.0,
                    melt_temp=200.0, mold_temp=50.0)


GENERIC = {
    "PP (generic)": Material("PP (generic)", n=0.25, tau_star=2.5e4, D1=8.8e12, D2=263.15,
                             A1=27.0, A2=51.6, melt_temp=230.0, mold_temp=40.0,
                             no_flow_temp=130.0),
    "ABS (generic)": Material("ABS (generic)", n=0.29, tau_star=3.0e4, D1=4.3e11, D2=373.15,
                              A1=25.0, A2=51.6, melt_temp=240.0, mold_temp=60.0,
                              no_flow_temp=130.0),
    "PC (generic)": Material("PC (generic)", n=0.20, tau_star=5.0e5, D1=3.6e12, D2=417.15,
                             A1=30.0, A2=51.6, melt_temp=300.0, mold_temp=90.0,
                             no_flow_temp=170.0),
}
CUSTOM = "Custom"

# The Custom material's preference keys and defaults (those of generic ABS).
CUSTOM_KEYS = {
    "MoldFlowCustomName": ("name", "My material"),
    "MoldFlowCustomN": ("n", 0.29),
    "MoldFlowCustomTauStar": ("tau_star", 3.0e4),
    "MoldFlowCustomD1": ("D1", 4.3e11),
    "MoldFlowCustomD2": ("D2", 373.15),
    "MoldFlowCustomA1": ("A1", 25.0),
    "MoldFlowCustomA2": ("A2", 51.6),
    "MoldFlowCustomMeltTemp": ("melt_temp", 240.0),
    "MoldFlowCustomMoldTemp": ("mold_temp", 60.0),
    "MoldFlowCustomNoFlowTemp": ("no_flow_temp", 130.0),
}


def material_names():
    return list(GENERIC) + [CUSTOM]


def custom_material(params=None) -> Material:
    """The user's own material, from the preferences."""
    if params is None:
        from ..prefs import dfm_params

        params = dfm_params()
    values = {}
    for key, (field, default) in CUSTOM_KEYS.items():
        if field == "name":
            values[field] = params.GetString(key, default) or default
        else:
            # D1 is too big for a spin box: kept as text ("4.3e11")
            raw = params.GetString(key, "") if key == "MoldFlowCustomD1" else None
            if raw:
                try:
                    values[field] = float(raw)
                    continue
                except ValueError:
                    pass
            values[field] = params.GetFloat(key, float(default)) if raw is None else float(default)
    return Material(**values)


def material_for(name: str) -> Material:
    if name in GENERIC:
        return GENERIC[name]
    if name == CUSTOM:
        return custom_material()
    raise KeyError(f"Unknown material '{name}'")
