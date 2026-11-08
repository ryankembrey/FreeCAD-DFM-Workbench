# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Boss rules: wall, height, core depth, and bosses standing on their own.

Bosses are moulded drafted, so they are measured at their base, where they
are widest: the recogniser gives the outer diameter there, and a drafted
bore's diameter at its mid depth (see ``wall_at_base``).
"""

from __future__ import annotations

import math
from typing import Optional

from ...core.base.feature_check import FeatureCheck, Measure, mm
from ...core.recognition.model import Feature, FeatureKind
from ...core.registries import register_check
from ...core.rules import Rulebook
from ...core.utils.part_features import PartFeatures


def _number(feature: Feature, name: str, default: float = 0.0) -> float:
    value = FeatureCheck.param(feature, name)
    return default if value is None else value


def core_reach(boss: Feature, hole: Feature) -> float:
    """How far down the boss its hole reaches from the top (mm, at most the
    boss's height: a through hole, or one deeper than the boss, reaches the base)."""
    height = _number(boss, "height")
    if hole.params.get("through"):
        return height
    return min(_number(hole, "depth"), height)


def wall_at_base(boss: Feature, hole: Feature) -> Optional[float]:
    """The boss wall where it is thickest: at the bottom of its core.

    The outer wall tapers in towards the top (``taper_deg``), and a drafted
    bore opens up towards the top (``wall_taper_deg``; its ``diameter`` is
    at mid depth), so the wall is thickest where the core stops: at the
    base when the core reaches it, else at the bottom of the hole.
    """
    od = FeatureCheck.param(boss, "outer_diameter")
    d = FeatureCheck.param(hole, "diameter")
    if od is None or d is None:
        return None
    height = _number(boss, "height")
    depth = _number(hole, "depth")
    reach = core_reach(boss, hole)
    od_there = od - 2.0 * (height - reach) * math.tan(math.radians(_number(boss, "taper_deg")))
    k = math.tan(math.radians(_number(hole, "wall_taper_deg")))
    d_there = d + depth * k - 2.0 * reach * k  # from the entry (top) down to the reach
    return (od_there - d_there) / 2.0


def inner_hole(boss: Feature, pf: PartFeatures) -> Optional[Feature]:
    hole = pf.feature(boss.params.get("inner_hole"))
    return hole if hole is not None and hole.kind == FeatureKind.HOLE else None


def boss_faces(boss: Feature, hole: Optional[Feature]) -> tuple[int, ...]:
    """The boss's faces and its hole's."""
    return tuple(sorted(set(boss.faces) | set(hole.faces if hole else ())))


@register_check(Rulebook.MAX_BOSS_WALL_RATIO)
class BossWallCheck(FeatureCheck):
    """Boss wall (or a solid boss's diameter) as a % of the nominal wall.

    Pins are left out: a slender solid pin is meant to be solid.
    """

    kinds = (FeatureKind.BOSS,)
    skip_subtypes = frozenset({"pin"})

    @property
    def name(self) -> str:
        return "Boss Wall Check"

    @property
    def progress_text(self) -> str:
        return "Checking boss walls"

    def measure(self, boss: Feature, pf: PartFeatures) -> Optional[Measure]:
        od = self.param(boss, "outer_diameter")
        if not od:
            return None
        hole = inner_hole(boss, pf)
        if hole is None:
            # Solid: the whole boss is one thick section.
            return Measure(self.percent_of_nominal(od, pf), boss.label, boss_faces(boss, None),
                           size=f"solid {mm(od)} section")
        wall = wall_at_base(boss, hole)
        if wall is None or wall <= 0:
            return None
        return Measure(self.percent_of_nominal(wall, pf), boss.label, boss_faces(boss, hole),
                       size=f"{mm(wall)} wall")


@register_check(Rulebook.MAX_BOSS_HEIGHT_RATIO)
class BossHeightCheck(FeatureCheck):
    """Boss height as a multiple of its outer diameter.

    Pins (covered by their own slenderness) and columns (held at both ends)
    are left out.
    """

    kinds = (FeatureKind.BOSS,)
    skip_subtypes = frozenset({"pin", "column"})
    uses_nominal = False

    @property
    def name(self) -> str:
        return "Boss Height Check"

    @property
    def progress_text(self) -> str:
        return "Checking boss heights"

    def measure(self, boss: Feature, pf: PartFeatures) -> Optional[Measure]:
        od, height = self.param(boss, "outer_diameter"), self.param(boss, "height")
        if not od or not height:
            return None
        return Measure(height / od, boss.label, boss_faces(boss, inner_hole(boss, pf)),
                       size=f"{mm(height)} tall with a {mm(od)} outer diameter")


@register_check(Rulebook.MIN_BOSS_CORE_DEPTH)
class BossCoreDepthCheck(FeatureCheck):
    """How far a screw boss's hole reaches towards its base, as a % of the
    boss's height. A core that stops short leaves a solid plug at the base."""

    kinds = (FeatureKind.BOSS,)
    uses_nominal = False

    @property
    def name(self) -> str:
        return "Boss Core Depth Check"

    @property
    def progress_text(self) -> str:
        return "Checking boss cores"

    def measure(self, boss: Feature, pf: PartFeatures) -> Optional[Measure]:
        hole = inner_hole(boss, pf)
        height = self.param(boss, "height")
        if hole is None or not height:
            return None
        if _from_the_base(boss, hole):
            return None  # cored from underneath: nothing is left at the base
        if hole.params.get("through"):
            value = 100.0
        else:
            value = 100.0 * _number(hole, "depth") / height
        return Measure(value, boss.label, boss_faces(boss, hole),
                       size=f"{mm(_number(hole, 'depth'))} deep in a {mm(height)} tall boss")


def _from_the_base(boss: Feature, hole: Feature) -> bool:
    """True when a blind hole goes into the boss from its base side.

    A boss's axis points from its base to its top; a hole's into the hole.
    """
    if hole.params.get("through"):
        return False
    a, b = boss.params.get("axis"), hole.params.get("axis")
    if not (isinstance(a, tuple) and isinstance(b, tuple) and len(a) == 3 and len(b) == 3):
        return False
    return sum(x * y for x, y in zip(a, b)) > 0.5


@register_check(Rulebook.STANDALONE_BOSS)
class StandaloneBossCheck(FeatureCheck):
    """Bosses taller than their diameter tied to nothing: no side wall, no
    gusset and no rib running into them."""

    kinds = (FeatureKind.BOSS,)
    skip_subtypes = frozenset({"pin", "column"})
    uses_nominal = False

    @property
    def name(self) -> str:
        return "Standalone Boss Check"

    @property
    def progress_text(self) -> str:
        return "Checking for standalone bosses"

    def measure(self, boss: Feature, pf: PartFeatures) -> Optional[Measure]:
        od, height = self.param(boss, "outer_diameter"), self.param(boss, "height")
        if not od or not height or height <= od:
            return None
        if boss.params.get("wall_faces") or _number(boss, "gusset_count") > 0:
            return None
        ribs = pf.result.of_kind(FeatureKind.RIB)
        if any(r.parent == boss.id or boss.id in r.interacts_with for r in ribs):
            return None
        return Measure(1.0, boss.label, boss_faces(boss, inner_hole(boss, pf)),
                       size=f"{mm(height)} tall", overview="not tied to a wall or rib")
