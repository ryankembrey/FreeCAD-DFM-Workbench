# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""3D printing rules measured on the part's geometry rather than on
recognised features:

* Minimum Gap: facing walls close enough to fuse shut (PRINT_GAP_ANALYZER);
* Large Flat Face: flat faces large enough to warp, or (resin) to pull hard
  on the tank at each peel (FLAT_FACE_ANALYZER);
* Long Thin Part: the whole part much longer than it is wide, which warps
  (PART_EXTENT_ANALYZER);
* Bed Edge Chamfer: FDM edges on the bed with no chamfer or fillet for the
  first layer to spread into (BED_EDGE_ANALYZER).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Optional

from ...core.base.feature_check import Measure, MeasuredCheck, mm
from ...core.models import CheckResult
from ...core.processes.process import RuleFeedback, RuleLimit
from ...core.registries import register_check
from ...core.rules import Rulebook
from .wall_steel_checks import _faces_label

#: Gaps wider than this many times the threshold aren't gathered (they pass).
GAP_REACH = 4.0


class _Measured(MeasuredCheck):
    """A rule whose analyzer's data become measures (``measures``)."""

    def measures(self, data: Any, thresholds: tuple) -> Iterable[Measure]:
        return ()

    def run_check(
        self,
        analysis_data_map: Any,
        rule_config: RuleLimit,
        rule: Rulebook,
        feedback: Optional[RuleFeedback] = None,
        **kwargs: Any,
    ) -> list[CheckResult]:
        thresholds = self.thresholds(rule, rule_config)
        if thresholds is None or analysis_data_map is None:
            return []
        return self.grade_measures(self.measures(analysis_data_map, thresholds), rule, rule_config,
                                   feedback)


@register_check(Rulebook.MIN_PRINT_GAP)
class PrintGapCheck(_Measured):
    """The narrowest gap between facing walls, one finding per pair of faces.
    Too narrow and the walls fuse: excess resin cures in it, powder sinters
    in it, or FDM lines touch."""

    message_fields = {"faces": 'The two faces, such as "Face8 and Face10"'}

    @property
    def name(self) -> str:
        return "Print Gap Check"

    @property
    def progress_text(self) -> str:
        return "Checking for narrow gaps"

    @property
    def required_analyzer_id(self) -> str:
        return "PRINT_GAP_ANALYZER"

    def measures(self, per_face: dict, thresholds: tuple) -> Iterable[Measure]:
        reach = GAP_REACH * max(t for t in thresholds if t is not None)
        pairs: dict[tuple[int, int], float] = {}
        for (_kind, face), value in per_face.items():
            if not value:
                continue
            gap, other = value[0], value[1]
            if gap > reach:
                continue
            key = (min(face, other), max(face, other))
            pairs[key] = min(gap, pairs.get(key, gap))
        for (a, b), gap in sorted(pairs.items()):
            yield Measure(gap, _faces_label(a, b), (a, b), size=f"{mm(gap)} wide",
                          extra={"faces": f"Face{a} and Face{b}"})


@register_check(Rulebook.MAX_FLAT_FACE_AREA)
class FlatFaceCheck(_Measured):
    """Each flat face larger than the aim (with a print orientation, only
    faces lying flat in the layers)."""

    @property
    def name(self) -> str:
        return "Flat Face Check"

    @property
    def progress_text(self) -> str:
        return "Checking large flat faces"

    @property
    def required_analyzer_id(self) -> str:
        return "FLAT_FACE_ANALYZER"

    def measures(self, per_face: dict, thresholds: tuple) -> Iterable[Measure]:
        for (_kind, face), area in sorted(per_face.items()):
            if area:
                yield Measure(area, f"Face{face}", (face,), size=f"{area:,.0f} mm²")


@register_check(Rulebook.MAX_PART_ASPECT_RATIO)
class LongThinPartCheck(_Measured):
    """The part's length over its width (its smallest bounding box at any
    angle): HP MJF warns of parts over 10 times longer than wide."""

    @property
    def name(self) -> str:
        return "Long Thin Part Check"

    @property
    def progress_text(self) -> str:
        return "Checking the part's proportions"

    @property
    def required_analyzer_id(self) -> str:
        return "PART_EXTENT_ANALYZER"

    def measures(self, extent: Any, thresholds: tuple) -> Iterable[Measure]:
        if not getattr(extent, "width", 0):
            return
        size = f"{extent.length:.1f} × {extent.width:.1f} × {extent.thickness:.1f} mm"
        yield Measure(extent.slenderness, "Part", (), size=size)


@register_check(Rulebook.BED_EDGE_CHAMFER)
class BedEdgeCheck(_Measured):
    """One finding per face on the bed with sharp edges (no chamfer or fillet
    where it meets a wall), highlighting those edges."""

    @property
    def name(self) -> str:
        return "Bed Edge Check"

    @property
    def progress_text(self) -> str:
        return "Checking edges on the print bed"

    @property
    def required_analyzer_id(self) -> str:
        return "BED_EDGE_ANALYZER"

    def measures(self, per_edge: dict, thresholds: tuple) -> Iterable[Measure]:
        sharp: dict[int, list[int]] = defaultdict(list)
        for (_kind, edge), value in sorted(per_edge.items()):
            if value:
                sharp[value[0]].append(edge)
        for bed, edges in sorted(sharp.items()):
            count = f"{len(edges)} sharp edge{'s' if len(edges) != 1 else ''}"
            yield Measure(1.0, f"Face{bed}", (), size=count, overview=f"{count} on the bed",
                          edges=tuple(edges))
