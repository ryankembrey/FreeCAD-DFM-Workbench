# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Radius rules: the fillet where a boss or rib meets its base, and every
other filleted inside corner.

A sharp root concentrates stress and the flow stalls there; a root fillet
of a fair fraction of the nominal wall fixes both. A boss or rib with no
root fillet has a sharp root: 0%, an error. (Sharp Internal Corners also
flags that edge; these rules name the feature and the radius it needs.)
"""

from __future__ import annotations

from typing import Optional

from ...core.base.feature_check import FeatureCheck, Measure, mm
from ...core.recognition.model import Feature, FeatureKind
from ...core.registries import register_check
from ...core.rules import Rulebook
from ...core.utils.part_features import PartFeatures


def root_measure(check: FeatureCheck, feature: Feature, pf: PartFeatures) -> Measure:
    """The root radius as a % of the nominal wall, with the feature's faces and
    those of its root fillets."""
    radius = check.param(feature, "root_fillet_radius") or 0.0
    bases = set(feature.params.get("base_faces") or ())
    fillets = [f for f in pf.result.of_kind(FeatureKind.FILLET)
               if f.parent == feature.id and (not bases or bases & set(f.support_faces))]
    faces = set(feature.faces).union(*(f.faces for f in fillets)) if fillets else set(feature.faces)
    size = f"{mm(radius)} root radius" if radius > 0 else "sharp root (no fillet)"
    return Measure(check.percent_of_nominal(radius, pf), feature.label, tuple(sorted(faces)),
                   size=size, extra={"radius": mm(radius)})


@register_check(Rulebook.MIN_BOSS_ROOT_RADIUS)
class BossRootRadiusCheck(FeatureCheck):
    """Root fillet of every boss, as a % of the nominal wall."""

    message_fields = {"radius": "The root fillet radius"}

    kinds = (FeatureKind.BOSS,)

    @property
    def name(self) -> str:
        return "Boss Root Radius Check"

    @property
    def progress_text(self) -> str:
        return "Checking boss root radii"

    def measure(self, boss: Feature, pf: PartFeatures) -> Optional[Measure]:
        return root_measure(self, boss, pf)


@register_check(Rulebook.MIN_RIB_ROOT_RADIUS)
class RibRootRadiusCheck(FeatureCheck):
    """Root fillet of every rib and gusset, as a % of the nominal wall."""

    message_fields = {"radius": "The root fillet radius"}

    kinds = (FeatureKind.RIB,)
    skip_subtypes = frozenset({"crush"})

    @property
    def name(self) -> str:
        return "Rib Root Radius Check"

    @property
    def progress_text(self) -> str:
        return "Checking rib root radii"

    def measure(self, rib: Feature, pf: PartFeatures) -> Optional[Measure]:
        return root_measure(self, rib, pf)


#: Fillets that belong to these have rules of their own (root and hole radii).
_OWN_RULES = frozenset({FeatureKind.BOSS, FeatureKind.RIB, FeatureKind.HOLE})


@register_check(Rulebook.MIN_INTERNAL_CORNER_RADIUS)
class InternalCornerRadiusCheck(FeatureCheck):
    """Every filleted inside corner, as a % of the nominal wall (DFMPro: at
    least half the wall). Corners with no fillet at all are Sharp Internal
    Corners; roots of bosses and ribs, and hole bottoms, have their own rules."""

    kinds = (FeatureKind.FILLET,)

    @property
    def name(self) -> str:
        return "Internal Corner Radius Check"

    @property
    def progress_text(self) -> str:
        return "Checking inside corner radii"

    def measure(self, fillet: Feature, pf: PartFeatures) -> Optional[Measure]:
        if fillet.subtype != "concave":
            return None
        owner = pf.feature(fillet.parent)
        if owner is not None and owner.kind in _OWN_RULES:
            return None
        radius = self.param(fillet, "radius_min") or self.param(fillet, "radius")
        if not radius:
            return None
        return Measure(self.percent_of_nominal(radius, pf), fillet.label, fillet.faces,
                       size=f"{mm(radius)} radius")
