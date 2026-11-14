# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The citations data (docs/_data/citations.yaml): sources, and claims about
the built-in processes' values. The file's header describes its format."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

KINDS = {
    "published": "Published",
    "derived": "Derived",
    "judgement": "Engineering judgement",
}
UNSOURCED = "Not yet sourced"


@dataclass(frozen=True)
class Source:
    id: str
    publisher: str
    title: str
    url: Optional[str]
    archive: Optional[str]
    accessed: Optional[str]
    note: str = ""


@dataclass(frozen=True)
class Claim:
    kind: str
    cite: tuple[str, ...] = ()
    note: str = ""


class Citations:
    def __init__(self, sources: dict[str, Source], claims: dict[tuple, Claim], errors: list[str]):
        self.sources = sources
        #: (process stem, material, rule name, attr) -> Claim
        self.claims = claims
        #: Problems found while reading: unknown processes, sources, kinds...
        self.errors = errors

    def claim(self, stem: str, material: str, rule_name: str, attr: str) -> Optional[Claim]:
        return self.claims.get((stem, material, rule_name, attr))


def load(path: Path, processes) -> Citations:
    """Read *path* and check every claim against *processes* (ProcessDocs)."""
    from freecad.DFM.core.rules import SHAPE_FIELD_ATTRS, Rulebook

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    errors: list[str] = []

    sources = {}
    for sid, s in (data.get("sources") or {}).items():
        s = s or {}
        if not s.get("publisher") or not s.get("title"):
            errors.append(f"source {sid}: needs a publisher and a title")
        accessed = s.get("accessed")
        sources[sid] = Source(
            id=sid, publisher=str(s.get("publisher", "")), title=str(s.get("title", "")),
            url=s.get("url"), archive=s.get("archive"),
            accessed=str(accessed) if accessed else None, note=str(s.get("note") or ""),
        )

    by_stem = {p.stem: p for p in processes}
    claims: dict[tuple, Claim] = {}

    def make(where: str, c: dict) -> Optional[Claim]:
        kind = c.get("kind")
        if kind not in KINDS:
            errors.append(f"{where}: kind must be one of {', '.join(KINDS)}, not {kind!r}")
            return None
        cite = tuple(c.get("cite") or ())
        for sid in cite:
            if sid not in sources:
                errors.append(f"{where}: cites unknown source {sid!r}")
        return Claim(kind=kind, cite=cite, note=str(c.get("note") or ""))

    for stem, materials in (data.get("values") or {}).items():
        doc = by_stem.get(stem)
        if doc is None:
            errors.append(f"values: no process file {stem}.yaml")
            continue
        for material, rules in (materials or {}).items():
            mat = doc.process.materials.get(material)
            if mat is None:
                errors.append(f"{stem} / {material}: no such material")
                continue
            for rule_name, entry in (rules or {}).items():
                where = f"{stem} / {material} / {rule_name}"
                try:
                    rule = Rulebook[rule_name]
                except KeyError:
                    errors.append(f"{where}: no such rule")
                    continue
                if rule not in mat.rule_limits:
                    errors.append(f"{where}: the material has no value for this rule")
                    continue
                attrs = SHAPE_FIELD_ATTRS[rule.shape]
                if isinstance(entry, dict) and "kind" in entry:
                    per_attr = {a: entry for a in attrs}
                else:
                    per_attr = entry or {}
                for attr, c in per_attr.items():
                    if attr not in attrs:
                        errors.append(f"{where} / {attr}: the rule has no such value")
                        continue
                    if not isinstance(c, dict):
                        errors.append(f"{where} / {attr}: expected {{kind, cite, note}}")
                        continue
                    if getattr(mat.rule_limits[rule], attr, "") in ("", None):
                        errors.append(f"{where} / {attr}: no value is written there")
                        continue
                    claim = make(f"{where} / {attr}", c)
                    if claim is not None:
                        claims[(stem, material, rule_name, attr)] = claim

    return Citations(sources, claims, errors)
