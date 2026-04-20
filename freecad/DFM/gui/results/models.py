# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from collections import defaultdict

from ...dfm.processes.process import Process
from ...dfm.models import CheckResult, Severity


class DFMReportModel:
    """Holds the data of a DFM report and handles grouping and verdicts."""

    STATUS_THEMES = {
        "FAILED": {"text": "Failed", "color": "#E24B4A"},
        "WARNING": {"text": "Warning", "color": "#D4900A"},
        "SUCCESSFUL": {"text": "Successful", "color": "#639922"},
    }

    def __init__(self, results: list[CheckResult], process: Process, material: str):
        self.results = results
        self.process = process
        self.material = material

    @property
    def active_results(self):
        return [r for r in self.results if not r.ignore]

    def get_grouped_results(self) -> dict:
        grouped = defaultdict(list)
        for result in self.results:
            grouped[result.rule_id].append(result)
        for rule_id in grouped:
            grouped[rule_id].sort(key=lambda x: (x.ignore, -x.severity.value))

        def rule_sort_key(item):
            rule_id, findings = item
            active = [f for f in findings if not f.ignore]
            if not active:
                return (2, 0, rule_id.label)
            worst = max(f.severity.value for f in active)
            return (2 - worst, -len(active), rule_id.label)

        return dict(sorted(grouped.items(), key=rule_sort_key))

    def get_verdict(self) -> tuple[str, str]:
        errors = sum(1 for r in self.active_results if r.severity == Severity.ERROR)
        warnings = sum(1 for r in self.active_results if r.severity == Severity.WARNING)

        parts = []
        if errors:
            parts.append(f"{errors} error{'s' if errors != 1 else ''}")
        if warnings:
            parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")

        details = f" ({', '.join(parts)})" if parts else ""
        if errors:
            theme = self.STATUS_THEMES["FAILED"]
        elif warnings:
            theme = self.STATUS_THEMES["WARNING"]
        else:
            theme = self.STATUS_THEMES["SUCCESSFUL"]

        return f"{theme['text']}{details}", theme["color"]

    def toggle_ignore_state(self, finding: CheckResult):
        finding.ignore = not finding.ignore
