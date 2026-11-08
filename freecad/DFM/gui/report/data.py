# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""What a report is made from, and which findings get viewport images.

``ReportData`` is a snapshot of the results panel: the findings grouped by
rule plus the lookups the report needs (criticality, target and limit,
feedback). It knows nothing about Qt widgets or the 3D view, so the layout
can be previewed and tested without FreeCAD.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from ...core.models import CheckResult, Severity
from ...core.processes.process import RuleFeedback, RuleLimit
from ...core.rules import SHAPE_FIELD_ATTRS, Criticality, Rulebook, RuleShape
from .settings import ReportSettings
from .template import file_name_from_pattern


@dataclass
class ReportData:
    document: str
    model: str
    process: str
    process_kind: str
    material: str
    verdict: str
    verdict_color: str
    grouped: dict[Rulebook, list[CheckResult]]
    active_rules: list[Rulebook]
    criticality: Callable[[Rulebook], Criticality]
    rule_limit: Callable[[Rulebook], Optional[RuleLimit]] = lambda rule: None
    values_title: Callable[[Rulebook], str] = lambda rule: ""
    feedback: Callable[[Rulebook], Optional[RuleFeedback]] = lambda rule: None
    derived_hint: Callable[[Rulebook], str] = lambda rule: ""
    created: datetime = field(default_factory=datetime.now)
    run: Optional[int] = None

    # ------------------------------------------------------------------

    @property
    def findings(self) -> list[CheckResult]:
        return [f for fs in self.grouped.values() for f in fs]

    @property
    def passed_rules(self) -> list[Rulebook]:
        return [r for r in self.active_rules if not self.grouped.get(r)]

    def counts(self) -> dict[str, int]:
        active = [f for f in self.findings if not f.ignore]
        return {
            "errors": sum(1 for f in active if f.severity == Severity.ERROR),
            "warnings": sum(1 for f in active if f.severity == Severity.WARNING),
            "passed": len(self.passed_rules),
            "ignored": sum(1 for f in self.findings if f.ignore),
        }

    def placeholders(self, settings: ReportSettings) -> dict[str, str]:
        """Values for {placeholders} in titles, header, footer and file names."""
        values = {
            "document": self.document,
            "model": self.model,
            "process": self.process,
            "material": self.material,
            "date": self.created.strftime("%Y-%m-%d"),
            "company": settings.company_name,
            "project": settings.project,
            "revision": settings.revision,
            "prepared_by": settings.prepared_by,
        }
        values["title"] = _fill(settings.title, values)
        return values

    def title(self, settings: ReportSettings) -> str:
        return self.placeholders(settings)["title"] or "DFM report"

    def file_name(self, settings: ReportSettings) -> str:
        return file_name_from_pattern(settings.file_name, self.placeholders(settings))


def _fill(text: str, values: dict[str, str]) -> str:
    import re

    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), "") or ""), text).strip()


# ---------------------------------------------------------------------------
# What goes in
# ---------------------------------------------------------------------------


def included(finding: CheckResult, settings: ReportSettings) -> bool:
    """Whether the Content options put *finding* in the report."""
    if finding.ignore and not settings.include_ignored:
        return False
    if finding.severity == Severity.ERROR:
        return settings.include_errors
    if finding.severity == Severity.WARNING:
        return settings.include_warnings
    return True


@dataclass
class RuleSection:
    rule: Rulebook
    findings: list[CheckResult]  # the findings shown, worst first
    criticality: Criticality

    @property
    def passed(self) -> bool:
        return not self.findings

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.ERROR and not f.ignore)

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.WARNING and not f.ignore)

    @property
    def ignored(self) -> int:
        return sum(1 for f in self.findings if f.ignore)


def _finding_order(f: CheckResult):
    return (f.ignore, -f.severity.value)


def rule_sections(data: ReportData, settings: ReportSettings) -> list[RuleSection]:
    """Rules with findings in the report, ordered by the Group by option."""
    sections = []
    for rule, findings in data.grouped.items():
        shown = sorted((f for f in findings if included(f, settings)), key=_finding_order)
        if shown:
            sections.append(RuleSection(rule, shown, data.criticality(rule)))

    def worst(s: RuleSection) -> int:
        active = [f.severity.value for f in s.findings if not f.ignore]
        return -max(active) if active else 0

    if settings.group_by == "criticality":
        sections.sort(key=lambda s: (s.criticality.value, worst(s), s.rule.label))
    elif settings.group_by == "severity":
        sections.sort(key=lambda s: (worst(s), s.criticality.value, s.rule.label))
    else:
        sections.sort(key=lambda s: s.rule.label)
    return sections


def passed_sections(data: ReportData, settings: ReportSettings) -> list[RuleSection]:
    if not settings.include_passed:
        return []
    rules = sorted(data.passed_rules, key=lambda r: (data.criticality(r).value, r.label))
    return [RuleSection(r, [], data.criticality(r)) for r in rules]


# ---------------------------------------------------------------------------
# Viewport images
# ---------------------------------------------------------------------------


