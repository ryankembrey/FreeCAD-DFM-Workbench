# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Spacing rules: bosses and parallel ribs too close together.

The gap between two features is a thin blade of steel in the mould: it
heats up, wears and is hard to cool. Each pair is reported once, with both
features' faces, as "Boss 2 ↔ Boss 5". Gaps are measured where the features
are widest (at their base, for drafted ones).
"""

from __future__ import annotations

from itertools import combinations
from typing import Any, Iterable, Optional

from ...core.base.feature_check import FeatureCheck, Measure, mm
from ...core.recognition.model import Feature, FeatureKind
from ...core.registries import register_check
from ...core.rules import Rulebook
from ...core.utils import feature_geometry as G
from ...core.utils.part_features import PartFeatures
from .rib_checks import root_thickness

#: Axes this close count as parallel (degrees).
BOSS_AXIS_DEG = 1.0
#: Rib sides this close count as parallel (degrees).
RIB_SIDE_DEG = 5.0


def _pair(a: Feature, b: Feature, value: float, gap: float) -> Measure:
    return Measure(value, f"{a.label} ↔ {b.label}", tuple(sorted(set(a.faces) | set(b.faces))),
                   size=f"{mm(gap)} apart")


def boss_axis(boss: Feature) -> Optional[tuple]:
    """(base point, unit axis from base to top, height, radius at the base)."""
    if boss.frame is None:
        return None
    axis = G.unit(boss.params.get("axis") or boss.frame.axis)
    od, height = FeatureCheck.param(boss, "outer_diameter"), FeatureCheck.param(boss, "height")
    if axis is None or not od or not height:
        return None
    return boss.frame.origin, axis, height, od / 2.0


def boss_gap(a: Feature, b: Feature, face_index=None) -> Optional[float]:
    """Clear gap between two bosses' outer walls, or None when they don't face
    each other (stacked, on opposite sides of a wall, or merged)."""
    pa, pb = boss_axis(a), boss_axis(b)
    if pa is None or pb is None:
        return None
    (oa, axa, ha, ra), (ob, axb, hb, rb) = pa, pb
    if G.parallel(axa, axb, BOSS_AXIS_DEG):
        # Side by side only if they share some height along the axis.
        span_a = (0.0, ha)
        start_b = G.dot(G.sub(ob, oa), axa)
        end_b = start_b + hb * (1.0 if G.dot(axa, axb) > 0 else -1.0)
        if G.overlap(span_a, (min(start_b, end_b), max(start_b, end_b))) <= 0:
            return None
        gap = G.off_axis(ob, oa, axa) - ra - rb
    elif face_index is not None:
        gap = G.min_distance(G.faces_of(face_index, a.faces), G.faces_of(face_index, b.faces))
    else:
        return None
    return gap if gap is not None and gap > 1e-6 else None


def rib_frame(rib: Feature) -> Optional[tuple]:
    """(middle, height axis, length axis, thickness axis) of a rib."""
    frame = rib.frame
    if frame is None or frame.ref is None:
        return None
    h, u = G.unit(frame.axis), G.unit(frame.ref)
    if h is None or u is None:
        return None
    n = G.unit(G.cross(h, u))
    return (frame.origin, h, u, n) if n is not None else None


def rib_gap(a: Feature, b: Feature) -> Optional[float]:
    """Gap between the facing sides of two parallel ribs that run alongside
    each other (they overlap along their length and their height)."""
    fa, fb = rib_frame(a), rib_frame(b)
    if fa is None or fb is None:
        return None
    (oa, ha, ua, na), (ob, hb, _ub, nb) = fa, fb
    if not (G.parallel(na, nb, RIB_SIDE_DEG) and G.parallel(ha, hb, RIB_SIDE_DEG)):
        return None
    ta, tb = root_thickness(a), root_thickness(b)
    la, lb = FeatureCheck.param(a, "length"), FeatureCheck.param(b, "length")
    hta, htb = FeatureCheck.param(a, "height"), FeatureCheck.param(b, "height")
    if not all((ta, tb, la, lb, hta, htb)):
        return None
    d = G.sub(ob, oa)

    def span(centre: float, size: float) -> tuple[float, float]:
        return (centre - size / 2.0, centre + size / 2.0)

    if G.overlap(span(0.0, la), span(G.dot(d, ua), lb)) <= 0:
        return None
    if G.overlap(span(0.0, hta), span(G.dot(d, ha), htb)) <= 0:
        return None
    gap = abs(G.dot(d, na)) - (ta + tb) / 2.0
    return gap if gap > 1e-6 else None


@register_check(Rulebook.MIN_BOSS_SPACING)
class BossSpacingCheck(FeatureCheck):
    """Clear gap between bosses, as a multiple of the nominal wall."""

    kinds = (FeatureKind.BOSS,)

    @property
    def name(self) -> str:
        return "Boss Spacing Check"

    @property
    def progress_text(self) -> str:
        return "Checking the space between bosses"

    def measurements(self, pf: PartFeatures, **kwargs: Any) -> Iterable[Measure]:
        face_index = kwargs.get("face_index")
        for a, b in combinations(self.select(pf), 2):
            gap = boss_gap(a, b, face_index)
            if gap is not None:
                yield _pair(a, b, self.times_nominal(gap, pf), gap)


@register_check(Rulebook.MIN_RIB_SPACING)
class RibSpacingCheck(FeatureCheck):
    """Gap between parallel ribs side by side, as a multiple of the nominal wall."""

    kinds = (FeatureKind.RIB,)
    skip_subtypes = frozenset({"crush"})

    @property
    def name(self) -> str:
        return "Rib Spacing Check"

    @property
    def progress_text(self) -> str:
        return "Checking the space between ribs"

    def measurements(self, pf: PartFeatures, **kwargs: Any) -> Iterable[Measure]:
        for a, b in combinations(self.select(pf), 2):
            gap = rib_gap(a, b)
            if gap is not None:
                yield _pair(a, b, self.times_nominal(gap, pf), gap)
