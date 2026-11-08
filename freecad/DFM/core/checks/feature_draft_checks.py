# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Draft for ribs and bosses, judged by their own standard.

Rib sides and boss walls can release with less draft than a part's outer
walls (DFMPro: ribs 0.5° per side at least, boss outsides 0.5°, holes in
bosses 0.25°). While these rules are on, they take those faces over from
Minimum Draft Angle (``supersedes``), so a rib side isn't reported twice
against two different limits.

Only features standing along the pull direction are judged: across it,
their sides are formed by a side action or are undercuts.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional

from ...core.base.feature_check import FeatureCheck, Measure
from ...core.recognition.model import Feature, FeatureKind
from ...core.registries import register_check
from ...core.rules import Rulebook
from ...core.utils import feature_geometry as G
from ...core.utils.geometry import get_face_uv_center, get_face_uv_normal
from ...core.utils.part_features import PartFeatures
from .boss_checks import inner_hole
from .spacing_checks import rib_frame

#: A rib whose sides face within this of square to the pull runs along it (degrees).
ALONG_PULL_DEG = 10.0
#: A rib face turned at least this close to its thickness axis is a side.
SIDE_DEG = 30.0
#: Holes in bosses may have this share of the boss's draft (DFMPro: 0.25° vs 0.5°).
HOLE_SCALE = 0.5


def _degrees(value: float) -> str:
    return f"{value:.2f}°"


def _pull(pf: PartFeatures) -> Optional[tuple]:
    if pf.directional is None or pf.directional.direction is None:
        return None
    return G.unit(pf.directional.direction)


def rib_sides(rib: Feature, face_index) -> list[int]:
    """The faces of a rib that are its sides (not its ends or top)."""
    frame = rib_frame(rib)
    if frame is None or face_index is None:
        return []
    thickness_axis = frame[3]
    out = []
    for n in rib.faces:
        face = face_index.face_at(n)
        normal = get_face_uv_normal(face, *get_face_uv_center(face))
        if normal is not None and G.parallel((normal.X(), normal.Y(), normal.Z()), thickness_axis, SIDE_DEG):
            out.append(n)
    return out


@register_check(Rulebook.MIN_RIB_DRAFT)
class RibDraftCheck(FeatureCheck):
    """The smaller draft, against the pull, of a rib's two sides: for every rib
    whose sides run along the pull, whichever face it stands on (a rib tied
    to a floor and a side wall may be recognised as standing on the wall)."""

    kinds = (FeatureKind.RIB,)
    skip_subtypes = frozenset({"crush"})
    uses_nominal = False
    supersedes = (Rulebook.MIN_DRAFT_ANGLE,)

    @property
    def name(self) -> str:
        return "Rib Draft Check"

    @property
    def progress_text(self) -> str:
        return "Checking rib draft"

    def judged(self, pf: PartFeatures, face_index) -> list[tuple[Feature, list[int]]]:
        """(rib, its side faces) for the ribs whose sides run along the pull."""
        pull = _pull(pf)
        if pull is None or face_index is None:
            return []
        out = []
        for rib in self.select(pf):
            frame = rib_frame(rib)
            if frame is None or abs(G.dot(frame[3], pull)) > math.sin(math.radians(ALONG_PULL_DEG)):
                continue  # its sides face along the pull: a side action's job, or an undercut
            sides = [n for n in rib_sides(rib, face_index) if n in pf.directional.face_draft]
            if sides:
                out.append((rib, sides))
        return out

    def measurements(self, pf: PartFeatures, **kwargs: Any) -> Iterable[Measure]:
        for rib, sides in self.judged(pf, kwargs.get("face_index")):
            draft = min(abs(pf.directional.face_draft[n]) for n in sides)
            yield Measure(draft, rib.label, tuple(sides), size=f"drafted {_degrees(draft)} on its flatter side")

    def covered_faces(self, analysis_data, **kwargs) -> set[int]:
        if not isinstance(analysis_data, PartFeatures):
            return set()
        return {n for _rib, sides in self.judged(analysis_data, kwargs.get("face_index")) for n in sides}


@register_check(Rulebook.MIN_BOSS_DRAFT)
class BossDraftCheck(FeatureCheck):
    """Draft of each boss's outer wall, and of the hole in a screw boss, for
    bosses along the pull. The hole's limits are half the boss's."""

    kinds = (FeatureKind.BOSS,)
    uses_nominal = False
    supersedes = (Rulebook.MIN_DRAFT_ANGLE,)

    @property
    def name(self) -> str:
        return "Boss Draft Check"

    @property
    def progress_text(self) -> str:
        return "Checking boss draft"

    def judged(self, pf: PartFeatures) -> list[Feature]:
        if _pull(pf) is None:
            return []
        return [b for b in self.select(pf) if pf.annotation(b.id).get("along_pull")]

    def measurements(self, pf: PartFeatures, **kwargs: Any) -> Iterable[Measure]:
        for boss in self.judged(pf):
            outer = pf.annotation(boss.id).get("draft_min_deg")
            if outer is None:
                outer = self.param(boss, "taper_deg") or 0.0
            yield Measure(outer, boss.label, boss.faces, size=f"drafted {_degrees(outer)} outside")
            hole = inner_hole(boss, pf)
            if hole is None:
                continue
            inner = pf.annotation(hole.id).get("draft_min_deg")
            if inner is None:
                inner = self.param(hole, "wall_taper_deg") or 0.0
            yield Measure(inner, hole.label, hole.faces, size=f"drafted {_degrees(inner)} in its bore",
                          scale=HOLE_SCALE, note=f"in {boss.label}: limits halved")

    def covered_faces(self, analysis_data, **kwargs) -> set[int]:
        if not isinstance(analysis_data, PartFeatures):
            return set()
        out: set[int] = set()
        for boss in self.judged(analysis_data):
            out.update(boss.faces)
            hole = inner_hole(boss, analysis_data)
            if hole is not None:
                out.update(hole.faces)
        return out
