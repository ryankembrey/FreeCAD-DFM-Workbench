# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""3D printing (FDM) rules measured on recognised features: small holes,
horizontal holes, slender pins and lettering.

They read PRINT_FEATURE_ANALYZER: the same recognition as the moulding
rules, annotated for the print direction and nozzle instead of a pull.
"""

from __future__ import annotations

from typing import Any, Optional

from ...core.base.feature_check import FeatureCheck, Measure, mm
from ...core.models import CheckResult, Severity
from ...core.processes.process import RuleFeedback, RuleLimit
from ...core.recognition.model import Feature, FeatureKind
from ...core.registries import register_check
from ...core.rules import Rulebook
from ...core.utils.part_features import PartFeatures

#: A boss at least this many diameters tall is a pin (Formlabs: a pin is at
#: least twice as long as it is wide).
PIN_SLENDERNESS = 2.0
#: Characters smaller than this are hard to read once printed (mm; Xometry).
MIN_CHARACTER_HEIGHT = 3.5


class PrintFeatureCheck(FeatureCheck):
    uses_nominal = False

    @property
    def required_analyzer_id(self) -> str:
        return "PRINT_FEATURE_ANALYZER"


@register_check(Rulebook.MIN_PRINTED_HOLE_DIAMETER)
class PrintedHoleCheck(PrintFeatureCheck):
    """Hole diameter: small holes print undersized or close up."""

    kinds = (FeatureKind.HOLE,)

    @property
    def name(self) -> str:
        return "Printed Hole Check"

    @property
    def progress_text(self) -> str:
        return "Checking small printed holes"

    def measure(self, hole: Feature, pf: PartFeatures) -> Optional[Measure]:
        d = self.param(hole, "diameter")
        if not d:
            return None
        return Measure(d, hole.label, hole.faces, size=f"Ø{d:.2f} mm")


@register_check(Rulebook.HORIZONTAL_HOLE_TEARDROP)
class HorizontalHoleCheck(PrintFeatureCheck):
    """Round holes lying across the print direction, wide enough that their top
    must bridge (the recognition preference "Teardrop from hole diameter"),
    without a teardrop or diamond top."""

    kinds = (FeatureKind.HOLE,)

    @property
    def name(self) -> str:
        return "Horizontal Hole Check"

    @property
    def progress_text(self) -> str:
        return "Checking horizontal holes"

    def measure(self, hole: Feature, pf: PartFeatures) -> Optional[Measure]:
        if hole.params.get("profile") == "teardrop" or not pf.annotation(hole.id).get("teardrop_recommended"):
            return None
        d = self.param(hole, "diameter") or 0.0
        return Measure(1.0, hole.label, hole.faces, size=f"Ø{d:.2f} mm",
                       overview="round hole across the print direction")


@register_check(Rulebook.MIN_PIN_DIAMETER)
class PinDiameterCheck(PrintFeatureCheck):
    """Diameter of solid posts at least twice as tall as they are wide."""

    kinds = (FeatureKind.BOSS,)
    skip_subtypes = frozenset({"screw"})

    @property
    def name(self) -> str:
        return "Pin Diameter Check"

    @property
    def progress_text(self) -> str:
        return "Checking printed pins"

    def measure(self, boss: Feature, pf: PartFeatures) -> Optional[Measure]:
        od, height = self.param(boss, "outer_diameter"), self.param(boss, "height")
        if not od or not height or height < PIN_SLENDERNESS * od or boss.params.get("inner_hole"):
            return None
        return Measure(od, boss.label, boss.faces, size=f"{mm(od)} across and {mm(height)} tall")


@register_check(Rulebook.MIN_PRINTED_TEXT_DEPTH)
class PrintedTextCheck(PrintFeatureCheck):
    """How far lettering stands out from or is cut into the surface. Besides
    its thresholds it warns of characters too small to read."""

    kinds = (FeatureKind.TEXT,)

    @property
    def name(self) -> str:
        return "Printed Text Check"

    @property
    def progress_text(self) -> str:
        return "Checking printed text"

    def measure(self, text: Feature, pf: PartFeatures) -> Optional[Measure]:
        depth = self.param(text, "depth")
        if not depth:
            return None
        how = "raised" if text.subtype == "embossed" else "deep"
        return Measure(depth, text.label, text.faces, size=f"{mm(depth)} {how}",
                       extra={"relief": "height" if text.subtype == "embossed" else "depth"})

    def run_check(
        self,
        analysis_data_map: Any,
        rule_config: RuleLimit,
        rule: Rulebook,
        feedback: Optional[RuleFeedback] = None,
        **kwargs: Any,
    ) -> list[CheckResult]:
        results = super().run_check(analysis_data_map, rule_config, rule, feedback, **kwargs)
        if not isinstance(analysis_data_map, PartFeatures):
            return results
        for text in self.select(analysis_data_map):
            size = self.param(text, "glyph_size")
            if size is None or size >= MIN_CHARACTER_HEIGHT:
                continue
            results.append(CheckResult(
                rule_id=rule, severity=Severity.WARNING,
                overview=f"{text.label}: characters {mm(size)} tall",
                message=(f"Small Printed Text: the characters of {text.label} are about {mm(size)} "
                         f"tall, under {mm(MIN_CHARACTER_HEIGHT)}. Small letters lose their shape "
                         "within a few extrusion widths. Enlarge the text and use a bold, plain "
                         "(sans-serif) font; text on the top or bottom of the print reads best."),
                ignore=False, value=float(size), limit=MIN_CHARACTER_HEIGHT, unit="mm",
                failing_geometry=[("Face", n) for n in text.faces],
            ))
        return results
