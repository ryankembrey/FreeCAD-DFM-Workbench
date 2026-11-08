# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The base of the rules that measure recognised features (bosses, ribs...).

A feature rule says which features it looks at (``kinds``) and how to
measure one (``measure``); the base does the rest the same way for every
rule:

* reads the ``PartFeatures`` that FEATURE_ANALYZER returned;
* skips features recognised less confidently than the preference allows,
  and partial ones (cut through by something else);
* grades each value against the target and limit (past the limit is an
  error, past the target a warning);
* writes the finding: the feature's faces, an overview such as
  "Boss 3 (screw): 75% > 60%", and the message from the process's
  feedback template, with ``{nominal}``, ``{feature}`` and ``{size}``
  filled in besides ``{measured}``, ``{target}`` and ``{limit}``.

When a rule needs the nominal wall and it couldn't be measured, the rule is
skipped and one INFO finding for the whole run says so.

``MeasuredCheck`` is the grading and writing on its own, for rules measured
by other analyzers (wall transitions, thin steel).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..models import CheckResult, Severity
from ..processes.process import RuleFeedback, RuleLimit
from ..recognition.model import Feature, FeatureKind
from ..rules import Rulebook
from ..utils.part_features import PartFeatures
from .base_check import BaseCheck

#: run_notes key: the run already said the nominal wall wasn't measured.
NOMINAL_MISSING_NOTE = "nominal_wall_missing"


@dataclass
class Measure:
    """One measured feature (or pair of features) for a rule."""

    #: The rule's value, in the rule's unit (a %, a multiple, mm).
    value: float
    #: What the finding is called: the feature's label, or "Rib 4 ↔ Rib 7".
    label: str
    #: Faces to highlight (1-based face numbers).
    faces: tuple[int, ...]
    #: The {size} placeholder: the raw dimension, such as "1.75 mm wall".
    size: str = ""
    #: Multiplies the target and limit for this feature (through holes: 2).
    scale: float = 1.0
    #: Said after the overview in brackets, such as "through hole, limits doubled".
    note: str = ""
    #: More placeholders for the message.
    extra: dict[str, Any] = field(default_factory=dict)
    #: For yes/no rules: the overview after the label.
    overview: str = ""
    #: The worst severity this finding may have (a shallow gap is only a warning).
    cap: Optional[Severity] = None
    #: Edges to highlight as well (1-based edge numbers).
    edges: tuple[int, ...] = ()


def trim_number(value: float, decimals: int) -> str:
    """*value* with at most *decimals* decimals and no trailing zeros: 60, 62.5."""
    text = f"{value:.{decimals}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def mm(value: float) -> str:
    """A length for messages: "1.75 mm"."""
    return f"{value:.2f} mm"


