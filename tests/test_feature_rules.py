# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The feature-aware moulding rules, each on parts built to pass, warn and fail.

The base part is a 2.5 mm shelled box (dfm_test_support.shelled_box), so
the nominal wall is measured as 2.5 mm. Each finding is checked for its
severity, value, faces, and a message naming the feature and the nominal
wall.
"""

import dataclasses
import math
import os
import unittest

import dfm_test_support as S
from freecad.DFM.core.models import Severity
from freecad.DFM.core.recognition import FeatureKind
from freecad.DFM.core.recognition.testing import fixtures as FX
from freecad.DFM.core.rules import Rulebook as R

B = S.B
NOMINAL = "2.50 mm (measured)"


class RuleTest(unittest.TestCase):
    def findings(self, rule, shape, **kwargs):
        pf = S.part_features(shape, nominal=kwargs.pop("nominal", None))
        return pf, S.run_rule(rule, pf, shape, **kwargs)

    def one(self, rule, shape, severity, value, kind, **kwargs):
        """Exactly one finding, of *severity*, about the one feature of *kind*."""
        pf, found = self.findings(rule, shape, **kwargs)
        self.assertEqual(len(found), 1, [f.overview for f in found])
        f = found[0]
        self.assertEqual(f.severity, severity, f.overview)
        self.assertAlmostEqual(f.value, value, delta=0.6 if f.unit == "%" else 0.02)
        feature = pf.features(FeatureKind(kind))[0]
        self.assertTrue({("Face", n) for n in feature.faces} <= set(f.failing_geometry))
        self.assertIn(feature.label, f.message)
        self.assertIn(feature.label, f.overview)
        return pf, f

    def none(self, rule, shape, **kwargs):
        _pf, found = self.findings(rule, shape, **kwargs)
        self.assertEqual(found, [], [f.overview for f in found])


# -- Bosses ------------------------------------------------------------------------------


def screw_boss(wall, height=12.0, od=8.0, draft=0.0, **kw):
    """A screw boss whose wall at the base is *wall* (its core reaches the base)."""
    hole = od - 2 * wall
    if draft:
        hole += height * math.tan(math.radians(draft))  # the bore's Ø at mid depth
    return S.boss(od, height, hole=hole, draft=draft, hole_draft=draft, **kw)


class TestBossWall(RuleTest):
    rule = R.MAX_BOSS_WALL_RATIO

    def test_thin_wall_passes(self):
        self.none(self.rule, screw_boss(1.4))  # 56%

    def test_warning(self):
        pf, f = self.one(self.rule, screw_boss(1.75), Severity.WARNING, 70, "boss")
        hole = pf.features(FeatureKind.HOLE)[0]
        self.assertTrue({("Face", n) for n in hole.faces} <= set(f.failing_geometry))
        self.assertIn(NOMINAL, f.message)
        self.assertIn("1.75 mm wall", f.message)
        self.assertEqual(f.unit, "%")
        self.assertRegex(f.overview, r"^Boss \d+ \(screw\): 70% > 60%$")

    def test_error(self):
        _pf, f = self.one(self.rule, screw_boss(2.5), Severity.ERROR, 100, "boss")
        self.assertIn(NOMINAL, f.message)
        self.assertTrue(f.overview.endswith("100% > 75%"), f.overview)

    def test_solid_boss_says_core_it_out(self):
        _pf, f = self.one(self.rule, S.boss(8, 12), Severity.ERROR, 320, "boss")
        self.assertIn("solid 8.00 mm section", f.message)
        self.assertIn("Core it out", f.message)

    def test_pin_is_skipped(self):
        self.none(self.rule, S.boss(3, 15))

    def test_drafted_bosses_are_measured_at_the_base(self):
        for wall, severity in ((1.4, None), (1.75, Severity.WARNING), (2.5, Severity.ERROR)):
            shape = screw_boss(wall, draft=1.0)
            if severity is None:
                self.none(self.rule, shape)
            else:
                self.one(self.rule, shape, severity, 100 * wall / 2.5, "boss")

    def test_manual_nominal(self):
        # Entered as 3.5 mm, the 2.5 mm wall is 71%: a warning, not an error.
        _pf, f = self.one(self.rule, screw_boss(2.5), Severity.WARNING, 71.4, "boss", nominal=3.5)
        self.assertIn("3.50 mm (entered)", f.message)


class TestBossHeight(RuleTest):
    rule = R.MAX_BOSS_HEIGHT_RATIO

    def test_short_passes(self):
        self.none(self.rule, S.boss(8, 12, hole=3))  # 1.5×

    def test_warning(self):
        _pf, f = self.one(self.rule, S.boss(6, 16.5, hole=3), Severity.WARNING, 2.75, "boss")
        self.assertEqual(f.unit, "×")
        self.assertIn("16.50 mm tall", f.message)

    def test_error_for_a_slender_cored_boss(self):
        # Over 3 diameters tall but cored: a screw boss, so it is checked.
        self.one(self.rule, S.boss(6, 20, hole=3), Severity.ERROR, 3.33, "boss")

    def test_solid_pin_is_skipped(self):
        self.none(self.rule, S.boss(3, 15))


class TestBossCoreDepth(RuleTest):
    rule = R.MIN_BOSS_CORE_DEPTH

    def test_core_to_the_base_passes(self):
        self.none(self.rule, S.boss(8, 12, hole=3))

    def test_through_hole_passes(self):
        self.none(self.rule, S.boss(8, 12, hole=3, hole_depth=14.5 + 1))

    def test_half_depth_is_an_error(self):
        _pf, f = self.one(self.rule, S.boss(8, 12, hole=3, hole_depth=6), Severity.ERROR, 50, "boss")
        self.assertIn("6.00 mm deep in a 12.00 mm tall boss", f.message)

    def test_nearly_to_the_base_is_a_warning(self):
        self.one(self.rule, S.boss(8, 12, hole=3, hole_depth=10.5), Severity.WARNING, 87.5, "boss")

    def test_solid_boss_is_left_to_the_wall_rule(self):
        self.none(self.rule, S.boss(8, 12))


class TestStandaloneBoss(RuleTest):
    rule = R.STANDALONE_BOSS

    def test_isolated_boss(self):
        _pf, f = self.one(self.rule, S.boss(8, 12, hole=3), Severity.WARNING, 1, "boss")
        self.assertTrue(f.overview.endswith("not tied to a wall or rib"), f.overview)

    def test_severity_comes_from_the_process(self):
        _pf, found = self.findings(self.rule, S.boss(8, 12, hole=3), severity="ERROR")
        self.assertEqual([f.severity for f in found], [Severity.ERROR])

    def test_boss_tied_to_a_wall(self):
        self.none(self.rule, FX.by_name("boss_tied_to_wall").build())

    def test_boss_with_gussets(self):
        self.none(self.rule, FX.by_name("boss_with_gussets").build())

    def test_boss_with_a_rib_to_the_wall(self):
        s = B.fuse(S.boss(8, 12, at=(40, 25), hole=3), B.box(42, 24.25, 2.5, 77.5, 25.75, 10))
        self.none(self.rule, s)

    def test_short_boss_is_fine(self):
        self.none(self.rule, S.boss(10, 6, hole=3))


# -- Ribs ----------------------------------------------------------------------------------


class TestRibThickness(RuleTest):
    rule = R.MAX_RIB_THICKNESS_RATIO

    def test_thin_rib_passes(self):
        self.none(self.rule, S.rib(1.25, 8))  # 50%

    def test_warning(self):
        _pf, f = self.one(self.rule, S.rib(1.75, 8), Severity.WARNING, 70, "rib")
        self.assertIn("1.75 mm thick at its base", f.message)
        self.assertIn(NOMINAL, f.message)

    def test_error(self):
        self.one(self.rule, S.rib(2.5, 8), Severity.ERROR, 100, "rib")

    def test_drafted_rib_is_measured_at_its_root(self):
        # 1.75 mm at the root, thinner at the tip: still a warning.
        self.one(self.rule, S.rib(1.75, 8, draft=1.0), Severity.WARNING, 70, "rib")

    def test_crush_ribs_are_skipped(self):
        pf = S.part_features(FX.by_name("crush_ribs_straight").build(), nominal=0.5)
        self.assertTrue(any(f.subtype == "crush" for f in pf.result.of_kind(FeatureKind.RIB)))
        self.assertEqual(S.run_rule(self.rule, pf), [])


class TestRibHeight(RuleTest):
    rule = R.MAX_RIB_HEIGHT_RATIO

    def test_short_rib_passes(self):
        self.none(self.rule, S.rib(1.5, 7))  # 2.8×

    def test_warning(self):
        self.one(self.rule, S.rib(1.5, 10), Severity.WARNING, 4.0, "rib")

    def test_error(self):
        _pf, f = self.one(self.rule, S.rib(1.5, 14), Severity.ERROR, 5.6, "rib")
        self.assertIn("14.00 mm tall", f.message)

    def test_gussets_are_skipped(self):
        self.none(self.rule, FX.by_name("boss_with_gussets").build(), nominal=1.0)


# -- Spacing ---------------------------------------------------------------------------------


def two_bosses(gap):
    return B.fuse(S.boss(8, 12, at=(30, 25), hole=3), B.cylinder((38 + gap, 25, 2.5), 4, 12))


def two_ribs(gap):
    return S.rib(1.5, 8, y0=21.5 + gap, base=S.rib(1.5, 8, y0=20))


class PairTest(RuleTest):
    def pair(self, rule, shape, severity, value):
        pf, found = self.findings(rule, shape)
        self.assertEqual(len(found), 1, [f.overview for f in found])  # each pair once
        f = found[0]
        self.assertEqual(f.severity, severity, f.overview)
        self.assertAlmostEqual(f.value, value, delta=0.02)
        self.assertIn("\u2194", f.overview)
        a, b = pf.features(FeatureKind(rule.name.split("_")[1].lower()))
        self.assertEqual({("Face", n) for n in a.faces + b.faces}, set(f.failing_geometry))
        self.assertIn(a.label, f.message)
        self.assertIn(b.label, f.message)
        self.assertIn(NOMINAL, f.message)


class TestBossSpacing(PairTest):
    rule = R.MIN_BOSS_SPACING

    def test_spacings(self):
        self.pair(self.rule, two_bosses(2.5), Severity.WARNING, 1.0)    # 1×: at the limit
        self.pair(self.rule, two_bosses(1.25), Severity.ERROR, 0.5)
        self.pair(self.rule, two_bosses(3.75), Severity.WARNING, 1.5)
        self.none(self.rule, two_bosses(7.5))                           # 3×

    def test_bosses_on_opposite_sides_are_not_a_pair(self):
        s = B.fuse(S.boss(8, 12, at=(30, 25), hole=3), B.cylinder((40, 25, 0), 4, 10, (0, 0, -1)))
        self.none(self.rule, s)


class TestRibSpacing(PairTest):
    rule = R.MIN_RIB_SPACING

    def test_spacings(self):
        self.pair(self.rule, two_ribs(1.25), Severity.ERROR, 0.5)
        self.pair(self.rule, two_ribs(3.75), Severity.WARNING, 1.5)
        self.none(self.rule, two_ribs(7.5))

    def test_ribs_end_to_end_are_not_a_pair(self):
        self.none(self.rule, S.rib(1.5, 8, x0=45, length=20, base=S.rib(1.5, 8, x0=10, length=20)))


# -- Root radii -------------------------------------------------------------------------------


def filleted_boss(r):
    s = S.boss(8, 12, hole=3)
    if r:
        s = B.fillet(s, r, B.edges_where(s, lambda m, e: abs(m[2] - 2.5) < 1e-6 and abs(
            math.hypot(m[0] - 40, m[1] - 25) - 4) < 1e-3))
    return s


def filleted_rib(r):
    s = S.rib(1.5, 8)
    if r:
        s = B.fillet(s, r, B.edges_where(s, lambda m, e: abs(m[2] - 2.5) < 1e-6 and 15 < m[0] < 65
                                         and 19 < m[1] < 22))
    return s


class TestRootRadius(RuleTest):
    def test_boss(self):
        rule = R.MIN_BOSS_ROOT_RADIUS
        _pf, f = self.one(rule, filleted_boss(0), Severity.ERROR, 0, "boss")
        self.assertIn("sharp root", f.message)
        _pf, f = self.one(rule, filleted_boss(0.75), Severity.WARNING, 30, "boss")  # 0.3 T
        self.assertIn("0.75 mm root radius", f.message)
        fillet = _pf.features(FeatureKind.FILLET)[0]
        self.assertTrue({("Face", n) for n in fillet.faces} <= set(f.failing_geometry))
        self.none(rule, filleted_boss(1.5))  # 0.6 T

    def test_rib(self):
        rule = R.MIN_RIB_ROOT_RADIUS
        self.one(rule, filleted_rib(0), Severity.ERROR, 0, "rib")
        self.one(rule, filleted_rib(0.75), Severity.WARNING, 30, "rib")
        self.none(rule, filleted_rib(1.5))


# -- Holes ----------------------------------------------------------------------------------------


class TestCoredHoleDepth(RuleTest):
    rule = R.MAX_HOLE_DEPTH_RATIO

    def blind(self, k):
        return S.plate_with_holes([(50, 30, 4, 4 * k)], size=(100, 60, 20))

    def through(self, k):
        return S.plate_with_holes([(50, 30, 20 / k, None)], size=(100, 60, 20))

    def test_blind(self):
        self.none(self.rule, self.blind(1.5))
        _pf, f = self.one(self.rule, self.blind(2.5), Severity.WARNING, 2.5, "hole")
        self.assertIn("blind hole", f.message)
        self.one(self.rule, self.blind(4), Severity.ERROR, 4.0, "hole")

    def test_through_holes_may_go_twice_as_deep(self):
        for k in (1.5, 2.5, 4):
            self.none(self.rule, self.through(k))
        _pf, f = self.one(self.rule, self.through(5), Severity.WARNING, 5.0, "hole")
        self.assertIn("limits doubled", f.overview)
        self.assertIn("through hole", f.message)
        self.one(self.rule, self.through(7), Severity.ERROR, 7.0, "hole")

    def test_holes_across_the_pull_are_left_out(self):
        s = B.cut(B.box(0, 0, 0, 100, 60, 20), B.drill((0, 30, 10), (1, 0, 0), 4, 16))
        self.none(self.rule, s)

    def test_side_action_holes_are_checked(self):
        s = B.cut(B.box(0, 0, 0, 100, 60, 20), B.drill((0, 30, 10), (1, 0, 0), 4, 16))
        pf = S.part_features(s, sides=[(-1, 0, 0)])
        found = S.run_rule(self.rule, pf, s)
        self.assertEqual([f.severity for f in found], [Severity.ERROR])


class TestHoleSpacing(RuleTest):
    rule = R.MIN_HOLE_EDGE_DISTANCE

    def test_hole_near_the_edge(self):
        # 2 mm from the edge; the 4 mm hole is wider than the wall: 0.5×.
        _pf, f = self.one(self.rule, S.plate_with_holes([(4, 30, 4, None)]), Severity.ERROR, 0.5, "hole",
                          nominal=2.5)
        self.assertIn("from the edge of its face", f.message)
        self.assertIn("hole diameter, 4.00 mm", f.message)

    def test_two_holes_close_together_are_one_finding(self):
        pf, found = self.findings(self.rule, S.plate_with_holes([(50, 30, 4, None), (57, 30, 4, None)]),
                                  nominal=2.5)
        self.assertEqual(len(found), 1, [f.overview for f in found])
        self.assertAlmostEqual(found[0].value, 0.75, delta=0.01)
        self.assertEqual(found[0].severity, Severity.ERROR)
        holes = pf.features(FeatureKind.HOLE)
        self.assertEqual({("Face", n) for h in holes for n in h.faces}, set(found[0].failing_geometry))

    def test_well_placed_hole(self):
        self.none(self.rule, S.plate_with_holes([(50, 30, 4, None), (62, 30, 4, None)]), nominal=2.5)

    def test_holes_in_bosses_are_left_to_the_boss_rules(self):
        self.none(self.rule, S.boss(8, 12, hole=3))


# -- Walls: transitions and thin steel ----------------------------------------------------------


def run_analyzed(rule, shape, material=None, **kwargs):
    """Run *rule*'s own analyzer on *shape*, then its check."""
    from OCP.gp import gp_Dir

    from freecad.DFM.core.registries import get_analyzer_class, get_check_class
    from freecad.DFM.core.utils.geometry import EdgeIndex, FaceIndex

    analyzer = get_analyzer_class(get_check_class(rule)().required_analyzer_id)()
    data = analyzer.execute(shape, FaceIndex(shape), EdgeIndex(shape), prefs={},
                            PULL_DIRECTION=gp_Dir(0, 0, 1))
    return S.run_rule(rule, data, shape, material=material, **kwargs)


