# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The corpus: parts on disk with approved ("golden") recognition results.

Each case is ``<name>.brep`` (OCP's own format, exact) and
``<name>.golden.json``; ``<name>.step`` is written alongside for opening
in FreeCAD or any CAD system. A golden lists the features a person has
reviewed in the bench, each marked:

* ``correct``: must be found again (losing it is a regression);
* ``wrong``: a known false positive (finding it again is reported, not
  failed; losing it is an improvement);
* ``missed``: a feature the engine didn't find but should (a person
  picked its faces). Reported as a known miss until found.

Faces are stored by number and by signature (kind, area, centroid), so a
golden still applies if OpenCASCADE numbers the faces differently.
"""

from __future__ import annotations

import datetime as _dt
import glob
import json
import math
import os
from typing import Iterable, Optional

from ..api import recognize
from ..model import FaceSignature, FeatureKind, RecognitionResult
from ..options import RecognitionOptions
from . import shapes as B
from .compare import CaseResult, check_params, match_features

GOLDEN_SUFFIX = ".golden.json"


# -- Paths ------------------------------------------------------------------------------


def default_corpus_dir() -> str:
    """tests/recognition/corpus in the repository (next to the package)."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", "..", "..", "..", ".."))
    return os.path.join(root, "tests", "recognition", "corpus")


def list_goldens(folder: str) -> list[str]:
    return sorted(glob.glob(os.path.join(folder, "*" + GOLDEN_SUFFIX)))


def shape_path(golden_path: str) -> str:
    return golden_path[: -len(GOLDEN_SUFFIX)] + ".brep"


# -- Writing ----------------------------------------------------------------------------


def save_shape(folder: str, name: str, shape, step: bool = True) -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name + ".brep")
    B.write_brep(shape, path)
    if step:
        try:
            B.write_step(shape, os.path.join(folder, name + ".step"))
        except Exception:
            pass
    return path


def make_golden(name: str, result: RecognitionResult, verdicts: Optional[dict[int, str]] = None,
                missed: Iterable[dict] = (), status: str = "unreviewed", reviewer: str = "",
                notes: str = "", source: str = "") -> dict:
    """A golden dict from a result. *verdicts*: feature id -> correct / wrong
    (unreviewed if absent). *missed*: dicts with kind, subtype, faces."""
    verdicts = verdicts or {}
    feats = []
    for f in result.features:
        d = f.to_dict()
        d["verdict"] = verdicts.get(f.id, "unreviewed")
        feats.append(d)
    for m in missed:
        feats.append({"id": 0, "kind": m["kind"], "subtype": m.get("subtype", ""), "faces": list(m["faces"]),
                      "params": m.get("params", {}), "verdict": "missed"})
    return {
        "name": name,
        "status": status,
        "engine_version": result.engine_version,
        "fingerprint": result.fingerprint,
        "reviewed_by": reviewer,
        "reviewed_on": _dt.date.today().isoformat(),
        "source": source,
        "notes": notes,
        "options": result.options,
        "face_signatures": [s.to_dict() for s in result.face_signatures.values()],
        "features": feats,
    }


def save_golden(folder: str, name: str, shape, golden: dict) -> str:
    save_shape(folder, name, shape)
    path = os.path.join(folder, name + GOLDEN_SUFFIX)
    with open(path, "w") as fh:
        json.dump(golden, fh, indent=1)
    return path


def load_golden(path: str) -> dict:
    with open(path) as fh:
        return json.load(fh)


def export_fixtures(folder: str, cases=None, step: bool = True) -> int:
    """Write every fixture's part (BREP + STEP) and its ground truth as
    the faces the selectors pick, for use outside Python."""
    from . import fixtures as FX

    os.makedirs(folder, exist_ok=True)
    n = 0
    for c in (cases if cases is not None else FX.CASES):
        shape = c.build()
        save_shape(folder, c.name, shape, step)
        truth = {"name": c.name, "tags": list(c.tags), "notes": c.notes,
                 "allow": [k.value for k in c.allow],
                 "expect": [{"kind": e.kind.value, "subtype": e.subtype if not isinstance(e.subtype, tuple) else list(e.subtype),
                             "faces": sorted(e.faces(shape)), "params": {k: (list(v) if isinstance(v, tuple) else v)
                                                                         for k, v in e.params.items()}}
                            for e in c.expect]}
        with open(os.path.join(folder, c.name + ".expected.json"), "w") as fh:
            json.dump(truth, fh, indent=1)
        n += 1
    return n


