# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The nominal wall: measured on built parts, resolved from the requirement,
and its row in the setup panel (offscreen Qt, in a fresh interpreter)."""

import os
import subprocess
import sys
import unittest

import dfm_test_support as S
from freecad.DFM.core.recognition import Feature, FeatureKind, RecognitionResult, recognize
from freecad.DFM.core.recognition.testing import fixtures as FX
from freecad.DFM.core.utils.geometry import FaceIndex
from freecad.DFM.core.utils.nominal_wall import (
    SOURCE_MANUAL,
    SOURCE_MEASURED,
    SOURCE_NONE,
    describe,
    make_requirement,
    measure_nominal_wall,
    parse_requirement,
    resolve_nominal_wall,
    weighted_median,
)

B = S.B


def measured(shape):
    return measure_nominal_wall(shape, FaceIndex(shape), recognize(shape))


class TestMeasure(unittest.TestCase):
    def test_shelled_box(self):
        self.assertAlmostEqual(measured(S.shelled_box(2.5)), 2.5, delta=0.05)

    def test_features_are_left_out(self):
        s = S.boss(8, 15, at=(20, 25), hole=2.5, hole_depth=12)
        s = B.fuse(s, B.cylinder((60, 25, 2.5), 3.5, 12), B.box(30, 10, 2.5, 50, 11.5, 14.5),
                   B.box(30, 38, 2.5, 50, 39.5, 10.5))
        self.assertAlmostEqual(measured(s), 2.5, delta=0.05)

    def test_drafted_tray(self):
        self.assertAlmostEqual(measured(FX.by_name("molded_tray_drafted").build()), 2.0, delta=0.05)

    def test_solid_block_gives_its_smallest_dimension(self):
        # No shell: the rays cross the whole block, and the area weighting
        # favours the largest faces, which are 10 mm apart.
        self.assertAlmostEqual(measured(B.box(0, 0, 0, 100, 60, 10)), 10.0, delta=0.05)

    def test_weighted_median(self):
        self.assertEqual(weighted_median([(1.0, 1.0), (2.0, 5.0), (9.0, 1.0)]), 2.0)
        self.assertEqual(weighted_median([(3.0, 1.0)]), 3.0)


class TestRequirement(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_requirement({"mode": "manual", "value": 2.5}), ("manual", 2.5))
        self.assertEqual(parse_requirement({"mode": "auto"}), ("auto", None))

    def test_missing_or_malformed_is_auto(self):
        for value in (None, "", 3, {}, {"mode": "manual"}, {"mode": "manual", "value": "x"},
                      {"mode": "manual", "value": 0}, {"mode": "manual", "value": -1}, {"mode": "?"}):
            self.assertEqual(parse_requirement(value), ("auto", None), value)

    def test_make(self):
        self.assertEqual(make_requirement(), {"mode": "auto"})
        self.assertEqual(make_requirement("manual", 2), {"mode": "manual", "value": 2.0})

    def test_manual_is_used_without_measuring(self):
        s = S.shelled_box(2.5)  # Auto would give 2.5
        value = resolve_nominal_wall(s, FaceIndex(s), recognize(s), make_requirement("manual", 3.2))
        self.assertEqual(value, (3.2, SOURCE_MANUAL))

    def test_auto_measures(self):
        s = S.shelled_box(2.0)
        value, source = resolve_nominal_wall(s, FaceIndex(s), recognize(s), None)
        self.assertEqual(source, SOURCE_MEASURED)
        self.assertAlmostEqual(value, 2.0, delta=0.05)

    def test_auto_without_walls_gives_none(self):
        # Every face belongs to a feature that isn't a wall: nothing to measure.
        s = B.box(0, 0, 0, 20, 20, 20)
        found = recognize(s)
        no_walls = RecognitionResult(
            features=(Feature(1, FeatureKind.FILLET, "convex", tuple(range(1, found.face_count + 1))),),
            face_count=found.face_count, edge_count=found.edge_count, fingerprint=found.fingerprint,
            engine_version=found.engine_version, options={})
        self.assertEqual(resolve_nominal_wall(s, FaceIndex(s), no_walls, {"mode": "auto"}),
                         (None, SOURCE_NONE))

    def test_wrong_shape_gives_none(self):
        s = S.shelled_box()
        self.assertIsNone(measure_nominal_wall(s, FaceIndex(s), recognize(B.box(0, 0, 0, 1, 1, 1))))

    def test_describe(self):
        self.assertEqual(describe(2.481, SOURCE_MEASURED), "2.48 mm (measured)")
        self.assertEqual(describe(2.5, SOURCE_MANUAL), "2.50 mm (entered)")
        self.assertEqual(describe(None, SOURCE_NONE), "not measured")


class TestSetupRow(unittest.TestCase):
    def test_nominal_wall_row(self):
        try:
            import PySide6  # noqa: F401
        except ImportError:
            self.skipTest("PySide6 not installed")
        env = dict(os.environ)
        env["PYTHONPATH"] = S.ROOT + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        script = os.path.join(S.ROOT, "tests", "recognition_support", "nominal_wall_script.py")
        proc = subprocess.run([sys.executable, script], env=env, cwd=S.ROOT, capture_output=True,
                              text=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
