# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from __future__ import annotations

import re
import json
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..dfm.models import CheckResult, GeometryRef, Severity
from ..dfm.rules import Rulebook


@dataclass
class AnalysisRun:
    """
    Represents one saved analysis run.  Fully serialisable.
    """

    run: int
    timestamp: str
    document: str
    shape: str
    process: str
    material: str
    verdict: str
    findings: list[CheckResult] = field(default_factory=list)

    @property
    def label(self) -> str:
        """Human-readable label for UI display"""
        ts = self.timestamp[:16].replace("T", " ")
        return f"Run {self.run} — {ts}"


class HistoryManager:
    """
    Manages analysis run history for the DFM workbench.
    """

    def __init__(self, user_data_dir: Path):
        self._history_dir = user_data_dir / "dfm" / "history"
        self._history_dir.mkdir(parents=True, exist_ok=True)

    def save_run(
        self,
        results: list[CheckResult],
        doc_name: str,
        shape_name: str,
        process_name: str,
        material: str,
        verdict: str,
    ) -> AnalysisRun:
        """
        Appends a new run to the history file for this document/shape pair.
        Returns the saved AnalysisRun.
        """
        path = self._history_path(doc_name, shape_name)
        existing_runs = self._load_raw(path)

        run_index = (existing_runs[-1]["run"] + 1) if existing_runs else 1
        timestamp = datetime.now().isoformat(timespec="seconds")

        run_data = {
            "run": run_index,
            "timestamp": timestamp,
            "document": doc_name,
            "shape": shape_name,
            "process": process_name,
            "material": material,
            "verdict": verdict,
            "findings": [self._result_to_dict(r) for r in results],
        }

        existing_runs.append(run_data)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing_runs, f, indent=2)

        return AnalysisRun(
            run=run_index,
            timestamp=timestamp,
            document=doc_name,
            shape=shape_name,
            process=process_name,
            material=material,
            verdict=verdict,
            findings=results,
        )

    def load_runs(self, doc_name: str, shape_name: str) -> list[AnalysisRun]:
        path = self._history_path(doc_name, shape_name)
        raw_runs = self._load_raw(path)
        return [self._run_from_dict(r) for r in raw_runs]

    def load_run(self, doc_name: str, shape_name: str, run_index: int) -> Optional[AnalysisRun]:
        for run in self.load_runs(doc_name, shape_name):
            if run.run == run_index:
                return run
        return None

    def latest_run(self, doc_name: str, shape_name: str) -> Optional[AnalysisRun]:
        runs = self.load_runs(doc_name, shape_name)
        return runs[-1] if runs else None

    def run_count(self, doc_name: str, shape_name: str) -> int:
        return len(self.load_runs(doc_name, shape_name))

    def delete_run(self, doc_name: str, shape_name: str, run_index: int) -> bool:
        path = self._history_path(doc_name, shape_name)
        raw_runs = self._load_raw(path)
        original_count = len(raw_runs)
        raw_runs = [r for r in raw_runs if r["run"] != run_index]

        if len(raw_runs) == original_count:
            return False

        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw_runs, f, indent=2)

        return True

    def delete_all_runs(self, doc_name: str, shape_name: str) -> None:
        path = self._history_path(doc_name, shape_name)
        if path.exists():
            path.unlink()

    def list_tracked_shapes(self) -> list[tuple[str, str]]:
        results = []
        for p in sorted(self._history_dir.glob("*.json")):
            parts = p.stem.split("__", 1)
            if len(parts) == 2:
                results.append((self._decode_name(parts[0]), self._decode_name(parts[1])))
        return results

    def _history_path(self, doc_name: str, shape_name: str) -> Path:
        safe_doc = self._encode_name(doc_name)
        safe_shape = self._encode_name(shape_name)
        return self._history_dir / f"{safe_doc}__{safe_shape}.json"

    @staticmethod
    def _encode_name(name: str) -> str:
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)

    @staticmethod
    def _decode_name(name: str) -> str:
        return name

    @staticmethod
    def _load_raw(path: Path) -> list[dict]:
        """Loads raw JSON list from disk, returning [] if the file doesn't exist."""
        if not path.exists():
            return []
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    @staticmethod
    def _result_to_dict(result: CheckResult) -> dict:
        return {
            "rule": result.rule_id.name,
            "severity": result.severity.name,
            "overview": result.overview,
            "message": result.message,
            "value": result.value,
            "limit": result.limit,
            "target": getattr(result, "target", None),
            "unit": result.unit,
            "comparison": result.comparison,
            "ignore": result.ignore,
            "refs": [
                {"type": ref.type, "index": ref.index, "label": ref.label} for ref in result.refs
            ],
        }

    @staticmethod
    def _result_from_dict(d: dict) -> CheckResult:
        return CheckResult(
            rule_id=Rulebook[d["rule"]],
            severity=Severity[d["severity"]],
            overview=d.get("overview", ""),
            message=d.get("message", ""),
            value=d.get("value"),
            limit=d.get("limit"),
            comparison=d.get("comparison", ""),
            unit=d.get("unit", ""),
            ignore=d.get("ignore", False),
            refs=[
                GeometryRef(
                    type=ref["type"],
                    index=ref["index"],
                    label=ref["label"],
                )
                for ref in d.get("refs", [])
            ],
        )

    @classmethod
    def _run_from_dict(cls, d: dict) -> AnalysisRun:
        return AnalysisRun(
            run=d["run"],
            timestamp=d["timestamp"],
            document=d["document"],
            shape=d["shape"],
            process=d["process"],
            material=d["material"],
            verdict=d["verdict"],
            findings=[cls._result_from_dict(f) for f in d.get("findings", [])],
        )