class MeasuredCheck(BaseCheck):
    """A rule whose analyzer's data become ``Measure``s: grades them against
    the target and limit and writes the findings. Subclasses implement
    ``run_check`` by calling ``grade_measures``."""

    def value_placeholders(self, rule: Rulebook) -> dict[str, str]:
        # A binary rule's finding is a fact, not a value.
        return {} if rule.is_binary else super().value_placeholders(rule)

    def context_placeholders(self, rule: Rulebook) -> dict[str, str]:
        return {
            "feature": 'What it is about, such as "Boss 3 (screw)" or "Face12"',
            "size": 'Its size or what was found, such as "Ø3.00 mm and 9.00 mm deep"',
        }

    @staticmethod
    def grade(
        value: float, target: Optional[float], limit: Optional[float], comparison: str
    ) -> Optional[Severity]:
        """ERROR past the limit, WARNING past the target, else None.

        "max" rules are past a threshold above it, "min" rules below it.
        Values are compared to 4 decimals, so 1.75 / 2.5 is 70%, not a hair over.
        """
        value = round(value, 4)

        def past(threshold: Optional[float]) -> bool:
            if threshold is None:
                return False
            return value > threshold if comparison == "max" else value < threshold

        if past(limit):
            return Severity.ERROR
        if past(target):
            return Severity.WARNING
        return None

    def format_number(self, value: float, unit: str = "") -> str:
        if unit == "%":
            return f"{trim_number(value, 1)}%"
        if unit == "×":
            return f"{trim_number(value, 2)}×"
        if unit == "°":
            return f"{value:.2f}°"
        if unit == "mm²":
            return f"{value:,.0f} mm²"
        if unit:
            return f"{value:.2f} {unit}"
        return trim_number(value, 2)

    def thresholds(self, rule: Rulebook, rule_config: RuleLimit) -> Optional[tuple]:
        """(target, limit) as numbers (None for one not set), or None when the
        rule has neither and so nothing to check. Yes/no rules: (None, None)."""
        if rule.is_binary:
            return None, None
        target = self.safe_float(rule_config.target)
        limit = self.safe_float(rule_config.limit)
        if target is None and limit is None:
            return None
        return target, limit

    def grade_measures(
        self,
        measures: Iterable[Measure],
        rule: Rulebook,
        rule_config: RuleLimit,
        feedback: Optional[RuleFeedback],
        **placeholders: Any,
    ) -> list[CheckResult]:
        """Findings for the measures past the target or limit (or every measure,
        for yes/no rules), with *placeholders* for the messages."""
        thresholds = self.thresholds(rule, rule_config)
        if thresholds is None:
            return []
        target, limit = thresholds
        fb = feedback or RuleFeedback()
        results = []
        for m in measures:
            if rule.is_binary:
                severity = self.severity_from_rule_config(rule_config)
                scaled_target = scaled_limit = None
            else:
                scaled_target = None if target is None else target * m.scale
                scaled_limit = None if limit is None else limit * m.scale
                severity = self.grade(m.value, scaled_target, scaled_limit, rule.comparison)
            if severity is None:
                continue
            if m.cap is not None and severity.value > m.cap.value:
                severity = m.cap
            template = fb.error_msg if severity == Severity.ERROR else fb.warning_msg
            results.append(self.make_result(rule, m, severity, template, scaled_target, scaled_limit,
                                            **placeholders))
        return results

    def make_result(
        self,
        rule: Rulebook,
        m: Measure,
        severity: Severity,
        template: str,
        target: Optional[float],
        limit: Optional[float],
        **placeholders: Any,
    ) -> CheckResult:
        unit = rule.unit
        threshold = limit if severity == Severity.ERROR else target
        if rule.is_binary or threshold is None:
            overview = f"{m.label}: {m.overview}" if m.overview else m.label
            comparison = ""
        else:
            comparison = ">" if rule.comparison == "max" else "<"
            overview = (f"{m.label}: {self.format_number(m.value, unit)} {comparison} "
                        f"{self.format_number(threshold, unit)}")
        if m.note:
            overview += f" ({m.note})"
        message = self.format_feedback(
            template or "",
            m.value,
            target if target is not None else 0.0,
            limit if limit is not None else 0.0,
            unit,
            **{"feature": m.label, "size": m.size, **placeholders, **m.extra},
        )
        return CheckResult(
            rule_id=rule,
            severity=severity,
            overview=overview,
            message=message.strip(),
            ignore=False,
            value=float(m.value),
            limit=float(threshold) if threshold is not None else 0.0,
            comparison=comparison,
            unit=unit,
            failing_geometry=[("Face", n) for n in m.faces] + [("Edge", n) for n in m.edges],
        )


