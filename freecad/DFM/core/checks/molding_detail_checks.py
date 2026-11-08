# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Moulded details: living hinges, cantilever snap fits and lettering."""

from __future__ import annotations

import re
from typing import Any, Optional

from ...core.base.feature_check import FeatureCheck, Measure, mm
from ...core.models import CheckResult, Severity
from ...core.processes.process import RuleFeedback, RuleLimit
from ...core.recognition.model import Feature, FeatureKind
from ...core.registries import register_check
from ...core.rules import Rulebook
from ...core.utils.part_features import PartFeatures

#: A hinge web thinner than this may not fill (mm).
MIN_HINGE_WEB = 0.2
#: Materials a living hinge works in: the polyolefins.
_HINGE_MATERIALS = {"PP", "PE", "HDPE", "LDPE", "POLYPROPYLENE", "POLYETHYLENE"}


def hinge_material_ok(material) -> Optional[bool]:
    """True for polypropylene or polyethylene (by name or category), False for
    another material, None when unknown (no material, or Default)."""
    if material is None or getattr(material, "name", "Default") == "Default":
        return None
    words = set(re.split(r"[^A-Z]+", f"{material.name} {material.category}".upper()))
    return bool(words & _HINGE_MATERIALS) or "POLYOLEFIN" in material.category.upper()


@register_check(Rulebook.MAX_LIVING_HINGE_WEB)
class LivingHingeCheck(FeatureCheck):
    """The web of every living hinge: too thick and it cracks when flexed.

    Besides its thresholds it warns of a web too thin to fill (under 0.2 mm)
    and of a material other than PP or PE, which few hinges survive.
    """

    kinds = (FeatureKind.LIVING_HINGE,)
    uses_nominal = False

    @property
    def name(self) -> str:
        return "Living Hinge Check"

    @property
    def progress_text(self) -> str:
        return "Checking living hinges"

    def measure(self, hinge: Feature, pf: PartFeatures) -> Optional[Measure]:
        web = self.param(hinge, "web_thickness")
        if not web:
            return None
        return Measure(web, hinge.label, hinge.faces, size=f"{mm(web)} thick")

    def run_check(
        self,
        analysis_data_map: Any,
        rule_config: RuleLimit,
        rule: Rulebook,
        feedback: Optional[RuleFeedback] = None,
        **kwargs: Any,
    ) -> list[CheckResult]:
        results = super().run_check(analysis_data_map, rule_config, rule, feedback, **kwargs)
        pf = analysis_data_map
        if not isinstance(pf, PartFeatures):
            return results
        material = kwargs.get("_target_material")
        suits = hinge_material_ok(material)
        for hinge in self.select(pf):
            web = self.param(hinge, "web_thickness")
            if web is not None and web < MIN_HINGE_WEB:
                results.append(self._note(
                    rule, hinge, web, f"{hinge.label}: web {self.format_number(web, 'mm')} may not fill",
                    f"Living Hinge Web Too Thin ({self.format_number(web, 'mm')}): {hinge.label} has a "
                    f"web under {self.format_number(MIN_HINGE_WEB, 'mm')}, which the plastic may not fill "
                    "before it freezes. Hinges are usually 0.2 to 0.5 mm thick; gate so the flow crosses "
                    "the hinge in one direction."))
            if suits is False:
                results.append(self._note(
                    rule, hinge, web or 0.0, f"{hinge.label}: {material.name} is not a hinge material",
                    f"Living Hinge in {material.name}: {hinge.label} is a living hinge, but few materials "
                    "survive repeated flexing. Living hinges are moulded in polypropylene (PP) or "
                    "polyethylene (PE); in other plastics they crack after a few cycles. Use PP or PE, "
                    "or a separate pin hinge."))
        return results

    @staticmethod
    def _note(rule: Rulebook, hinge: Feature, web: float, overview: str, message: str) -> CheckResult:
        return CheckResult(
            rule_id=rule, severity=Severity.WARNING, overview=overview, message=message, ignore=False,
            value=float(web), limit=0.0, unit=rule.unit,
            failing_geometry=[("Face", n) for n in hinge.faces],
        )


