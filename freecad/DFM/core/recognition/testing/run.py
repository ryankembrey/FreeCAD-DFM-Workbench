# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The suite runner: every fixture, in every variant, plus the corpus.

Variants test that recognition doesn't depend on how a part is placed or
stored:

* ``base``: as built;
* ``moved``: rotated 37° about (1, 2, 3) and moved by (12, -7, 25);
* ``nurbs``: every surface converted to B-splines (as some STEP exporters
  write them);
* ``step``: written to STEP and read back (periodic faces may split).

Ground truth is chosen on the base part; each variant's faces are mapped
back to the base part's geometrically, so expectations carry over.

Command line::

    python -m freecad.DFM.core.recognition.testing.run [-k NAME] [--tags hole,rib]
        [--variants base,moved] [--corpus DIR] [--json report.json] [-v]

Exit status 1 if anything failed. The GUI bench calls run_suite() too.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex, BRepBuilderAPI_Transform
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

from ..api import recognize
from ..directional import annotate_directional
from ..options import RecognitionOptions
from .._topology.descriptors import _interior_sample
from . import fixtures as FX
from . import select as S
from . import shapes as B
from .compare import CaseResult, judge

VARIANTS = ("base", "moved", "nurbs", "step")
_AXIS = (1.0, 2.0, 3.0)
_ANGLE = 37.0
_SHIFT = (12.0, -7.0, 25.0)


def _trsf() -> gp_Trsf:
    rot = gp_Trsf()
    rot.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(*_AXIS)), math.radians(_ANGLE))
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(*_SHIFT))
    t.Multiply(rot)
    return t


def make_variant(shape, variant: str):
    if variant == "base":
        return shape
    if variant == "moved":
        return BRepBuilderAPI_Transform(shape, _trsf(), True).Shape()
    if variant == "nurbs":
        return B.nurbs(shape)
    if variant == "step":
        return B.step_roundtrip(shape)
    raise ValueError(variant)


def map_direction(d, variant: str):
    if d is None or variant != "moved":
        return d
    rot = gp_Trsf()
    rot.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(*_AXIS)), math.radians(_ANGLE))
    v = gp_Dir(*d).Transformed(rot)
    return (v.X(), v.Y(), v.Z())


def face_map(base, variant_shape, variant: str) -> dict[int, set[int]]:
    """base face number -> variant face numbers (by geometry)."""
    base_faces = S.describe(base)
    var_faces = S.describe(variant_shape)
    if variant in ("base", "nurbs") and len(base_faces) == len(var_faces):
        return {f.index: {f.index} for f in base_faces}
    inv = _trsf().Inverted() if variant == "moved" else gp_Trsf()
    out: dict[int, set[int]] = {f.index: set() for f in base_faces}
    for vf in var_faces:
        p, _n = _interior_sample(vf.face)
        if p is None:
            continue
        q = gp_Pnt(*p).Transformed(inv)
        v = BRepBuilderAPI_MakeVertex(q).Vertex()
        best, best_d = None, None
        for bf in base_faces:
            d = BRepExtrema_DistShapeShape(v, bf.face)
            if d.IsDone() and d.NbSolution() and (best_d is None or d.Value() < best_d):
                best, best_d = bf.index, d.Value()
        if best is not None and best_d is not None and best_d < 1e-2:
            out[best].add(vf.index)
    return out


@dataclass
class SuiteReport:
    cases: list[CaseResult] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.cases)

    def totals(self) -> dict[str, dict[str, float]]:
        tot: dict[str, list[int]] = {}
        for c in self.cases:
            for k, (tp, fp, fn) in c.counts.items():
                t = tot.setdefault(k, [0, 0, 0])
                t[0] += tp
                t[1] += fp
                t[2] += fn
        out = {}
        for k, (tp, fp, fn) in sorted(tot.items()):
            out[k] = {"tp": tp, "fp": fp, "fn": fn,
                      "precision": round(tp / (tp + fp), 4) if tp + fp else 1.0,
                      "recall": round(tp / (tp + fn), 4) if tp + fn else 1.0}
        return out

    def to_dict(self) -> dict:
        return {"passed": self.passed, "seconds": round(self.seconds, 3),
                "summary": {"cases": len(self.cases), "passed": sum(c.passed for c in self.cases),
                            "failed": sum(not c.passed for c in self.cases)},
                "totals": self.totals(), "cases": [c.to_dict() for c in self.cases]}


def run_case(case: FX.Case, variant: str = "base", options: Optional[RecognitionOptions] = None,
             keep_result: bool = False, base_shape=None):
    """Run one fixture in one variant. Returns (CaseResult, shape, result)."""
    report = CaseResult(case.name, variant)
    shape = result = None
    try:
        base = base_shape if base_shape is not None else case.build()
        shape = make_variant(base, variant)
        opts = options or RecognitionOptions.from_dict(case.options)
        t = time.perf_counter()
        result = recognize(shape, opts, strict=True)
        report.seconds = time.perf_counter() - t
        report.face_count = result.face_count
        mapping = face_map(base, shape, variant)
        expected_faces = []
        for e in case.expect:
            base_set = e.faces(base)
            mapped = set()
            for f in base_set:
                mapped |= mapping.get(f, set())
            expected_faces.append(mapped)
        judge(report, case.expect, expected_faces, result.features, case.allow)
        if case.max_seconds and report.seconds > case.max_seconds:
            report.fail(f"took {report.seconds:.1f}s, limit {case.max_seconds:.1f}s")
        if case.directional is not None:
            _judge_directional(report, case, base, shape, result, mapping, variant, opts)
        for w in result.warnings:
            report.fail("warning: " + w.splitlines()[0])
        if keep_result:
            report.result = result.to_dict()
    except Exception as exc:  # a crash is a failure, not the end of the run
        report.error = f"{type(exc).__name__}: {exc}"
        report.fail(report.error)
        report.notes.append(traceback.format_exc())
    return report, shape, result