def stepped_plate(thick, thin, ramp=0.0, fillet=0.0):
    """A plate *thick* mm thick for x < 50 and *thin* beyond, the step made
    over a ramp *ramp* long (0: a sharp step)."""
    plate = B.box(0, 0, 0, 100, 60, thick)
    if ramp:
        return B.cut(plate, B.prism([(50, -1, thick + 0.01), (50 + ramp, -1, thin), (101, -1, thin),
                                     (101, -1, thick + 0.01)], (0, 62, 0)))
    s = B.cut(plate, B.box(50, -1, thin, 101, 61, thick + 1))
    if fillet:
        s = B.fillet(s, fillet, B.edges_where(s, lambda m, e: abs(m[0] - 50) < 1e-6 and abs(m[2] - thin) < 1e-6))
    return s


class TestWallTransition(unittest.TestCase):
    rule = R.MAX_WALL_THICKNESS_VARIATION

    def test_sharp_step(self):
        found = run_analyzed(self.rule, stepped_plate(6, 3))
        self.assertEqual([f.severity for f in found], [Severity.ERROR])
        self.assertAlmostEqual(found[0].value, 50, delta=0.5)
        self.assertIn("from 6.00 mm to 3.00 mm sharply", found[0].message)
        self.assertIn("9.00 mm", found[0].message)  # the taper it needs: 3 × the step
        self.assertEqual(len(found[0].failing_geometry), 3)  # both levels and the riser

    def test_small_step_is_a_warning(self):
        found = run_analyzed(self.rule, stepped_plate(3, 2.5))
        self.assertEqual([f.severity for f in found], [Severity.WARNING])
        self.assertAlmostEqual(found[0].value, 16.7, delta=0.1)

    def test_semi_crystalline_materials_are_stricter(self):
        found = run_analyzed(self.rule, stepped_plate(3, 2.5), material="PP")
        self.assertEqual([f.severity for f in found], [Severity.ERROR])  # 16.7% > 15%
        self.assertEqual(run_analyzed(self.rule, stepped_plate(3, 2.5), material="ABS")[0].severity,
                         Severity.WARNING)

    def test_filleted_step_is_still_abrupt(self):
        self.assertEqual([f.severity for f in run_analyzed(self.rule, stepped_plate(6, 3, fillet=1.0))],
                         [Severity.ERROR])

    def test_gradual_ramp_passes(self):
        self.assertEqual(run_analyzed(self.rule, stepped_plate(6, 3, ramp=10)), [])

    def test_short_ramp_is_flagged(self):
        found = run_analyzed(self.rule, stepped_plate(6, 3, ramp=4))
        self.assertEqual([f.severity for f in found], [Severity.ERROR])
        self.assertRegex(found[0].message, r"over (3\.9\d|4\.0\d) mm")

    def test_pad_on_a_floor(self):
        # A 4 mm pad on a 2.4 mm floor is recognised only as a protrusion (an
        # unclaimed region): it thickens the wall to 6.4 mm, a 62.5% step.
        shape = B.fuse(S.shelled_box(2.4, 80, 50, 25), B.box(40, 25, 2.4, 65, 42, 6.4))
        found = run_analyzed(self.rule, shape, material="PLA")
        self.assertEqual([f.severity for f in found], [Severity.ERROR])
        self.assertAlmostEqual(found[0].value, 62.5, delta=0.1)
        self.assertIn("from 6.40 mm to 2.40 mm sharply", found[0].message)
        self.assertAlmostEqual(S.part_features(shape).nominal_wall, 2.4, delta=0.05)

    def test_corner_pad_in_a_housing(self):
        # A real part: a 4 mm pad in the corner of a 2.4 mm housing, merged
        # into two walls and claimed by no feature, with a snap fit standing
        # on the same floor (which must still count as a wall).
        from freecad.DFM.core.recognition.testing.shapes import read_brep

        shape = read_brep(os.path.join(S.ROOT, "tests", "parts", "corner_pad_housing.brep"))
        found = run_analyzed(self.rule, shape, material="ABS")
        self.assertEqual([f.severity for f in found], [Severity.ERROR], [f.overview for f in found])
        self.assertAlmostEqual(found[0].value, 62.5, delta=0.1)
        # The pad's top, both its open sides, and the floor.
        self.assertEqual(sorted(n for _k, n in found[0].failing_geometry), [15, 19, 20, 21])

    def test_uniform_parts_pass(self):
        for shape in (S.shelled_box(), FX.by_name("groove_housing_rim").build(),
                      FX.by_name("molded_tray_drafted").build(), S.boss(8, 12, hole=3)):
            self.assertEqual(run_analyzed(self.rule, shape), [])


