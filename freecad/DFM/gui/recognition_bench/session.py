# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The bench's state, without Qt: what is being inspected, what was found,
and what the reviewer said about it."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from ...core.recognition import (DirectionalReport, Feature, FeatureKind, RecognitionOptions,
                                 RecognitionResult, annotate_directional, recognize)
from ...core.recognition.testing import corpus

VERDICTS = ("unreviewed", "correct", "wrong")


@dataclass
class Missed:
    kind: FeatureKind
    faces: list[int]
    subtype: str = ""


@dataclass
class BenchSession:
    shape_occ: object = None
    label: str = ""
    target_object: object = None
    result: Optional[RecognitionResult] = None
    directional: Optional[DirectionalReport] = None
    verdicts: dict[int, str] = field(default_factory=dict)
    corrections: dict[int, dict] = field(default_factory=dict)   # id -> {"kind", "subtype"}
    missed: list[Missed] = field(default_factory=list)
    hints: list = field(default_factory=list)
    seconds: float = 0.0
    source: str = ""
    picked: list = field(default_factory=list)          # faces picked for a missed feature
    numbering_errors: list = field(default_factory=list)  # faces whose FreeCAD Face<n> differs

    # -- Running -------------------------------------------------------------

    def run(self, shape_occ, label: str, options: Optional[RecognitionOptions] = None, hints=(),
            target_object=None, progress=None, source: str = "") -> RecognitionResult:
        self.shape_occ = shape_occ
        self.label = label
        self.target_object = target_object
        self.source = source
        self.hints = list(hints)
        t = time.perf_counter()
        self.result = recognize(shape_occ, options, hints=self.hints, progress=progress)
        self.seconds = time.perf_counter() - t
        self.directional = None
        self.verdicts = {f.id: "unreviewed" for f in self.result.features}
        self.corrections = {}
        self.missed = []
        self.picked = []
        self.numbering_errors = []
        return self.result

    def check_numbering(self, freecad_faces) -> list[int]:
        """Compare the result's faces with FreeCAD's ``Face<n>``.

        *freecad_faces* is ``[(area, (x, y, z)), ...]`` in FreeCAD's order:
        each face's area and centre of mass. A face whose area or centroid
        differs means FreeCAD's ``Face<n>`` is not the face the result calls
        n, and every colour, label and pick on it would be wrong. Returns the
        faces that differ (all of them when the counts differ).
        """
        r = self.result
        if r is None:
            return []
        faces = list(freecad_faces)
        if len(faces) != r.face_count:
            self.numbering_errors = list(range(1, max(len(faces), r.face_count) + 1))
            return self.numbering_errors
        diag = 0.0
        for sig in r.face_signatures.values():
            diag = max(diag, *(abs(c) for c in sig.centroid))
        tol = max(1e-6 * diag, 1e-4)
        bad = []
        for i, (area, c) in enumerate(faces, start=1):
            sig = r.face_signatures.get(i)
            if sig is None:
                continue
            d = sum((a - b) ** 2 for a, b in zip(sig.centroid, c)) ** 0.5
            if abs(sig.area - area) > 1e-4 * max(sig.area, 1e-9) + 1e-7 or d > tol + 1e-4 * max(sig.area, 1.0) ** 0.5:
                bad.append(i)
        self.numbering_errors = bad
        return bad

    def annotate(self, pull=None, print_direction=None, side_actions=(), nozzle: float = 0.4,
                 options: Optional[RecognitionOptions] = None) -> DirectionalReport:
        if self.result is None:
            raise RuntimeError("Recognise a model first.")
        self.directional = annotate_directional(self.result, self.shape_occ, pull=pull,
                                                side_actions=side_actions, print_direction=print_direction,
                                                nozzle_diameter=nozzle, options=options)
        return self.directional

    # -- Queries ---------------------------------------------------------------

    def features(self) -> list[Feature]:
        out = list(self.result.features) if self.result else []
        if self.directional:
            out += list(self.directional.features)
        return out

    def feature(self, fid: int) -> Optional[Feature]:
        for f in self.features():
            if f.id == fid:
                return f
        return None

    def annotations(self, fid: int) -> dict:
        if self.directional is None:
            return {}
        return self.directional.feature_params(fid)

    def face_report(self, face: int) -> str:
        if self.result is None:
            return ""
        lines = [f"<b>Face{face}</b>"]
        sig = self.result.face_signatures.get(face)
        if sig:
            c = ", ".join(f"{v:.3f}" for v in sig.centroid)
            lines.append(f"{sig.kind}, area {sig.area:.4g}, centroid ({c})")
        owner = self.result.owner_of(face)
        lines.append(f"Part of: {owner.label}" if owner else "Part of no feature")
        users = [f.label for f in self.result.on_face(face) if owner is None or f.id != owner.id]
        if users:
            lines.append("Supports: " + ", ".join(users))
        if self.directional and face in self.directional.face_draft:
            lines.append(f"Draft: {self.directional.face_draft[face]:.3f}°")
        return "<br>".join(lines)

    def review_counts(self) -> tuple[int, int, int]:
        """(reviewed, wrong, total)."""
        v = list(self.verdicts.values())
        return sum(x != "unreviewed" for x in v), sum(x == "wrong" for x in v), len(v)

    def reviewed(self) -> bool:
        return bool(self.result) and all(v != "unreviewed" for v in self.verdicts.values())

    # -- Approving ---------------------------------------------------------------

    def golden(self, name: str, reviewer: str = "", notes: str = "") -> dict:
        if self.result is None:
            raise RuntimeError("Nothing recognised to approve.")
        g = corpus.make_golden(
            name, self.result, self.verdicts,
            missed=[{"kind": m.kind.value, "subtype": m.subtype, "faces": m.faces} for m in self.missed],
            status="approved" if self.reviewed() else "unreviewed",
            reviewer=reviewer, notes=notes, source=self.source or self.label)
        for f in g["features"]:
            fix = self.corrections.get(f.get("id"))
            if fix:
                f["corrected_kind"] = fix.get("kind")
                f["corrected_subtype"] = fix.get("subtype")
                if f["verdict"] == "wrong" and fix.get("kind"):
                    # the reviewer said what it really is: expect that instead
                    g["features"].append({"id": 0, "kind": fix["kind"], "subtype": fix.get("subtype", ""),
                                          "faces": f["faces"], "params": {}, "verdict": "missed"})
        return g

    def save(self, folder: str, name: str, reviewer: str = "", notes: str = "") -> str:
        return corpus.save_golden(folder, name, self.shape_occ, self.golden(name, reviewer, notes))

    def apply_golden(self, golden: dict) -> int:
        """Take verdicts from a golden for the features found now (matched by
        faces). Returns how many were carried over."""
        from ...core.recognition.testing.compare import match_features

        if self.result is None:
            return 0
        reviewed = [g for g in golden.get("features", []) if g.get("verdict") in ("correct", "wrong")]
        pairs = [(FeatureKind(g["kind"]), set(g["faces"])) for g in reviewed]
        n = 0
        for m in match_features(pairs, self.result.features):
            self.verdicts[m.feature] = reviewed[m.expect]["verdict"]
            n += 1
        for g in golden.get("features", []):
            if g.get("verdict") == "missed":
                self.missed.append(Missed(FeatureKind(g["kind"]), list(g["faces"]), g.get("subtype", "")))
        return n
