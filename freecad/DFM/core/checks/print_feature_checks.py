# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""3D printing rules measured on recognised features, for FDM, resin and
powder bed processes:

* holes: small, horizontal (FDM), deep and narrow;
* pins: thin, slender, with no fillet at the base;
* lettering: shallow, thin strokes, small characters;
* hollows (resin, powder): sealed, or with too few or too small escape
  holes; recesses that cup against the resin tank.

They read PRINT_FEATURE_ANALYZER: the same recognition as the moulding
rules, annotated for the print direction (when there is one) instead of a
pull.
"""

from __future__ import annotations

from typing import Any, Optional

import math

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
#: Characters smaller than this are hard to read once printed (mm). FDM:
#: Xometry's 3.5 mm. Resin and powder print finer: Xometry SLA and HLH SLS
#: recommend 2 mm (HP MJF: 2.1 mm).
MIN_CHARACTER_HEIGHT = 3.5
MIN_CHARACTER_HEIGHTS = {"RESIN": 2.0, "POWDER_BED": 2.0}
#: Pins thinner than this need a fillet at the base (mm; Hubs / FacFox FDM
#: guide: "pins smaller than 5 mm diameter").
ROOT_FILLET_BELOW = 5.0
#: Through holes may be this many times longer than blind holes (Xometry
#: LSPc: blind holes under 3x their diameter, through holes under 8x).
THROUGH_HOLE_SCALE = 8.0 / 3.0


def min_character_height(archetype) -> float:
    """The smallest legible character height for a process family (mm)."""
    return MIN_CHARACTER_HEIGHTS.get(getattr(archetype, "name", ""), MIN_CHARACTER_HEIGHT)


def pin_size(boss: Feature) -> Optional[tuple[float, float]]:
    """(diameter, height) of a solid post at least twice as tall as wide, or None."""
    od, height = FeatureCheck.param(boss, "outer_diameter"), FeatureCheck.param(boss, "height")
    if not od or not height or height < PIN_SLENDERNESS * od or boss.params.get("inner_hole"):
        return None
    return od, height


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


class PinCheck(PrintFeatureCheck):
    """The pins: solid posts at least twice as tall as wide."""

    kinds = (FeatureKind.BOSS,)
    skip_subtypes = frozenset({"screw"})


@register_check(Rulebook.MIN_PIN_DIAMETER)
class PinDiameterCheck(PinCheck):
    """Diameter of solid posts at least twice as tall as they are wide."""

    @property
    def name(self) -> str:
        return "Pin Diameter Check"

    @property
    def progress_text(self) -> str:
        return "Checking printed pins"

    def measure(self, boss: Feature, pf: PartFeatures) -> Optional[Measure]:
        size = pin_size(boss)
        if size is None:
            return None
        od, height = size
        return Measure(od, boss.label, boss.faces, size=f"{mm(od)} across and {mm(height)} tall")


@register_check(Rulebook.MAX_PIN_SLENDERNESS)
class PinSlendernessCheck(PinCheck):
    """A pin's height as a multiple of its diameter: tall thin pins wobble as
    they print and snap in handling (Formlabs: a 0.3 mm wire prints 7 mm tall,
    a 1.5 mm one 30 mm, about 20 diameters)."""

    @property
    def name(self) -> str:
        return "Pin Slenderness Check"

    @property
    def progress_text(self) -> str:
        return "Checking tall thin pins"

    def measure(self, boss: Feature, pf: PartFeatures) -> Optional[Measure]:
        size = pin_size(boss)
        if size is None:
            return None
        od, height = size
        return Measure(height / od, boss.label, boss.faces, size=f"{mm(height)} tall and {mm(od)} across")


@register_check(Rulebook.PIN_ROOT_FILLET)
class PinRootFilletCheck(PinCheck):
    """Pins under 5 mm across with no fillet at the base, where they snap
    (Hubs; Formlabs SLS: fillet thin features where they meet the part)."""

    @property
    def name(self) -> str:
        return "Pin Root Fillet Check"

    @property
    def progress_text(self) -> str:
        return "Checking pin bases"

    def measure(self, boss: Feature, pf: PartFeatures) -> Optional[Measure]:
        size = pin_size(boss)
        if size is None or size[0] >= ROOT_FILLET_BELOW or self.param(boss, "root_fillet_radius"):
            return None
        od, height = size
        return Measure(1.0, boss.label, boss.faces, size=f"{mm(od)} across and {mm(height)} tall",
                       overview="no fillet at its base")


@register_check(Rulebook.MIN_PRINTED_TEXT_DEPTH)
class PrintedTextCheck(PrintFeatureCheck):
    """How far lettering stands out from or is cut into the surface. Besides
    its thresholds it warns of characters too small to read."""

    message_fields = {"relief": '"height" (raised text) or "depth" (cut-in text)'}

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
        smallest = min_character_height(kwargs.get("_archetype"))
        for text in self.select(analysis_data_map):
            size = self.param(text, "glyph_size")
            if size is None or size >= smallest:
                continue
            results.append(CheckResult(
                rule_id=rule, severity=Severity.WARNING,
                overview=f"{text.label}: characters {mm(size)} tall",
                message=(f"Small Printed Text: the characters of {text.label} are about {mm(size)} "
                         f"tall, under {mm(smallest)}. Letters this small blur, fill in or lose "
                         "their shape when printed. Enlarge the text and use a bold, plain "
                         "(sans-serif) font."),
                ignore=False, value=float(size), limit=smallest, unit="mm",
                failing_geometry=[("Face", n) for n in text.faces],
            ))
        return results


@register_check(Rulebook.MIN_PRINTED_TEXT_STROKE)
class PrintedTextStrokeCheck(PrintFeatureCheck):
    """The thinnest stroke of printed lettering: raised strokes thinner than
    the process can build don't print, and cut-in ones fill in.

    It judges the gaps of cut-in lettering instead of Minimum Gap, which
    would otherwise report each of its grooves too."""

    message_fields = {
        "fault": 'What happens to strokes this thin: "may not print" or "may fill in"',
    }

    kinds = (FeatureKind.TEXT,)
    supersedes = (Rulebook.MIN_PRINT_GAP,)

    @property
    def name(self) -> str:
        return "Printed Text Stroke Check"

    @property
    def progress_text(self) -> str:
        return "Checking printed letter strokes"

    def measure(self, text: Feature, pf: PartFeatures) -> Optional[Measure]:
        stroke = self.param(text, "min_stroke")
        if not stroke:
            return None
        raised = text.subtype == "embossed"
        return Measure(stroke, text.label, text.faces,
                       size=f"{'raised' if raised else 'cut-in'} strokes {mm(stroke)} wide",
                       extra={"fault": "may not print" if raised else "may fill in"})

    def covered_faces(self, analysis_data, **kwargs: Any) -> set[int]:
        if not isinstance(analysis_data, PartFeatures):
            return set()
        return {n for text in self.select(analysis_data) for n in text.faces}


# -- Hollows and recesses (resin, powder bed) ------------------------------------------------------


@register_check(Rulebook.SEALED_CAVITY)
class SealedCavityCheck(PrintFeatureCheck):
    """A hollow inside the part with no opening: the resin or powder it is
    printed full of stays in. Resin left inside can crack the part later
    (Formlabs: "explosion of the print"); powder only adds weight, and is
    sometimes left in on purpose, so the processes set different severities."""

    kinds = (FeatureKind.VOID,)

    @property
    def name(self) -> str:
        return "Sealed Cavity Check"

    @property
    def progress_text(self) -> str:
        return "Checking for sealed hollows"

    def measure(self, void: Feature, pf: PartFeatures) -> Optional[Measure]:
        if void.params.get("escape_holes"):
            return None
        size = self.param(void, "max_size") or 0.0
        return Measure(1.0, void.label, void.faces, size=f"{mm(size)} across",
                       overview="sealed, no escape hole")


@register_check(Rulebook.MIN_ESCAPE_HOLE_DIAMETER)
class EscapeHoleCheck(PrintFeatureCheck):
    """The smallest escape hole of a hollow vented only through holes. Besides
    its thresholds it warns of a hollow with a single hole: resin or powder
    leaves through one while air comes in through another (Formlabs Fuse,
    Xometry MJF: at least two, on opposite sides)."""

    message_fields = {
        "holes": 'How many escape holes and the smallest, such as "2 escape holes, the smallest Ø3.00 mm"',
    }

    kinds = (FeatureKind.VOID,)

    @property
    def name(self) -> str:
        return "Escape Hole Check"

    @property
    def progress_text(self) -> str:
        return "Checking escape holes"

    @staticmethod
    def _faces(void: Feature) -> tuple[int, ...]:
        """The hollow's faces and its escape holes' (the support faces)."""
        return tuple(void.faces) + tuple(n for n in void.support_faces if n not in void.faces)

    def measure(self, void: Feature, pf: PartFeatures) -> Optional[Measure]:
        count = int(void.params.get("escape_holes") or 0)
        d = self.param(void, "min_escape_diameter")
        if not count or not d:
            return None
        holes = f"one escape hole, Ø{d:.2f} mm" if count == 1 else \
            f"{count} escape holes, the smallest Ø{d:.2f} mm"
        return Measure(d, void.label, self._faces(void), size=f"Ø{d:.2f} mm",
                       extra={"holes": holes})

    def run_check(
        self,
        analysis_data_map: Any,
        rule_config: RuleLimit,
        rule: Rulebook,
        feedback: Optional[RuleFeedback] = None,
        **kwargs: Any,
    ) -> list[CheckResult]:
        results = super().run_check(analysis_data_map, rule_config, rule, feedback, **kwargs)
        if not isinstance(analysis_data_map, PartFeatures) or self.thresholds(rule, rule_config) is None:
            return results
        for void in self.select(analysis_data_map):
            if int(void.params.get("escape_holes") or 0) != 1:
                continue
            results.append(CheckResult(
                rule_id=rule, severity=Severity.WARNING,
                overview=f"{void.label}: one escape hole",
                message=(f"Single Escape Hole: {void.label} has only one escape hole. Resin or "
                         "powder has to leave through it while air comes in the same way, so it "
                         "drains slowly and some stays behind. Add a second hole on the far side "
                         "of the hollow, so air or the cleaning blast can flow through."),
                ignore=False, value=1.0, limit=2.0, unit="",
                failing_geometry=[("Face", n) for n in self._faces(void)],
            ))
        return results


@register_check(Rulebook.MAX_CUP_AREA)
class CuppingCheck(PrintFeatureCheck):
    """Resin: the opening of a recess that seals against the tank film (or
    the build platform) as it prints, so each peel pulls a vacuum in it
    (Formlabs, "cupping"). Small cups and ones with thick walls usually print;
    large ones bow in, blow out or tear the part off its supports.

    Recesses that open towards the platform cup only when printed directly on
    it (not on supports): those findings are warnings at most."""

    message_fields = {"sealed_by": "What seals the cup: the tank's film, or the build platform"}

    kinds = (FeatureKind.HOLE, FeatureKind.POCKET, FeatureKind.CUTOUT)

    @property
    def name(self) -> str:
        return "Cupping Check"

    @property
    def progress_text(self) -> str:
        return "Checking for suction cups"

    def measure(self, feature: Feature, pf: PartFeatures) -> Optional[Measure]:
        ann = pf.annotation(feature.id)
        where, area = ann.get("cup"), ann.get("cup_area")
        if not where or not area:
            return None
        if feature.kind == FeatureKind.HOLE:
            d = self.param(feature, "diameter") or 2.0 * math.sqrt(area / math.pi)
            size = f"Ø{d:.2f} mm"
        else:
            size = f"{area:,.0f} mm² open"
        on_platform = where == "platform"
        return Measure(
            area, feature.label, feature.faces, size=size,
            note="on the platform" if on_platform else "",
            cap=Severity.WARNING if on_platform else None,
            extra={"sealed_by": ("the build platform, when printed directly on it" if on_platform
                                 else "the resin tank's film at every layer")},
        )


@register_check(Rulebook.MAX_PRINTED_HOLE_DEPTH)
class PrintedHoleDepthCheck(PrintFeatureCheck):
    """A hole's depth as a multiple of its diameter: deep narrow holes trap
    uncured resin or unfused powder (Xometry LSPc: blind holes under 3x,
    through holes under 8x). Through holes may be 8/3 as long."""

    message_fields = {"hole_kind": '"blind" or "through"'}

    kinds = (FeatureKind.HOLE,)

    @property
    def name(self) -> str:
        return "Printed Hole Depth Check"

    @property
    def progress_text(self) -> str:
        return "Checking deep printed holes"

    def measure(self, hole: Feature, pf: PartFeatures) -> Optional[Measure]:
        d, depth = self.param(hole, "diameter"), self.param(hole, "depth")
        if not d or not depth:
            return None
        through = bool(hole.params.get("through"))
        return Measure(
            depth / d, hole.label, hole.faces,
            size=f"Ø{d:.2f} mm and {mm(depth)} {'long' if through else 'deep'}",
            scale=THROUGH_HOLE_SCALE if through else 1.0,
            note="through hole: longer allowed" if through else "",
            extra={"hole_kind": "through" if through else "blind"},
        )