class TestThinSteel(unittest.TestCase):
    rule = R.MIN_MOLD_STEEL_THICKNESS

    def slot(self, width, depth):
        return B.cut(B.box(0, 0, 0, 100, 60, 15), B.box(20, -1, 15 - depth, 20 + width, 61, 16))

    def test_deep_narrow_slot(self):
        found = run_analyzed(self.rule, self.slot(0.8, 10))
        self.assertEqual([f.severity for f in found], [Severity.ERROR])
        self.assertAlmostEqual(found[0].value, 0.8, delta=0.01)
        self.assertEqual(found[0].unit, "mm")
        self.assertIn("0.80 mm wide and 10.00 mm deep", found[0].message)
        self.assertEqual(len(found[0].failing_geometry), 2)

    def test_warning_and_pass(self):
        self.assertEqual([f.severity for f in run_analyzed(self.rule, self.slot(1.2, 10))], [Severity.WARNING])
        self.assertEqual(run_analyzed(self.rule, self.slot(3, 10)), [])

    def test_shallow_gap_is_only_a_warning(self):
        found = run_analyzed(self.rule, self.slot(0.8, 0.5))
        self.assertEqual([f.severity for f in found], [Severity.WARNING])
        self.assertIn("shallow", found[0].overview)

    def test_close_ribs(self):
        found = run_analyzed(self.rule, two_ribs(0.9))
        self.assertEqual([f.severity for f in found], [Severity.ERROR])

    def test_holes_and_corners_are_not_thin_steel(self):
        self.assertEqual(run_analyzed(self.rule, S.plate_with_holes([(50, 30, 1, None)])), [])
        self.assertEqual(run_analyzed(self.rule, S.shelled_box()), [])
        self.assertEqual(run_analyzed(self.rule, FX.by_name("text_embossed_drafted").build()), [])


