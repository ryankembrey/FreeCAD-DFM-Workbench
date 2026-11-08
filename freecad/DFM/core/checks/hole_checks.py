# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Hole rules: how deep a cored hole is, and how close it sits to the next
hole or to the edge of its face.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from ...core.base.feature_check import FeatureCheck, Measure, mm
from ...core.recognition.model import Feature, FeatureKind
from ...core.registries import register_check
from ...core.rules import Rulebook
from ...core.utils import feature_geometry as G
from ...core.utils.part_features import PartFeatures

#: A hole's axis this close to the pull or a side action is formed by a core pin.
CORED_AXIS_DEG = 1.0
#: A through hole's pin is held at both ends: its thresholds are doubled.
THROUGH_SCALE = 2.0


def is_threaded(hole: Feature) -> bool:
    return hole.subtype == "threaded" or bool(hole.params.get("threaded"))


def is_cored(hole: Feature, pf: PartFeatures) -> bool:
    """True when the hole's axis is along the pull or a side action, so a core
    pin forms it. Without a pull direction every hole is taken as cored."""
    if pf.directional is None or pf.directional.direction is None:
        return True
    if pf.annotation(hole.id).get("along_pull"):
        return True
    axis = G.unit(hole.params.get("axis") or (hole.frame.axis if hole.frame else (0.0, 0.0, 0.0)))
    return axis is not None and any(G.parallel(axis, G.unit(s), CORED_AXIS_DEG)
                                    for s in pf.side_actions if G.unit(s))


@register_check(Rulebook.MAX_HOLE_DEPTH_RATIO)
class CoredHoleDepthCheck(FeatureCheck):
    """Depth of a cored hole as a multiple of its diameter. Through holes may
    go twice as deep (their pin is held at both ends); threaded holes are
    left out (usually tapped or fitted with an insert)."""

    message_fields = {"hole_kind": '"blind" or "through"'}

    kinds = (FeatureKind.HOLE,)
    skip_subtypes = frozenset({"threaded"})
    uses_nominal = False

    @property
    def name(self) -> str:
        return "Cored Hole Depth Check"

    @property
    def progress_text(self) -> str:
        return "Checking cored hole depths"

    def measure(self, hole: Feature, pf: PartFeatures) -> Optional[Measure]:
        d, depth = self.param(hole, "diameter"), self.param(hole, "depth")
        if not d or not depth or is_threaded(hole) or not is_cored(hole, pf):
            return None
        through = bool(hole.params.get("through"))
        return Measure(
            depth / d, hole.label, hole.faces,
            size=f"Ø{d:.2f} mm and {mm(depth)} deep",
            scale=THROUGH_SCALE if through else 1.0,
            note="through hole: limits doubled" if through else "",
            extra={"hole_kind": "through" if through else "blind"},
        )


@register_check(Rulebook.MIN_HOLE_EDGE_DISTANCE)
class HoleSpacingCheck(FeatureCheck):
    """The gap from a hole's rim to the nearest other hole opening in the same
    face, or to the edge of that face, as a multiple of the nominal wall or
    the hole's diameter, whichever is larger.

    Holes in bosses are left to the boss wall rule.
    """

    message_fields = {
        "reference": "What the gap is compared with: the nominal wall or the hole's diameter",
    }

    kinds = (FeatureKind.HOLE,)

    @property
    def name(self) -> str:
        return "Hole Spacing Check"

    @property
    def progress_text(self) -> str:
        return "Checking the space around holes"

    def select(self, pf: PartFeatures) -> list[Feature]:
        bosses = {b.id for b in pf.result.of_kind(FeatureKind.BOSS)}
        return [h for h in super().select(pf) if h.parent not in bosses]

    def measurements(self, pf: PartFeatures, **kwargs: Any) -> Iterable[Measure]:
        face_index = kwargs.get("face_index")
        if face_index is None:
            return
        holes = self.select(pf)
        # Where each hole opens: {hole id: {entry face: rim edges}}
        rims = {h.id: {e: G.rim(face_index, h.faces, e) for e in h.support_faces} for h in holes}
        rims = {hid: {e: r for e, r in by_face.items() if r} for hid, by_face in rims.items()}
        reported: set[frozenset] = set()
        for hole in holes:
            d = self.param(hole, "diameter")
            if not d:
                continue
            best: Optional[tuple[float, Optional[Feature]]] = None
            for entry, edges in rims[hole.id].items():
                gap = G.outer_wire_distance(face_index, entry, edges)
                if gap is not None and (best is None or gap < best[0]):
                    best = (gap, None)
                for other in holes:
                    theirs = rims[other.id].get(entry) if other.id != hole.id else None
                    if not theirs:
                        continue
                    gap = G.min_distance(G.compound(edges), G.compound(theirs))
                    if gap is not None and gap > 1e-6 and (best is None or gap < best[0]):
                        best = (gap, other)
            if best is None:
                continue
            gap, other = best
            if other is None:
                yield Measure(gap / max(pf.nominal_wall, d), hole.label, hole.faces,
                              size=f"{mm(gap)} from the edge of its face",
                              extra={"reference": _reference(pf.nominal_wall, d)})
                continue
            key = frozenset((hole.id, other.id))
            if key in reported:
                continue
            reported.add(key)
            d_max = max(d, self.param(other, "diameter") or 0.0)
            yield Measure(gap / max(pf.nominal_wall, d_max), f"{hole.label} ↔ {other.label}",
                          tuple(sorted(set(hole.faces) | set(other.faces))),
                          size=f"{mm(gap)} apart",
                          extra={"reference": _reference(pf.nominal_wall, d_max)})


def _reference(nominal: float, diameter: float) -> str:
    """What the gap was compared with, for messages."""
    if diameter >= nominal:
        return f"the hole diameter, {mm(diameter)}"
    return "the nominal wall"
