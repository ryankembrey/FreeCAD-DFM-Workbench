# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The second batch of feature rules: rib and boss draft (and how they take
faces over from Minimum Draft Angle), boss hole radius, lead-in and outer
diameter, inside corner radius and moulded text; and for 3D printing small
holes, horizontal holes, pins and text."""

import math
import os
import unittest

import yaml

import dfm_test_support as S
from freecad.DFM.core.models import CheckResult, Severity
from freecad.DFM.core.processes.process import Process
from freecad.DFM.core.recognition import FeatureKind
from freecad.DFM.core.recognition.testing import fixtures as FX
from freecad.DFM.core.rules import Rulebook as R
from test_feature_rules import RuleTest

B = S.B


def severities(found):
    return [f.severity for f in found]


# -- Draft ---------------------------------------------------------------------------------------


class TestRibDraft(RuleTest):
    rule = R.MIN_RIB_DRAFT

    def test_levels(self):
        found = self.findings(self.rule, S.rib(1.5, 10))[1]
        self.assertEqual(severities(found), [Severity.ERROR])
        self.assertEqual(found[0].overview, "Rib 1 (single): 0.00° < 0.50°")
        found = self.findings(self.rule, S.rib(1.5, 10, draft=0.7))[1]
        self.assertEqual(severities(found), [Severity.WARNING])
        self.assertAlmostEqual(found[0].value, 0.7, delta=0.01)
        self.none(self.rule, S.rib(1.5, 10, draft=1.2))

    def test_only_the_sides_are_judged(self):
        pf, found = self.findings(self.rule, S.rib(1.5, 10))
        rib = pf.features(FeatureKind.RIB)[0]
        sides = {n for _k, n in found[0].failing_geometry}
        self.assertEqual(len(sides), 2)
        self.assertLess(sides, set(rib.faces))  # not its ends or top

    def test_rib_tied_to_a_wall_and_floor(self):
        # The recogniser sees this rib as standing on the back wall; its sides
        # still run along the pull, so they are judged.
        from freecad.DFM.core.recognition.testing.shapes import read_brep

        shape = read_brep(os.path.join(S.ROOT, "tests", "parts", "corner_pad_housing.brep"))
        _pf, found = self.findings(self.rule, shape)
        self.assertEqual([sorted(n for _k, n in f.failing_geometry) for f in found], [[16, 18]])


class TestBossDraft(RuleTest):
    rule = R.MIN_BOSS_DRAFT

    def test_boss_and_hole_without_draft(self):
        pf, found = self.findings(self.rule, S.boss(8, 12, hole=3))
        self.assertEqual(severities(found), [Severity.ERROR, Severity.ERROR])
        boss, hole = found
        self.assertTrue(boss.overview.endswith("0.00° < 0.50°"), boss.overview)
        self.assertIn("limits halved", hole.overview)
        self.assertTrue(hole.overview.startswith(pf.features(FeatureKind.HOLE)[0].label))
        self.assertIn("0.25°", hole.overview)

    def test_drafted_boss_with_a_lightly_drafted_hole(self):
        # 1° outside passes; 0.4° in the bore is under its halved aim of 0.5°.
        found = self.findings(self.rule, S.boss(8, 12, hole=3.2, draft=1.0, hole_draft=0.4))[1]
        self.assertEqual(severities(found), [Severity.WARNING])
        self.assertAlmostEqual(found[0].value, 0.4, delta=0.02)


class TestSupersession(unittest.TestCase):
    """While Rib Draft or Boss Draft is on, Minimum Draft Angle drops its
    findings on the faces they judge, and keeps the rest."""

    def test_draft_findings_move_to_the_feature_rules(self):
        from freecad.DFM.app.analysis_runner import AnalysisRunner
        from freecad.DFM.core.utils.geometry import FaceIndex

        shape = S.rib(1.5, 10, y0=5, base=S.boss(8, 12, hole=3))
        pf = S.part_features(shape)
        rib = pf.features(FeatureKind.RIB)[0]
        boss = pf.features(FeatureKind.BOSS)[0]
        hole = pf.features(FeatureKind.HOLE)[0]
        wall = next(n for n in range(1, pf.result.face_count + 1) if pf.result.owner_of(n) is None)

        def general(n):
            return CheckResult(R.MIN_DRAFT_ANGLE, Severity.ERROR, "", "", False, 0.0, 0.5,
                               failing_geometry=[("Face", n)])

        everything = [general(n) for n in (*rib.faces, *boss.faces, *hole.faces, wall)]
        runner = AnalysisRunner()
        runner.analyzer_cache = {"FEATURE_ANALYZER": pf}
        kwargs = {"face_index": FaceIndex(shape)}

        active = [R.MIN_DRAFT_ANGLE, R.MIN_RIB_DRAFT, R.MIN_BOSS_DRAFT]
        kept = {n for r in runner._apply_supersession(everything, active, **kwargs)
                for _k, n in r.failing_geometry}
        self.assertTrue(kept.isdisjoint(set(boss.faces) | set(hole.faces)))
        self.assertIn(wall, kept)
        rib_kept = kept & set(rib.faces)
        self.assertTrue(rib_kept and len(rib_kept) == len(rib.faces) - 2)  # its ends and top stay

        # With the feature rules off, nothing is dropped.
        self.assertEqual(len(runner._apply_supersession(everything, [R.MIN_DRAFT_ANGLE], **kwargs)),
                         len(everything))


# -- Boss details ----------------------------------------------------------------------------------


class TestBossHoleBaseRadius(RuleTest):
    rule = R.MIN_BOSS_HOLE_BASE_RADIUS

    def filleted(self, r):
        s = S.boss(8, 12, hole=4, hole_depth=10)
        if r:
            s = B.fillet(s, r, B.edges_where(s, lambda m, e: abs(m[2] - 4.5) < 1e-6
                                             and abs(math.hypot(m[0] - 40, m[1] - 25) - 2) < 1e-3))
        return s

    def test_levels(self):
        pf, found = self.findings(self.rule, self.filleted(0))
        self.assertEqual(severities(found), [Severity.ERROR])
        self.assertIn("sharp corner at the bottom of its hole", found[0].message)
        hole = pf.features(FeatureKind.HOLE)[0]
        self.assertEqual({n for _k, n in found[0].failing_geometry}, set(hole.faces))
        pf, found = self.findings(self.rule, self.filleted(0.75))
        self.assertEqual(severities(found), [Severity.WARNING])
        self.assertAlmostEqual(found[0].value, 30, delta=0.5)
        fillet = pf.features(FeatureKind.FILLET)[0]
        self.assertTrue(set(fillet.faces) <= {n for _k, n in found[0].failing_geometry})
        self.none(self.rule, self.filleted(1.5))

    def test_holes_that_reach_through_or_have_no_corner(self):
        self.none(self.rule, S.boss(8, 12, hole=3, hole_depth=16))   # through the floor


class TestBossLeadIn(RuleTest):
    rule = R.BOSS_HOLE_LEAD_IN

    def test_square_entry(self):
        _pf, f = self.one(self.rule, S.boss(8, 12, hole=3), Severity.WARNING, 1, "boss")
        self.assertIn("starts square", f.message)

    def test_chamfered_or_countersunk_entry(self):
        self.none(self.rule, FX.by_name("boss_screw_realistic").build())
        s = S.boss(8, 12, hole=3)
        s = B.chamfer(s, 0.4, B.edges_where(s, lambda m, e: abs(m[2] - 14.5) < 1e-6
                                            and abs(math.hypot(m[0] - 40, m[1] - 25) - 1.5) < 1e-3))
        self.none(self.rule, s)


class TestBossOuterDiameter(RuleTest):
    rule = R.MIN_BOSS_OD_RATIO

    def test_levels(self):
        self.none(self.rule, S.boss(8, 12, hole=3))                                   # 2.67×
        self.one(self.rule, S.boss(6, 12, hole=3.2), Severity.WARNING, 1.875, "boss")
        _pf, f = self.one(self.rule, S.boss(6, 12, hole=4), Severity.ERROR, 1.5, "boss")
        self.assertIn("6.00 mm across for a 4.00 mm hole", f.message)


# -- Corners and text ------------------------------------------------------------------------------


def box_with_floor_fillets(r):
    s = S.shelled_box()
    return B.fillet(s, r, B.edges_where(s, lambda m, e: abs(m[2] - 2.5) < 1e-6 and (
        abs(m[0] - 2.5) < 1e-6 or abs(m[0] - 77.5) < 1e-6 or abs(m[1] - 2.5) < 1e-6 or abs(m[1] - 47.5) < 1e-6)))


class TestInternalCornerRadius(RuleTest):
    rule = R.MIN_INTERNAL_CORNER_RADIUS

    def test_levels(self):
        _pf, f = self.one(self.rule, box_with_floor_fillets(0.5), Severity.ERROR, 20, "fillet")
        self.assertIn("0.50 mm radius", f.message)
        self.one(self.rule, box_with_floor_fillets(1.0), Severity.WARNING, 40, "fillet")
        self.none(self.rule, box_with_floor_fillets(1.5))

    def test_roots_of_bosses_and_ribs_are_left_to_their_rules(self):
        self.none(self.rule, FX.by_name("rib_realistic_drafted_filleted").build(), nominal=3.0)
        self.none(self.rule, FX.by_name("boss_screw_realistic").build(), nominal=3.0)


class TestMouldedText(RuleTest):
    rule = R.MIN_TEXT_STROKE

    def test_raised_text_with_bold_strokes_passes(self):
        self.none(self.rule, FX.by_name("text_embossed").build(), nominal=2.0)

    def test_recessed_text_is_a_warning(self):
        _pf, f = self.one(self.rule, FX.by_name("text_engraved").build(), Severity.WARNING, 0.5, "text",
                          nominal=2.0)
        self.assertIn("recessed", f.overview)
        self.assertIn("raised ribs of steel", f.message)

    def test_thin_strokes(self):
        # The fixture's strokes are 0.89 mm: against an aim of 1 mm and a 0.9 mm limit, an error.
        pf = S.part_features(FX.by_name("text_embossed").build(), nominal=2.0)
        found = S.run_rule(self.rule, pf, target=1.0, limit=0.9)
        self.assertEqual(severities(found), [Severity.ERROR])
        self.assertIn("strokes as thin as 0.89 mm", found[0].message)


# -- 3D printing ------------------------------------------------------------------------------------


_FDM = None


def fdm_process():
    global _FDM
    if _FDM is None:
        path = os.path.join(S.ROOT, "freecad", "DFM", "core", "processes", "fdm_3d_printing.yaml")
        with open(path, encoding="utf-8") as f:
            _FDM = Process.from_yaml(yaml.safe_load(f))
    return _FDM


def printed(rule, shape, direction=(0, 0, 1), **limits):
    """Run *rule* of the FDM process on *shape*, printed along *direction*."""
    from OCP.gp import gp_Dir

    from freecad.DFM.core.processes.process import RuleLimit
    from freecad.DFM.core.registries import get_analyzer_class, get_check_class
    from freecad.DFM.core.utils.geometry import EdgeIndex, FaceIndex

    check = get_check_class(rule)()
    self_analyzer = get_analyzer_class(check.required_analyzer_id)()
    pf = self_analyzer.execute(shape, FaceIndex(shape), EdgeIndex(shape), prefs={},
                               PRINT_ORIENTATION=gp_Dir(*direction), NOZZLE_DIAMETER=0.4)
    config = fdm_process().materials["Default"].rule_limits[rule]
    if limits:
        config = RuleLimit(target=str(limits.get("target", config.target)),
                           limit=str(limits.get("limit", config.limit)))
    return check.run_check(pf, config, rule, feedback=fdm_process().rule_feedback[rule])


class TestPrintFeatureAnalyzer(unittest.TestCase):
    def test_print_annotations_and_no_nominal_wall(self):
        from OCP.gp import gp_Dir

        from freecad.DFM.core.models import ProcessRequirement
        from freecad.DFM.core.registries import get_analyzer_class
        from freecad.DFM.core.utils.geometry import EdgeIndex, FaceIndex

        cls = get_analyzer_class("PRINT_FEATURE_ANALYZER")
        self.assertEqual(cls().requirements,
                         {ProcessRequirement.PRINT_ORIENTATION})  # the FDM archetype asks for the nozzle
        shape = B.cut(B.box(0, 0, 0, 40, 20, 20), B.drill((0, 10, 10), (1, 0, 0), 8, 41))
        pf = cls().execute(shape, FaceIndex(shape), EdgeIndex(shape), prefs={},
                           PRINT_ORIENTATION=gp_Dir(0, 0, 1), NOZZLE_DIAMETER=0.4)
        self.assertIsNone(pf.nominal_wall)
        hole = pf.features(FeatureKind.HOLE)[0]
        self.assertTrue(pf.annotation(hole.id).get("horizontal"))


class TestPrintedRules(unittest.TestCase):
    def test_hole_diameter(self):
        found = printed(R.MIN_PRINTED_HOLE_DIAMETER,
                        S.plate_with_holes([(20, 30, 0.8, None), (40, 30, 1.5, None), (60, 30, 3, None)]))
        self.assertEqual(severities(found), [Severity.ERROR, Severity.WARNING])
        self.assertEqual([f.value for f in found], [0.8, 1.5])

    def test_horizontal_hole(self):
        shape = B.cut(B.box(0, 0, 0, 40, 20, 20), B.drill((0, 10, 10), (1, 0, 0), 8, 41))
        found = printed(R.HORIZONTAL_HOLE_TEARDROP, shape)
        self.assertEqual(severities(found), [Severity.WARNING])
        self.assertIn("across the print direction", found[0].overview)
        self.assertEqual(printed(R.HORIZONTAL_HOLE_TEARDROP, shape, direction=(1, 0, 0)), [])
        self.assertEqual(printed(R.HORIZONTAL_HOLE_TEARDROP, FX.by_name("hole_teardrop").build(),
                                 direction=(0, 0, 1)), [])

    def test_pins(self):
        shape = B.fuse(B.box(0, 0, 0, 60, 40, 3), B.cylinder((10, 10, 3), 0.75, 8),
                       B.cylinder((30, 10, 3), 1.25, 8), B.cylinder((50, 10, 3), 2, 10),
                       B.cylinder((30, 30, 3), 1, 1.5))  # the last is a stub, not a pin
        found = printed(R.MIN_PIN_DIAMETER, shape)
        self.assertEqual(severities(found), [Severity.ERROR, Severity.WARNING])
        self.assertEqual([f.value for f in found], [1.5, 2.5])

    def test_text(self):
        self.assertEqual(printed(R.MIN_PRINTED_TEXT_DEPTH, FX.by_name("text_embossed").build()), [])
        found = printed(R.MIN_PRINTED_TEXT_DEPTH, FX.by_name("text_engraved").build(), target=0.8, limit=0.6)
        self.assertEqual(severities(found), [Severity.ERROR])
        self.assertIn("0.50 mm deep", found[0].message)

    def test_small_characters(self):
        from freecad.DFM.core.checks import print_feature_checks as P

        shape = FX.by_name("text_embossed").build()
        original = P.MIN_CHARACTER_HEIGHT
        P.MIN_CHARACTER_HEIGHT = 10.0  # the fixture's letters are 8 mm
        try:
            found = printed(R.MIN_PRINTED_TEXT_DEPTH, shape)
        finally:
            P.MIN_CHARACTER_HEIGHT = original
        self.assertEqual(severities(found), [Severity.WARNING])
        self.assertIn("characters 8.00 mm tall", found[0].overview)


if __name__ == "__main__":
    unittest.main()