# -- Living hinges and snap fits ---------------------------------------------------------------


def hinge(web, plate=2.0):
    """A plate *plate* mm thick with a flat-bottomed groove leaving a web *web* thick."""
    return B.cut(B.box(0, 0, 0, 64, 20, plate), B.box(30, -1, web, 34, 21, plate + 1))


class TestLivingHinge(RuleTest):
    rule = R.MAX_LIVING_HINGE_WEB

    def test_thin_web_passes(self):
        self.none(self.rule, hinge(0.4), material="PP")

    def test_warning_and_error(self):
        _pf, f = self.one(self.rule, hinge(0.5), Severity.WARNING, 0.5, "living_hinge", material="PP")
        self.assertIn("0.50 mm thick", f.message)
        self.assertEqual(f.unit, "mm")
        self.assertTrue(f.overview.endswith("0.50 mm > 0.40 mm"), f.overview)
        # Webs over 0.6 mm are only recognised as hinges with the recognition
        # preference "Thickest living hinge web" raised.
        shape = hinge(0.8, plate=3.0)
        self.assertEqual(S.part_features(shape).features(FeatureKind.LIVING_HINGE), ())
        pf = S.part_features(shape, prefs={"RecognitionMaxHingeThickness": 1.0})
        found = S.run_rule(self.rule, pf, shape, material="PP")
        self.assertEqual([f.severity for f in found], [Severity.ERROR])
        self.assertTrue(found[0].overview.endswith("0.80 mm > 0.60 mm"), found[0].overview)
        # The recogniser's own hinge fixture, against a stricter target.
        self.one(self.rule, FX.by_name("living_hinge_flat").build(), Severity.WARNING, 0.4, "living_hinge",
                 material="PP", target=0.3)

    def test_too_thin_to_fill(self):
        _pf, f = self.one(self.rule, hinge(0.15), Severity.WARNING, 0.15, "living_hinge", material="PP")
        self.assertIn("may not fill", f.overview)

    def test_material_other_than_pp_or_pe(self):
        _pf, f = self.one(self.rule, hinge(0.4), Severity.WARNING, 0.4, "living_hinge", material="ABS")
        self.assertIn("ABS", f.message)
        self.assertIn("polypropylene", f.message)
        self.none(self.rule, hinge(0.4), material="PE")
        self.none(self.rule, hinge(0.4))  # Default: material unknown
        # "PET / PBT" is not PE.
        _pf, found = self.findings(self.rule, hinge(0.4), material="PET / PBT")
        self.assertEqual(len(found), 1)