class FeatureCheck(MeasuredCheck):
    """A rule measured on recognised features. Subclasses set ``kinds`` and
    implement ``measure`` (or ``measurements`` for pairs of features)."""

    #: The kinds of feature the rule looks at.
    kinds: tuple[FeatureKind, ...] = ()
    #: Subtypes it leaves out (such as crush ribs).
    skip_subtypes: frozenset[str] = frozenset()
    #: The rule is a ratio to the nominal wall, so it can't run without one.
    uses_nominal: bool = True

    @property
    def required_analyzer_id(self) -> str:
        return "FEATURE_ANALYZER"

    # -- What to measure --------------------------------------------------------

    def context_placeholders(self, rule: Rulebook) -> dict[str, str]:
        fields = super().context_placeholders(rule)
        if self.uses_nominal:
            fields["nominal"] = 'The nominal wall and where it came from, such as "2.40 mm (measured)"'
        return fields

    def select(self, pf: PartFeatures) -> list[Feature]:
        """The features this rule measures: its kinds, recognised confidently
        enough, whole (not cut through), and not a skipped subtype."""
        return [
            f for f in pf.features(*self.kinds)
            if f.subtype not in self.skip_subtypes and not f.params.get("partial")
        ]

    def measure(self, feature: Feature, pf: PartFeatures) -> Optional[Measure]:
        """The rule's value for one feature, or None to skip it."""
        return None

    def measurements(self, pf: PartFeatures, **kwargs: Any) -> Iterable[Measure]:
        """Every value to grade. By default, measure() of each selected feature."""
        for feature in self.select(pf):
            found = self.measure(feature, pf)
            if found is not None:
                yield found

    def run_check(
        self,
        analysis_data_map: Any,
        rule_config: RuleLimit,
        rule: Rulebook,
        feedback: Optional[RuleFeedback] = None,
        **kwargs: Any,
    ) -> list[CheckResult]:
        pf = analysis_data_map
        if not isinstance(pf, PartFeatures) or self.thresholds(rule, rule_config) is None:
            return []
        if self.uses_nominal and pf.nominal_wall is None:
            return self.nominal_missing(rule, **kwargs)
        return self.grade_measures(self.measurements(pf, **kwargs), rule, rule_config, feedback,
                                   nominal=pf.nominal_text)

    # -- Helpers for subclasses ---------------------------------------------------

    @staticmethod
    def percent_of_nominal(length: float, pf: PartFeatures) -> float:
        return 100.0 * length / pf.nominal_wall

    @staticmethod
    def times_nominal(length: float, pf: PartFeatures) -> float:
        return length / pf.nominal_wall

    @staticmethod
    def param(feature: Feature, name: str) -> Optional[float]:
        """A numeric parameter, or None when it is missing or not a number."""
        value = feature.params.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    # -- No nominal wall ---------------------------------------------------------------

    def nominal_missing(self, rule: Rulebook, **kwargs: Any) -> list[CheckResult]:
        """One INFO finding per run: the nominal wall couldn't be measured, so
        the rules that need it were skipped. Later rules add nothing."""
        notes = kwargs.get("run_notes")
        if notes is not None:
            if NOMINAL_MISSING_NOTE in notes:
                return []
            notes.add(NOMINAL_MISSING_NOTE)
        skipped = rules_needing_nominal(kwargs.get("_active_rules")) or [rule]
        names = ", ".join(r.label for r in skipped)
        count = f"{len(skipped)} rule{'s' if len(skipped) != 1 else ''}"
        message = (
            "Nominal Wall Not Measured: no clear nominal wall was found on this part, so "
            f"{count} that compare features with it {'were' if len(skipped) != 1 else 'was'} "
            f"skipped ({names}). In the analysis setup, set Nominal wall to Manual, enter "
            "the part's main wall thickness and run the analysis again."
        )
        return [CheckResult(
            rule_id=rule,
            severity=Severity.INFO,
            overview=f"Nominal wall not measured: {count} skipped",
            message=message,
            ignore=False,
            value=0.0,
            limit=0.0,
            unit="",
        )]


def rules_needing_nominal(active_rules: Optional[Iterable[Rulebook]]) -> list[Rulebook]:
    """The rules among *active_rules* whose check needs the nominal wall."""
    if not active_rules:
        return []
    from ..registries.checks_registry import get_check_class

    out = []
    for rule in active_rules:
        cls = get_check_class(rule)
        if cls is not None and issubclass(cls, FeatureCheck) and cls.uses_nominal:
            out.append(rule)
    return out