#: Cantilever deflection y = K · ε · l² / h (Bayer / Covestro, "Snap-Fit Joints
#: for Plastics", Table 1): K for a beam of constant thickness, and for one
#: whose thickness falls linearly to half at the hook.
K_CONSTANT = 0.67
K_HALF_TAPER = 1.09


def root_strain(snap: Feature) -> Optional[float]:
    """The strain (%) at a snap-fit beam's root as it deflects by its overhang.

    ε = h · y / (K · l²), with K between 0.67 (constant thickness) and 1.09
    (tapering to half at the tip), straight-line between for other tapers;
    a beam tapering further is taken as tapering to half (an upper bound).
    """
    h = FeatureCheck.param(snap, "thickness")
    y = FeatureCheck.param(snap, "overhang")
    length = FeatureCheck.param(snap, "length")
    if not h or not y or not length:
        return None
    tip = FeatureCheck.param(snap, "thickness_tip") or h
    ratio = min(1.0, max(0.5, tip / h))
    k = K_CONSTANT + (1.0 - ratio) / 0.5 * (K_HALF_TAPER - K_CONSTANT)
    return 100.0 * h * y / (k * length ** 2)


@register_check(Rulebook.MAX_SNAP_FIT_STRAIN)
class SnapFitStrainCheck(FeatureCheck):
    """Bending strain at each cantilever snap-fit's root as it is pushed home,
    against the material's permissible strain."""

    kinds = (FeatureKind.SNAP_FIT,)
    uses_nominal = False

    @property
    def name(self) -> str:
        return "Snap-Fit Strain Check"

    @property
    def progress_text(self) -> str:
        return "Checking snap-fit strain"

    def measure(self, snap: Feature, pf: PartFeatures) -> Optional[Measure]:
        strain = root_strain(snap)
        if strain is None:
            return None
        h, length, y = (self.param(snap, k) for k in ("thickness", "length", "overhang"))
        tapered = (self.param(snap, "thickness_tip") or h) < h - 1e-6
        return Measure(
            strain, snap.label, snap.faces,
            size=f"{mm(h)} thick{' at the root' if tapered else ''} and {mm(length)} long, and bends "
                 f"{mm(y)} to clear its catch",
        )


@register_check(Rulebook.MIN_TEXT_STROKE)
class MouldedTextCheck(FeatureCheck):
    """The thinnest stroke of moulded lettering. Besides its thresholds it warns
    of text recessed into the part: in the mould its letters are thin raised
    steel that chips off (raised text, cut into the steel, lasts)."""

    kinds = (FeatureKind.TEXT,)
    uses_nominal = False

    @property
    def name(self) -> str:
        return "Moulded Text Check"

    @property
    def progress_text(self) -> str:
        return "Checking moulded text"

    def measure(self, text: Feature, pf: PartFeatures) -> Optional[Measure]:
        stroke = self.param(text, "min_stroke")
        if not stroke:
            return None
        return Measure(stroke, text.label, text.faces, size=f"strokes as thin as {mm(stroke)}")

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
            if text.subtype != "engraved":
                continue
            depth = self.param(text, "depth") or 0.0
            results.append(CheckResult(
                rule_id=rule, severity=Severity.WARNING,
                overview=f"{text.label}: recessed into the part",
                message=(f"Recessed Text: {text.label} is cut {mm(depth)} into the part, so in the mould "
                         "its letters are thin raised ribs of steel that chip and wear. Raise the text "
                         "from the part instead (about 0.4 to 0.8 mm), or set it in a shallow recessed "
                         "panel so it is raised but stays below the surface."),
                ignore=False, value=float(depth), limit=0.0, unit="mm",
                failing_geometry=[("Face", n) for n in text.faces],
            ))
        return results