class TestSnapFitStrain(RuleTest):
    rule = R.MAX_SNAP_FIT_STRAIN

    def test_constant_beam(self):
        # 1.5 · 1.6 · 1.0 / 9.5² = 2.66 %: over the 2 % Default placeholder.
        _pf, f = self.one(self.rule, FX.by_name("snap_fit_cantilever").build(), Severity.ERROR, 2.66,
                          "snap_fit")
        self.assertIn("1.60 mm thick and 9.50 mm long", f.message)
        self.assertIn("Snap fit 1 (cantilever)", f.overview)

    def test_material_thresholds(self):
        # Polycarbonate takes 4 % once, about 2.4 % for frequent use.
        self.one(self.rule, FX.by_name("snap_fit_cantilever").build(), Severity.WARNING, 2.66, "snap_fit",
                 material="PC")

    def test_tapered_beam_strains_less(self):
        # 1.6 mm at the root, 1.1 at the tip (a ratio of 0.69): K = 0.93.
        _pf, f = self.one(self.rule, FX.by_name("snap_fit_drafted").build(), Severity.ERROR, 2.38, "snap_fit")
        self.assertIn("thick at the root", f.message)

    def test_root_strain(self):
        from freecad.DFM.core.checks.molding_detail_checks import root_strain
        from freecad.DFM.core.recognition import Feature

        def beam(h, tip):
            return Feature(1, FeatureKind.SNAP_FIT, "cantilever", (1,),
                           params={"thickness": h, "thickness_tip": tip, "length": 20.0, "overhang": 2.0})
        self.assertAlmostEqual(root_strain(beam(2.0, 2.0)), 100 * 2 * 2 / (0.67 * 400), places=6)
        self.assertAlmostEqual(root_strain(beam(2.0, 1.0)), 100 * 2 * 2 / (1.09 * 400), places=6)
        self.assertAlmostEqual(root_strain(beam(2.0, 0.5)), root_strain(beam(2.0, 1.0)), places=9)


