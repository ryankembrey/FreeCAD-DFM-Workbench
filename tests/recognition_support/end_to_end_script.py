# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The moulding process end to end through AnalysisRunner, with FreeCAD
stand-ins: the old rules and the feature rules run together on one part,
then again for another material (checks only). Run by
tests/test_feature_rules_end_to_end.py in a fresh interpreter."""

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
print("end to end: ok")