# -- Checking -----------------------------------------------------------------------------


def _signature_map(old: list[dict], new: dict[int, FaceSignature], scale: float) -> dict[int, int]:
    """Old face number -> new face number by kind, area and centroid."""
    out = {}
    tol = max(1e-6, 1e-5 * scale)
    for s in old:
        best, best_d = None, None
        for n in new.values():
            if n.kind != s["kind"]:
                continue
            if abs(n.area - s["area"]) > max(1e-6, 1e-4 * max(1.0, s["area"])):
                continue
            d = math.dist(n.centroid, s["centroid"])
            if d <= tol * 100 and (best_d is None or d < best_d):
                best, best_d = n.index, d
        if best is not None:
            out[int(s["index"])] = best
    return out


def check_golden(path: str, options: Optional[RecognitionOptions] = None, keep_result: bool = False) -> CaseResult:
    name = os.path.basename(path)[: -len(GOLDEN_SUFFIX)]
    report = CaseResult(name, "corpus")
    try:
        golden = load_golden(path)
        shape = B.read_brep(shape_path(path))
        opts = options or RecognitionOptions.from_dict(golden.get("options", {}))
        result = recognize(shape, opts)
        report.face_count = result.face_count
        if keep_result:
            report.result = result.to_dict()
        if golden.get("fingerprint") == result.fingerprint:
            fmap = {i: i for i in range(1, result.face_count + 1)}
        else:
            scale = max([abs(c) for s in golden["face_signatures"] for c in s["centroid"]] or [1.0])
            fmap = _signature_map(golden["face_signatures"], result.face_signatures, scale)
            report.notes.append("faces renumbered since approval: matched by signature")
        reviewed = [g for g in golden["features"] if g.get("verdict") in ("correct", "missed", "wrong")]
        if golden.get("status") != "approved":
            report.notes.append(f"golden status is {golden.get('status')!r}: not failing on it")
        want = [g for g in reviewed if g["verdict"] in ("correct", "missed")]
        wrong = [g for g in reviewed if g["verdict"] == "wrong"]
        pairs = [(FeatureKind(g["kind"]), {fmap[f] for f in g["faces"] if f in fmap}) for g in want]
        matches = match_features(pairs, result.features)
        got = {m.expect: m for m in matches}
        by_id = {f.id: f for f in result.features}
        for i, g in enumerate(want):
            label = f"{g['kind']} on {g['faces']}"
            if i in got:
                f = by_id[got[i].feature]
                report.count(FeatureKind(g["kind"]), 0)
                if g["verdict"] == "missed":
                    report.notes.append(f"previously missed {label} is now found")
                    continue
                if g.get("subtype") and f.subtype != g["subtype"]:
                    report.fail(f"{f.label}: subtype {f.subtype!r}, approved {g['subtype']!r}")
                exp = {k: ((float(v), max(1e-3, 1e-4 * abs(float(v)))) if isinstance(v, (int, float)) and not isinstance(v, bool) else
                           (tuple(v) if isinstance(v, list) else v))
                       for k, v in g.get("params", {}).items() if k not in ("axis", "base_faces", "gussets", "inner_hole")}
                for err in check_params(f, exp):
                    report.fail(err)
            elif g["verdict"] == "missed":
                report.count(FeatureKind(g["kind"]), 2)
                report.notes.append(f"known miss: {label}")
            else:
                report.count(FeatureKind(g["kind"]), 2)
                report.fail(f"regression: approved {label} not found")
        wpairs = [(FeatureKind(g["kind"]), {fmap[f] for f in g["faces"] if f in fmap}) for g in wrong]
        for m in match_features(wpairs, result.features):
            report.notes.append(f"known false positive still found: {wrong[m.expect]['kind']} {wrong[m.expect]['faces']}")
        if golden.get("status") != "approved":
            report.passed = True
            report.notes.extend("(unreviewed) " + f for f in report.failures)
            report.failures = []
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {exc}"
        report.fail(report.error)
    return report