def _judge_directional(report, case, base, shape, result, mapping, variant, opts):
    dx = case.directional
    rep = annotate_directional(
        result, shape, pull=map_direction(dx.pull, variant),
        side_actions=[map_direction(s, variant) for s in dx.side_actions],
        print_direction=map_direction(dx.print_direction, variant), nozzle_diameter=dx.nozzle, options=opts)
    report.directional = rep.to_dict()

    def mapped(sel):
        out = set()
        for f in sel(base):
            out |= mapping.get(f, set())
        return out

    sub = CaseResult(case.name, variant)
    judge(sub, dx.features, [mapped(e.faces) for e in dx.features], rep.features, dx.allow)
    for text in sub.failures:
        report.fail("directional: " + text)
    for k, v in sub.counts.items():
        t = report.counts.setdefault(k, [0, 0, 0])
        for i in range(3):
            t[i] += v[i]
    for sel, key, value in dx.annotations:
        faces = mapped(sel)
        owners = {result.owner_of(f).id for f in faces if result.owner_of(f) is not None}
        if not owners:
            report.fail(f"directional: no feature on {sel!r} to annotate")
            continue
        for fid in owners:
            got = rep.annotations.get(fid, {}).get(key)
            if got != value:
                report.fail(f"directional: feature {fid} {key} = {got!r}, wanted {value!r}")


def run_suite(cases: Optional[Iterable[FX.Case]] = None, variants: Iterable[str] = VARIANTS,
              options: Optional[RecognitionOptions] = None, corpus_dir: Optional[str] = None,
              progress: Optional[Callable[[str, int, int], None]] = None, keep_results: bool = False,
              check_abort: Optional[Callable[[], bool]] = None) -> SuiteReport:
    cases = list(cases if cases is not None else FX.CASES)
    jobs = [(c, v) for c in cases for v in variants if v in c.variants]
    golden = []
    if corpus_dir:
        from .corpus import list_goldens
        golden = list_goldens(corpus_dir)
    total = len(jobs) + len(golden)
    report = SuiteReport()
    t0 = time.perf_counter()
    built: dict[str, object] = {}
    for i, (c, v) in enumerate(jobs):
        if check_abort and check_abort():
            break
        if progress:
            progress(f"{c.name} [{v}]", i, total)
        base = built.get(c.name)
        if base is None:
            try:
                base = c.build()
            except Exception:
                base = None
            built = {c.name: base}
        r, _shape, _res = run_case(c, v, options, keep_results, base_shape=base)
        report.cases.append(r)
    if golden:
        from .corpus import check_golden
        for j, path in enumerate(golden):
            if check_abort and check_abort():
                break
            if progress:
                progress(path, len(jobs) + j, total)
            report.cases.append(check_golden(path, options, keep_result=keep_results))
    report.seconds = time.perf_counter() - t0
    return report


def format_report(rep: SuiteReport, verbose: bool = False) -> str:
    lines = []
    for c in rep.cases:
        mark = "PASS" if c.passed else "FAIL"
        lines.append(f"{mark}  {c.name:<38} {c.variant:<6} {c.face_count:>5} faces {c.seconds * 1000:8.1f} ms")
        if not c.passed or verbose:
            for f in c.failures:
                lines.append(f"        - {f}")
            if verbose:
                for n in c.notes:
                    if not n.startswith("Traceback"):
                        lines.append(f"        . {n}")
            if c.error:
                tb = [n for n in c.notes if n.startswith("Traceback")]
                lines.extend("        " + l for l in (tb[0].splitlines() if tb else []))
    lines.append("")
    lines.append(f"{'kind':<12} {'tp':>5} {'fp':>5} {'fn':>5} {'precision':>10} {'recall':>8}")
    for k, t in rep.totals().items():
        lines.append(f"{k:<12} {t['tp']:>5} {t['fp']:>5} {t['fn']:>5} {t['precision']:>10.3f} {t['recall']:>8.3f}")
    n_pass = sum(c.passed for c in rep.cases)
    lines.append(f"\n{n_pass}/{len(rep.cases)} passed in {rep.seconds:.1f} s")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Feature recognition test suite")
    ap.add_argument("-k", "--pattern", default="", help="only cases whose name contains this")
    ap.add_argument("--tags", default="", help="comma-separated tags (any)")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--corpus", default=None, help="folder of approved goldens to check too")
    ap.add_argument("--json", default=None, help="write the full report here")
    ap.add_argument("--export", default=None, help="write every fixture (BREP, STEP, expectations) here")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    cases = FX.select_cases(tags=[t for t in args.tags.split(",") if t] or None, pattern=args.pattern)
    if args.export:
        from .corpus import export_fixtures
        n = export_fixtures(args.export, cases)
        print(f"exported {n} fixtures to {args.export}")
        return 0
    variants = [v for v in args.variants.split(",") if v]

    def progress(name, i, n):
        if args.verbose:
            print(f"[{i + 1}/{n}] {name}", file=sys.stderr)

    rep = run_suite(cases, variants, corpus_dir=args.corpus, progress=progress)
    print(format_report(rep, args.verbose))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rep.to_dict(), fh, indent=1)
    return 0 if rep.passed else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
