# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The moulding process end to end through AnalysisRunner, with FreeCAD
stand-ins: the old rules and the feature rules run together on one part,
then again for another material (checks only). Then the FDM, resin and
powder bed processes. Run by tests/test_feature_rules_end_to_end.py in a
fresh interpreter."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import freecad_stubs  # noqa: E402,F401

from OCP.gp import gp_Dir  # noqa: E402

import dfm_test_support as S  # noqa: E402
from freecad.DFM.app import analysis_runner  # noqa: E402
from freecad.DFM.core.models import Severity  # noqa: E402
from freecad.DFM.core.rules import Rulebook as R  # noqa: E402

B = S.B
# The runner converts a FreeCAD shape; here the part is already OCP.
analysis_runner.freecad_to_ocp = lambda shape: shape

# A housing: a thick-walled boss standing alone, a thick tall rib, a deep hole.
part = S.boss(8, 12, at=(20, 25), hole=3.0, hole_depth=6)
part = S.rib(2.5, 14, y0=38, x0=35, length=30, base=part)
part = B.cut(part, B.drill((60, 15, 2.5), (0, 0, -1), 1.0, 2.5))

runner = analysis_runner.AnalysisRunner()
steps = []
results = runner.run_analysis(
    "Plastic Injection Molding", "Default", part,
    progress_cb=lambda done, total, text: steps.append((done, total, text)),
    PULL_DIRECTION=gp_Dir(0, 0, 1), SIDE_ACTIONS=[], NOMINAL_WALL_THICKNESS={"mode": "auto"},
)
by_rule = {}
for r in results:
    by_rule.setdefault(r.rule_id, []).append(r)
for rule, found in by_rule.items():
    for f in found:
        print(f"{rule.name:28s} {f.severity.name:7s} {f.overview}")

expected = {R.MAX_BOSS_WALL_RATIO, R.MIN_BOSS_CORE_DEPTH, R.STANDALONE_BOSS, R.MAX_RIB_THICKNESS_RATIO,
            R.MAX_RIB_HEIGHT_RATIO, R.MIN_BOSS_ROOT_RADIUS, R.MIN_RIB_ROOT_RADIUS}
missing = expected - set(by_rule)
assert not missing, f"missing findings for {sorted(r.name for r in missing)}"
assert R.MIN_DRAFT_ANGLE in by_rule, "the old rules still run"  # nothing on this part is drafted
assert all(f.failing_geometry for fs in by_rule.values() for f in fs if f.severity != Severity.INFO)
assert "2.50 mm (measured)" in by_rule[R.MAX_BOSS_WALL_RATIO][0].message
assert steps and steps[-1][0] <= steps[-1][1], steps[-1:]
assert "FEATURE_ANALYZER" in runner.analyzer_cache

# Rib and boss draft take their faces over from the general draft rule.
judged = {n for r in results if r.rule_id in (R.MIN_RIB_DRAFT, R.MIN_BOSS_DRAFT) for _k, n in r.failing_geometry}
assert judged, "rib and boss draft findings"
general = {n for r in by_rule[R.MIN_DRAFT_ANGLE] for _k, n in r.failing_geometry}
assert not (judged & general), sorted(judged & general)

# Another material: the checks again, nothing measured again.
cached = dict(runner.analyzer_cache)
again = runner.rerun_checks_only("Plastic Injection Molding", "PP")
assert runner.analyzer_cache == cached
assert {r.rule_id for r in again} >= expected, sorted({r.rule_id.name for r in again})

# Manual nominal wall: entered 4 mm, the 2.5 mm rib is 62.5%: a warning now.
results = runner.run_analysis(
    "Plastic Injection Molding", "Default", part,
    PULL_DIRECTION=gp_Dir(0, 0, 1), SIDE_ACTIONS=[], NOMINAL_WALL_THICKNESS={"mode": "manual", "value": 4.0},
)
rib = [r for r in results if r.rule_id == R.MAX_RIB_THICKNESS_RATIO]
assert [r.severity for r in rib] == [Severity.WARNING], [r.overview for r in rib]
assert "4.00 mm (entered)" in rib[0].message
# The FDM process: its feature rules read the print-direction recognition.
printed = B.fuse(B.box(0, 0, 0, 60, 40, 3), B.cylinder((10, 10, 3), 0.75, 8))
printed = B.cut(printed, B.drill((40, 20, 3), (0, 0, -1), 0.8, 4))
results = analysis_runner.AnalysisRunner().run_analysis(
    "FDM", "PLA", printed, PRINT_ORIENTATION=gp_Dir(0, 0, 1), NOZZLE_DIAMETER=0.4, LAYER_HEIGHT=0.2)
fdm = {r.rule_id for r in results}
assert {R.MIN_PIN_DIAMETER, R.MIN_PRINTED_HOLE_DIAMETER, R.PIN_ROOT_FILLET, R.BED_EDGE_CHAMFER} <= fdm, \
    sorted(r.name for r in fdm)

# Resin and powder bed: a hollow block with one small drain hole, a pocket
# open towards the tank and a thin pin.
hollow = B.cut(B.box(0, 0, 0, 60, 40, 20), B.box(10, 10, 5, 30, 30, 15), B.drill((20, 20, 20), (0, 0, -1), 2, 6),
               B.box(38, 8, 12, 56, 32, 21))
hollow = B.fuse(hollow, B.cylinder((47, 20, 12), 0.3, 15))  # 25 diameters tall
resin = analysis_runner.AnalysisRunner().run_analysis(
    "Resin 3D Printing (SLA)", "Default", hollow, PRINT_ORIENTATION=gp_Dir(0, 0, 1))
found = {r.rule_id for r in resin}
assert {R.MIN_ESCAPE_HOLE_DIAMETER, R.MAX_CUP_AREA, R.MAX_PIN_SLENDERNESS} <= found, sorted(r.name for r in found)
single = [r for r in resin if r.rule_id == R.MIN_ESCAPE_HOLE_DIAMETER and "one escape hole" in r.overview]
assert single, "the single-hole warning"

# Powder bed: no print orientation given; the same part, sealed.
sealed = B.cut(B.box(0, 0, 0, 150, 12, 12), B.box(20, 3, 3, 120, 9, 9))
powder_runner = analysis_runner.AnalysisRunner()
powder = powder_runner.run_analysis("Powder Bed Fusion (SLS)", "PA12 (SLS)", sealed)
found = {r.rule_id: r for r in powder}
assert found[R.SEALED_CAVITY].severity == Severity.WARNING, found.get(R.SEALED_CAVITY)
assert R.MAX_PART_ASPECT_RATIO in found and not found[R.MAX_PART_ASPECT_RATIO].failing_geometry
# TPU: same measurements, its own thresholds.
again = powder_runner.rerun_checks_only("Powder Bed Fusion (SLS)", "TPU")
assert {r.rule_id for r in again} >= {R.SEALED_CAVITY, R.MAX_PART_ASPECT_RATIO}
print("end to end: ok")
