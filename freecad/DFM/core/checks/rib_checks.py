# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Rib rules: thickness at the base and height, against the nominal wall.

Crush ribs (inside a bore, meant to interfere with what is pressed in) are
left out of both.
"""

from __future__ import annotations

from typing import Optional

from ...core.base.feature_check import FeatureCheck, Measure, mm
from ...core.recognition.model import Feature, FeatureKind
from ...core.registries import register_check
from ...core.rules import Rulebook
from ...core.utils.part_features import PartFeatures


def root_thickness(rib: Feature) -> Optional[float]:
    """A rib's thickness at its base (where a drafted rib is thickest)."""
    root = FeatureCheck.param(rib, "thickness_root")
    return root if root else FeatureCheck.param(rib, "thickness")


@register_check(Rulebook.MAX_RIB_THICKNESS_RATIO)
class RibThicknessCheck(FeatureCheck):
    """Rib (and gusset) thickness at the base, as a % of the nominal wall."""

    kinds = (FeatureKind.RIB,)
    skip_subtypes = frozenset({"crush"})

    @property
    def name(self) -> str:
        return "Rib Thickness Check"

    @property
    def progress_text(self) -> str:
        return "Checking rib thickness"

    def measure(self, rib: Feature, pf: PartFeatures) -> Optional[Measure]:
        thickness = root_thickness(rib)
        if not thickness:
            return None
        return Measure(self.percent_of_nominal(thickness, pf), rib.label, rib.faces,
                       size=f"{mm(thickness)} thick at its base")


@register_check(Rulebook.MAX_RIB_HEIGHT_RATIO)
class RibHeightCheck(FeatureCheck):
    """Rib height as a multiple of the nominal wall. Gussets are left out:
    they are braced by what they stand against."""

    kinds = (FeatureKind.RIB,)
    skip_subtypes = frozenset({"crush", "gusset"})

    @property
    def name(self) -> str:
        return "Rib Height Check"

    @property
    def progress_text(self) -> str:
        return "Checking rib heights"

    def measure(self, rib: Feature, pf: PartFeatures) -> Optional[Measure]:
        height = self.param(rib, "height")
        if not height:
            return None
        return Measure(self.times_nominal(height, pf), rib.label, rib.faces,
                       size=f"{mm(height)} tall")