def image_eligible(finding: CheckResult, settings: ReportSettings) -> bool:
    """Whether *finding* can get an image under the "Include" option."""
    if not included(finding, settings):
        return False
    if settings.image_findings == "errors":
        return finding.severity == Severity.ERROR and not finding.ignore
    if settings.image_findings == "errors_warnings":
        return finding.severity in (Severity.ERROR, Severity.WARNING) and not finding.ignore
    return True


def default_image_picks(data: ReportData, settings: ReportSettings) -> set[int]:
    """Findings that get an image when they aren't chosen individually.

    Returns ``id()`` of each finding: the worst findings of each rule first,
    at most ``images_per_rule`` per rule.
    """
    picks: set[int] = set()
    for section in rule_sections(data, settings):
        eligible = [f for f in section.findings if image_eligible(f, settings)]
        picks.update(id(f) for f in eligible[: max(settings.images_per_rule, 0)])
    return picks


@dataclass
class ImageJob:
    """One viewport image to capture."""

    key: str
    findings: list[CheckResult]  # highlighted; empty for the plain model
    caption: str = ""
    aspect: float = 4 / 3  # width / height


OVERVIEW_ASPECT = 16 / 9
PAIR_ASPECT = 4 / 3  # each of the two cover images when shown side by side


def image_jobs(
    data: ReportData, settings: ReportSettings, picks: Optional[set[int]] = None
) -> list[ImageJob]:
    """Every image the report needs, in the order they appear."""
    jobs: list[ImageJob] = []
    if settings.overview_image:
        shown = [f for s in rule_sections(data, settings) for f in s.findings if not f.ignore]
        if settings.design_image:
            # Side by side: the part as its designer colored it, then the findings.
            jobs.append(ImageJob("design", [], "As designed", PAIR_ASPECT))
            jobs.append(ImageJob("overview", shown, "Findings highlighted", PAIR_ASPECT))
        else:
            jobs.append(ImageJob("overview", shown, "All findings highlighted", OVERVIEW_ASPECT))

    if settings.image_mode == "per_rule":
        for section in rule_sections(data, settings):
            eligible = [f for f in section.findings if image_eligible(f, settings)]
            if eligible:
                n = len(eligible)
                caption = f"{n} finding{'s' if n != 1 else ''} highlighted"
                jobs.append(ImageJob(rule_image_key(section.rule), eligible, caption))
    elif settings.image_mode == "per_finding":
        picks = default_image_picks(data, settings) if picks is None else picks
        for section in rule_sections(data, settings):
            for f in section.findings:
                if id(f) in picks and image_eligible(f, settings):
                    jobs.append(ImageJob(finding_image_key(f), [f], finding_caption(f)))
    return jobs


def rule_image_key(rule: Rulebook) -> str:
    return f"rule:{rule.name}"


def finding_image_key(finding: CheckResult) -> str:
    return f"finding:{id(finding)}"


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def geometry_label(finding: CheckResult) -> str:
    labels = [ref.label for ref in finding.refs]
    if not labels:
        return "—"
    if len(labels) > 3:
        return ", ".join(labels[:3]) + f" +{len(labels) - 3}"
    return ", ".join(labels)


def finding_caption(finding: CheckResult) -> str:
    result = finding.overview or ""
    return f"{geometry_label(finding)} · {result}" if result else geometry_label(finding)


def severity_label(finding: CheckResult) -> str:
    if finding.ignore:
        return "Ignored"
    return {Severity.ERROR: "Error", Severity.WARNING: "Warning"}.get(finding.severity, "Info")


def format_value(rule: Rulebook, value) -> str:
    if value in (None, ""):
        return ""
    try:
        text = f"{float(value):g}"
    except (TypeError, ValueError):
        text = str(value)
    unit = rule.unit
    if unit and unit not in ("°", "%", "×"):
        text += f" {unit}"
    elif unit:
        text += unit
    if rule.unit_suffix:
        text += f" {rule.unit_suffix}"
    return text


def limit_phrases(rule: Rulebook, limit: Optional[RuleLimit]) -> list[str]:
    """Target and limit as short phrases, such as ["Aim for 3°", "At least 1°"]."""
    if limit is None:
        return []
    if rule.shape == RuleShape.BINARY:
        severity = (limit.binary_severity or "ERROR").upper()
        return [f"Reported as {'a warning' if severity == 'WARNING' else 'an error'}"]
    if rule.shape == RuleShape.MIN_AND_MAX:
        lo = format_value(rule, limit.min_value)
        hi = format_value(rule, limit.max_value)
        return [f"Between {lo} and {hi}"] if lo and hi else []
    phrases = []
    for attr in SHAPE_FIELD_ATTRS.get(rule.shape, ()):
        value = format_value(rule, getattr(limit, attr, ""))
        if value:
            phrases.append(f"{rule.field_label_for(attr)} {value}")
    return phrases


def feedback_messages(section: RuleSection) -> list[tuple[Severity, str]]:
    """The distinct feedback messages of a rule's findings, worst first."""
    seen: dict[Severity, str] = {}
    for f in section.findings:
        if f.ignore or not f.message or f.severity in seen:
            continue
        seen[f.severity] = f.message.strip()
    return sorted(seen.items(), key=lambda item: -item[0].value)
