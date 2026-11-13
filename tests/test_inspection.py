# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The Symmetry Check and Compare Bodies engine (core/inspection), on parts
built with OCP."""

import math
import unittest

import dfm_test_support  # noqa: F401  (FreeCAD stand-ins, sys.path)

from freecad.DFM.core.inspection import compare as C
from freecad.DFM.core.inspection import deviation as D
from freecad.DFM.core.inspection import geometry as G
from freecad.DFM.core.inspection import symmetry as SY
from freecad.DFM.core.inspection.models import ADDED, CHANGED, CUT, EXTRA, MISMATCH, REMOVED
from freecad.DFM.core.inspection.naming import fmt_mm, fmt_volume
from freecad.DFM.core.recognition import recognize
from freecad.DFM.core.recognition.testing import shapes as B


def plate(bosses=((-35, 18), (35, 18)), holes=((-35, -20, 4), (35, -20, 4)), extra=None, pocket=None, rib=None):
    """A 120 x 80 x 10 plate with screw bosses and through holes."""
    s = B.box(-60, -40, 0, 60, 40, 10)
    for x, y in bosses:
        s = B.fuse(s, B.cylinder((x, y, 10), 7, 16))
        s = B.cut(s, B.cylinder((x, y, 14), 3, 13))
    for x, y, r in holes:
        s = B.cut(s, B.cylinder((x, y, -1), r, 12))
    if extra:
        s = B.fuse(s, B.cylinder((extra[0], extra[1], 10), 5, 10))
    if pocket:
        s = B.cut(s, B.box(*pocket))
    if rib:
        s = B.fuse(s, B.box(*rib))
    return s


def flange(skip=None):
    s = B.cut(B.cylinder((0, 0, 0), 40, 8), B.cylinder((0, 0, -1), 12, 10))
    for k in range(6):
        if k != skip:
            a = math.radians(60 * k)
            s = B.cut(s, B.cylinder((30 * math.cos(a), 30 * math.sin(a), -1), 4, 10))
    return s


def asymmetric():
    return plate(holes=((-35, -20, 4), (36.5, -20, 4)), extra=(20, -22), pocket=(-23, 13, 5, -7, 23, 11))


class TestGeometry(unittest.TestCase):
    def test_a_difference_knows_which_faces_it_came_from(self):
        s = plate(extra=(20, -22))
        moved = G.transformed(s, G.mirror((0, 0, 0), (1, 0, 0)))
        regions = G.difference(s, moved.shape, 0.01)
        self.assertEqual(len(regions), 1)
        r = regions[0]
        self.assertAlmostEqual(r.volume, math.pi * 25 * 10, places=3)
        # Its side and top are the part's own faces; its bottom is the mirror's top face.
        self.assertEqual(len(r.area_from(G.OWN)), 2)
        self.assertEqual(len(r.area_from(G.TOOL)), 1)

    def test_a_transformed_copy_keeps_its_faces_in_order(self):
        s = plate()
        moved = G.transformed(s, G.rotation((0, 0, 0), (0, 0, 1), 90))
        self.assertEqual(len(moved.face_map), len(G.faces(s)))

    def test_sample_points_reach_the_middle_of_flat_faces(self):
        pts = G.sample_points(B.box(0, 0, 0, 10, 10, 10))
        self.assertTrue(any(abs(p[0] - 5) < 1 and abs(p[1] - 5) < 1 and p[2] == 10 for p in pts))


class TestSymmetry(unittest.TestCase):
    def test_a_symmetric_part_is_symmetric(self):
        res = SY.check(plate(), SY.SymmetrySpec())
        self.assertTrue(res.symmetric)
        self.assertEqual(res.differences, [])
        self.assertAlmostEqual(res.matched_fraction, 1.0)
        self.assertEqual(SY.plane_name(res.direction), "YZ")
        self.assertIn("Auto chose YZ", res.note)

    def test_what_is_not_symmetric_is_found_and_named(self):
        s = asymmetric()
        res = SY.check(s, SY.SymmetrySpec(direction=(1, 0, 0)), recognize(s))
        self.assertFalse(res.symmetric)
        by_kind = {d.kind: d for d in res.differences}
        self.assertEqual(set(by_kind), {EXTRA, CUT, MISMATCH})
        boss, pocket, hole = by_kind[EXTRA], by_kind[CUT], by_kind[MISMATCH]
        self.assertEqual((boss.name, boss.detail), ("Boss Ø10", "only on the +X side"))
        self.assertAlmostEqual(boss.deviation, 10.0, places=2)
        self.assertTrue(boss.solids and boss.ghosts)
        self.assertEqual((pocket.name, pocket.detail), ("Pocket 16 × 10", "only on the −X side"))
        self.assertAlmostEqual(pocket.volume, 800.0, places=1)
        self.assertEqual(hole.name, "Hole Ø8")
        self.assertRegex(hole.detail, r"^1\.(49|5) mm off its partner$")
        # One difference for the shifted hole, though it leaves a sliver on each side.
        self.assertEqual(len(hole.ghosts), 2)
        self.assertAlmostEqual(res.matched_fraction, 0.982, places=3)
        # Numbered largest first.
        self.assertEqual([d.number for d in res.differences], [1, 2, 3])
        self.assertGreaterEqual(abs(res.differences[0].volume), abs(res.differences[-1].volume))

    def test_best_fit_places_the_plane_where_the_part_matches_best(self):
        # The extra boss pulls the centre of mass sideways; the plane stays at X = 0.
        s = plate(extra=(20, -22))
        self.assertGreater(abs(G.centre(s)[0]), 0.1)
        res = SY.check(s, SY.SymmetrySpec(direction=(1, 0, 0)))
        self.assertLess(abs(res.origin[0]), 0.01)
        self.assertEqual(len(res.differences), 1)
        com = SY.check(s, SY.SymmetrySpec(direction=(1, 0, 0), through=SY.CENTRE))
        self.assertGreater(len(com.differences), 1)  # everything looks a little off

    def test_offset_moves_the_plane(self):
        res = SY.check(plate(), SY.SymmetrySpec(direction=(1, 0, 0), through=SY.ORIGIN, offset=2.0))
        self.assertAlmostEqual(res.origin[0], 2.0)
        self.assertFalse(res.symmetric)

    def test_midplane_of_two_faces(self):
        # The plate's end faces, at X = -60 and X = +60, facing opposite ways.
        point, normal = SY.midplane(((-60, 5, 5), (-1, 0, 0)), ((60, -3, 2), (1, 0, 0)))
        self.assertAlmostEqual(G.vdot(point, normal), 0.0)
        self.assertEqual(SY.plane_name(normal), "YZ")
        res = SY.check(plate(), SY.SymmetrySpec(direction=normal, through=SY.POINT, point=point))
        self.assertTrue(res.symmetric)
        # Off-centre faces give an off-centre plane.
        point, _n = SY.midplane(((-60, 0, 0), (1, 0, 0)), ((20, 0, 0), (1, 0, 0)))
        self.assertAlmostEqual(point[0], -20.0)
        # No plane between faces that aren't parallel, or that are coplanar.
        self.assertIsNone(SY.midplane(((0, 0, 0), (1, 0, 0)), ((5, 0, 0), (0, 1, 0))))
        self.assertIsNone(SY.midplane(((0, 0, 0), (1, 0, 0)), ((0, 9, 3), (-1, 0, 0))))

    def test_auto_finds_the_plane_the_part_is_symmetric_about(self):
        # Symmetric about XZ only: one boss row, offset in Y.
        s = plate(bosses=((-35, 18), (35, 18)), holes=())
        res = SY.check(s, SY.SymmetrySpec())
        self.assertEqual(SY.plane_name(res.direction), "YZ")
        self.assertTrue(res.symmetric)

    def test_ignored_no_longer_counts(self):
        s = plate(extra=(20, -22))
        first = SY.check(s, SY.SymmetrySpec(direction=(1, 0, 0)))
        key = first.differences[0].key
        again = SY.check(s, SY.SymmetrySpec(direction=(1, 0, 0)), ignored=[key])
        self.assertTrue(again.differences[0].ignored)
        self.assertTrue(again.symmetric)

    def test_rotational_symmetry(self):
        ok = SY.check(flange(), SY.SymmetrySpec(kind=SY.ROTATIONAL, copies=6))
        self.assertTrue(ok.symmetric)
        self.assertEqual(SY.axis_letter(ok.direction), "Z")
        s = flange(skip=2)
        bad = SY.check(s, SY.SymmetrySpec(kind=SY.ROTATIONAL, copies=6), recognize(s))
        self.assertEqual(len(bad.differences), 1)
        self.assertEqual(bad.differences[0].name, "Hole Ø8")
        self.assertEqual(bad.differences[0].detail, "no matching copy 60° round")

    def test_asymmetry_map(self):
        s = plate(extra=(20, -22))
        res = SY.check(s, SY.SymmetrySpec(direction=(1, 0, 0)))
        mesh = G.triangulate(s, 0.3)
        pts = D.centroids(mesh.vertices, mesh.triangles)
        vals = D.asymmetry_values(pts, SY.partner_shape(s, res), [d.box for d in res.differences], 0.01)
        finite = [v for v in vals if math.isfinite(v)]
        self.assertTrue(finite)
        self.assertAlmostEqual(max(finite), 10.0, places=1)
        # Far from the boss everything is symmetric: no value.
        far = [v for p, v in zip(pts, vals) if p[0] < -40]
        self.assertTrue(all(math.isinf(v) for v in far))


class TestCompare(unittest.TestCase):
    def setUp(self):
        self.a = plate()
        self.b = plate(bosses=((-35, 18),), holes=((-35, -20, 4), (35, -20, 5)), rib=(-12, -7, 10, 18, -4, 22))

    def test_identical(self):
        res = C.compare(self.a, plate())
        self.assertTrue(res.identical)
        self.assertEqual(res.added, 0.0)

    def test_added_removed_and_changed(self):
        res = C.compare(self.a, self.b, 0.01, C.PLACED, recognize(self.a), recognize(self.b))
        rows = [(d.kind, d.name, d.detail) for d in res.differences]
        self.assertEqual(rows[0], (ADDED, "Rib", "30 × 3 × 12 mm"))
        self.assertEqual(rows[1][:2], (REMOVED, "Boss Ø14"))
        self.assertEqual(rows[2], (CHANGED, "Hole Ø8 → Ø10", "made larger"))
        self.assertAlmostEqual(res.added, 1080.0, places=1)
        self.assertAlmostEqual(res.net, res.b.volume - res.a.volume, places=6)
        self.assertEqual(res.a.faces, len(G.faces(self.a)))
        self.assertTrue(res.differences[0].solids and not res.differences[0].ghosts)
        self.assertTrue(res.differences[1].ghosts and not res.differences[1].solids)

    def test_best_fit_lines_up_a_moved_copy(self):
        moved = G.transformed(self.a, G.rigid([[0.9961947, -0.0871557, 0, 3], [0.0871557, 0.9961947, 0, -2],
                                               [0, 0, 1, 1]])).shape
        self.assertFalse(C.compare(self.a, moved).identical)
        fit = C.compare(self.a, moved, 0.01, C.BEST_FIT)
        self.assertTrue(fit.identical)
        self.assertLess(fit.fit_rms, 0.05)

    def test_best_fit_is_not_pulled_off_by_the_changes(self):
        res = C.compare(self.a, self.b, 0.01, C.BEST_FIT)
        self.assertEqual(len(res.differences), 3)
        self.assertAlmostEqual(res.added, 1080.0, places=0)

    def test_deviation_map_signs(self):
        res = C.compare(self.a, self.b)
        mesh = G.triangulate(self.b, 0.3)
        pts = D.centroids(mesh.vertices, mesh.triangles)
        vals = D.deviation_values(pts, C.reference_in_b(self.a, res), [d.box for d in res.differences], 0.01)
        finite = [v for v in vals if math.isfinite(v)]
        self.assertAlmostEqual(max(finite), 12.0, places=1)    # the rib's top: added
        # The enlarged hole's wall is 1 mm inside A: removed.
        self.assertLess(min(finite), -0.9)


class TestFormatting(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(fmt_mm(8.0), "8")
        self.assertEqual(fmt_mm(7.5), "7.5")
        self.assertEqual(fmt_volume(785.4), "0.79 cm³")
        self.assertEqual(fmt_volume(-2123.7, signed=True), "−2.12 cm³")


if __name__ == "__main__":
    unittest.main()
