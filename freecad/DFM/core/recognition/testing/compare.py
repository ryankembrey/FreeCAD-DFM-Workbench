# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Judging a recognition result against expectations.

An expected feature is found when a recognised feature of the same kind
contains at least ``RECALL`` of its expected faces and at least
``PRECISION`` of the recognised feature's faces are expected ones (the
recognised feature may own a few more: a hole's bottom, a rib's end).
Matching is greedy, best overlap first. Parameters are then checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..model import Feature, FeatureKind

RECALL = 0.75
PRECISION = 0.4


@dataclass
class Match:
    expect: int
    feature: int
    recall: float
    precision: float


@dataclass
class CaseResult:
    name: str
    variant: str
    passed: bool = True
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    counts: dict[str, list[int]] = field(default_factory=dict)   # kind -> [tp, fp, fn]
    seconds: float = 0.0
    face_count: int = 0
    result: Optional[dict] = None       # RecognitionResult.to_dict(), when kept
    directional: Optional[dict] = None
    error: str = ""

    def fail(self, text: str) -> None:
        self.passed = False
        self.failures.append(text)

    def count(self, kind: FeatureKind, which: int) -> None:
        self.counts.setdefault(kind.value, [0, 0, 0])[which] += 1

    def to_dict(self) -> dict:
        return {
            "name": self.name, "variant": self.variant, "passed": self.passed,
            "failures": self.failures, "notes": self.notes,
            "matches": [m.__dict__ for m in self.matches], "missing": self.missing,
            "extra": self.extra, "counts": self.counts, "seconds": round(self.seconds, 4),
            "face_count": self.face_count, "error": self.error,
            "result": self.result, "directional": self.directional,
        }


def _param_ok(actual: Any, expected: Any) -> bool:
    # (value, tolerance): two numbers, the second a small float
    if isinstance(expected, tuple) and len(expected) == 2 and isinstance(expected[1], float) \
            and isinstance(expected[0], (int, float)) and not isinstance(expected[0], bool) \
            and 0.0 <= expected[1] <= 0.5:
        value, tol = expected
        try:
            return abs(float(actual) - float(value)) <= tol
        except (TypeError, ValueError):
            return False
    if isinstance(expected, tuple):
        if not isinstance(actual, (tuple, list)) or len(actual) != len(expected):
            return False
        return all(_param_ok(a, e) for a, e in zip(actual, expected))
    if isinstance(expected, float) and isinstance(actual, (int, float)):
        return abs(actual - expected) <= 1e-6 * max(1.0, abs(expected))
    return actual == expected


def check_params(feature: Feature, params: dict) -> list[str]:
    errors = []
    for name, exp in params.items():
        if name not in feature.params:
            errors.append(f"{feature.label}: no parameter {name!r} (wanted {exp!r})")
            continue
        if not _param_ok(feature.params[name], exp):
            errors.append(f"{feature.label}: {name} = {feature.params[name]!r}, wanted {exp!r}")
    return errors


def subtype_ok(feature: Feature, subtype) -> bool:
    if subtype is None:
        return True
    if isinstance(subtype, (tuple, list, set)):
        return feature.subtype in subtype
    return feature.subtype == subtype


def match_features(expected: list[tuple[FeatureKind, set[int]]], features: Iterable[Feature]) -> list[Match]:
    feats = list(features)
    options = []
    for i, (kind, faces) in enumerate(expected):
        if not faces:
            continue
        for f in feats:
            if f.kind != kind:
                continue
            inter = len(faces & set(f.faces))
            if not inter:
                continue
            recall = inter / len(faces)
            precision = inter / max(1, len(f.faces))
            if recall >= RECALL and precision >= PRECISION:
                options.append((recall + precision, i, f.id, recall, precision))
    options.sort(key=lambda t: (-t[0], t[1], t[2]))
    used_e, used_f, out = set(), set(), []
    for _s, i, fid, r, p in options:
        if i in used_e or fid in used_f:
            continue
        used_e.add(i)
        used_f.add(fid)
        out.append(Match(i, fid, round(r, 3), round(p, 3)))
    return out


def judge(report: CaseResult, expects: list, expected_faces: list[set[int]], features: tuple[Feature, ...],
          allow: Iterable[FeatureKind]) -> None:
    """Fill *report* comparing *features* with *expects* (fixtures.Expect)."""
    by_id = {f.id: f for f in features}
    pairs = [(e.kind, faces) for e, faces in zip(expects, expected_faces)]
    for i, faces in enumerate(expected_faces):
        if not faces:
            report.fail(f"expected {expects[i].kind.value}: its selector matched no faces "
                        f"({expects[i].faces!r})")
    matches = match_features(pairs, features)
    report.matches = matches
    matched_e = {m.expect for m in matches}
    matched_f = {m.feature for m in matches}
    for m in matches:
        e = expects[m.expect]
        f = by_id[m.feature]
        report.count(e.kind, 0)
        if not subtype_ok(f, e.subtype):
            report.fail(f"{f.label}: subtype {f.subtype!r}, wanted {e.subtype!r}")
        for err in check_params(f, e.params):
            report.fail(err)
        if f.confidence < e.min_confidence:
            report.fail(f"{f.label}: confidence {f.confidence:.2f} < {e.min_confidence}")
    for i, e in enumerate(expects):
        if i not in matched_e:
            report.count(e.kind, 2)
            near = [f.label + str(list(f.faces)) for f in features if f.kind == e.kind]
            text = f"missing {e.kind.value} on faces {sorted(expected_faces[i])}" + \
                   (f" (found {', '.join(near)})" if near else "")
            report.missing.append(text)
            report.fail(text)
    allow = set(allow)
    for f in features:
        if f.id in matched_f:
            continue
        text = f"{f.label} on faces {list(f.faces)}"
        if f.kind in allow:
            report.notes.append("allowed extra " + text)
            continue
        report.count(f.kind, 1)
        report.extra.append(text)
        report.fail("unexpected " + text)