@dataclass
class RuleDiff:
    rule_label: str
    previous_count: int
    current_count: int
    previous_errors: int = 0
    previous_warnings: int = 0
    previous_success: int = 0
    current_errors: int = 0
    current_warnings: int = 0
    current_success: int = 0

    @property
    def status(self) -> str:
        if self.previous_count == 0 and self.current_count == 0:
            return "unchanged"
        if self.previous_count == 0 and self.current_count > 0:
            return "new"
        if self.current_count == 0 and self.previous_count > 0:
            return "resolved"
        if self.current_count < self.previous_count:
            return "improved"
        if self.current_count > self.previous_count:
            return "regressed"
        return "unchanged"


def diff_runs(run_a: "AnalysisRun", run_b: "AnalysisRun") -> "list[RuleDiff]":
    """Compares two runs by rule. run_a is older, run_b is newer."""
    from collections import Counter

    def _counts(findings):
        errors = Counter()
        warnings = Counter()
        successes = Counter()
        for f in findings:
            if f.ignore:
                continue
            label = f.rule_id.label
            if f.severity.name == "ERROR":
                errors[label] += 1
            elif f.severity.name == "WARNING":
                warnings[label] += 1
            elif f.severity.name == "SUCCESS":
                successes[label] += 1
        return errors, warnings, successes

    a_errors, a_warnings, a_successes = _counts(run_a.findings)
    b_errors, b_warnings, b_successes = _counts(run_b.findings)

    all_rules = (
        set(a_errors)
        | set(a_warnings)
        | set(a_successes)
        | set(b_errors)
        | set(b_warnings)
        | set(b_successes)
    )

    results = []
    for rule in sorted(all_rules):
        prev_e = a_errors[rule]
        prev_w = a_warnings[rule]
        prev_s = a_successes[rule]
        curr_e = b_errors[rule]
        curr_w = b_warnings[rule]
        curr_s = b_successes[rule]
        results.append(
            RuleDiff(
                rule_label=rule,
                previous_count=prev_e + prev_w,
                current_count=curr_e + curr_w,
                previous_errors=prev_e,
                previous_warnings=prev_w,
                previous_success=prev_s,
                current_errors=curr_e,
                current_warnings=curr_w,
                current_success=curr_s,
            )
        )
    return results