# -- No nominal wall -------------------------------------------------------------------------


class TestNominalMissing(unittest.TestCase):
    def test_one_info_for_the_whole_run(self):
        shape = S.rib(2.5, 14, base=S.boss(6, 20, hole=3))  # thick wall, thick rib, tall boss
        pf = dataclasses.replace(S.part_features(shape), nominal_wall=None, nominal_source="none")
        active = [R.MAX_BOSS_WALL_RATIO, R.MAX_RIB_THICKNESS_RATIO, R.MAX_BOSS_HEIGHT_RATIO,
                  R.MAX_RIB_HEIGHT_RATIO]
        notes = set()
        found = []
        for rule in active:
            found += S.run_rule(rule, pf, shape, run_notes=notes, _active_rules=active)
        info = [f for f in found if f.severity == Severity.INFO]
        self.assertEqual(len(info), 1, [f.overview for f in found])
        self.assertEqual(info[0].rule_id, R.MAX_BOSS_WALL_RATIO)
        self.assertEqual(info[0].failing_geometry, [])
        for rule in (R.MAX_BOSS_WALL_RATIO, R.MAX_RIB_THICKNESS_RATIO, R.MAX_RIB_HEIGHT_RATIO):
            self.assertIn(rule.label, info[0].message)
        self.assertNotIn(R.MAX_BOSS_HEIGHT_RATIO.label, info[0].message)
        self.assertIn("Manual", info[0].message)
        # A rule that doesn't need the nominal wall still runs.
        self.assertTrue(any(f.rule_id == R.MAX_BOSS_HEIGHT_RATIO for f in found))


if __name__ == "__main__":
    unittest.main()
