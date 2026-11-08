# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""FEATURE_ANALYZER: one PartFeatures per part, cached, abortable."""

import pickle
import unittest

import dfm_test_support as S
from freecad.DFM.core.models import ProcessRequirement
from freecad.DFM.core.recognition import FeatureKind
from freecad.DFM.core.registries.analyzers_registry import get_analyzer_class
from freecad.DFM.core.utils.geometry import EdgeIndex, FaceIndex
from freecad.DFM.core.utils.part_features import FEATURE_CACHE, PartFeatures


class TestFeatureAnalyzer(unittest.TestCase):
    def setUp(self):
        FEATURE_CACHE.clear()
        self.shape = S.boss(8, 15, hole=2.5)

    def test_registered_and_shape_level(self):
        cls = get_analyzer_class("FEATURE_ANALYZER")
        self.assertIsNotNone(cls)
        self.assertFalse(cls.parallel)
        self.assertEqual(cls().iteration_count(self.shape), 100)
        self.assertIn(ProcessRequirement.NOMINAL_WALL_THICKNESS, cls().requirements)
        self.assertIn(ProcessRequirement.PULL_DIRECTION, cls().requirements)

    def test_returns_part_features(self):
        steps = []
        cls = get_analyzer_class("FEATURE_ANALYZER")
        pf = cls().execute(self.shape, FaceIndex(self.shape), EdgeIndex(self.shape),
                           progress_cb=steps.append, prefs={},
                           NOMINAL_WALL_THICKNESS={"mode": "auto"})
        self.assertIsInstance(pf, PartFeatures)
        self.assertEqual(pf.nominal_source, "measured")
        self.assertAlmostEqual(pf.nominal_wall, 2.5, delta=0.05)
        self.assertEqual(pf.nominal_text, "2.50 mm (measured)")
        self.assertEqual(len(pf.features(FeatureKind.BOSS)), 1)
        self.assertEqual(steps[-1], 100)
        self.assertEqual(steps, sorted(steps))
        # Plain data: it pickles (kept with a run, checked again for another material).
        again = pickle.loads(pickle.dumps(pf))
        self.assertEqual(again.nominal_wall, pf.nominal_wall)
        self.assertEqual(len(again.result.features), len(pf.result.features))

    def test_manual_nominal_and_directional(self):
        pf = S.part_features(self.shape, nominal=3.0)
        self.assertEqual((pf.nominal_wall, pf.nominal_source), (3.0, "manual"))
        self.assertEqual(pf.nominal_text, "3.00 mm (entered)")
        boss = pf.features(FeatureKind.BOSS)[0]
        self.assertTrue(pf.annotation(boss.id).get("along_pull"))

    def test_second_run_hits_the_cache(self):
        S.part_features(self.shape)
        hits = FEATURE_CACHE.hits
        S.part_features(self.shape)
        self.assertEqual(FEATURE_CACHE.hits, hits + 1)
        self.assertEqual(len(FEATURE_CACHE), 1)

    def test_hints_only_change_the_key_when_used(self):
        S.part_features(self.shape, prefs={"RecognitionUseHistoryHints": False}, hints=["ignored"])
        S.part_features(self.shape, prefs={"RecognitionUseHistoryHints": False})
        self.assertEqual(len(FEATURE_CACHE), 1)

    def test_abort_returns_cleanly(self):
        cls = get_analyzer_class("FEATURE_ANALYZER")
        pf = cls().execute(self.shape, FaceIndex(self.shape), EdgeIndex(self.shape),
                           check_abort=lambda: True, prefs={})
        self.assertIsInstance(pf, PartFeatures)
        self.assertEqual(pf.result.features, ())
        self.assertIsNone(pf.nominal_wall)
        self.assertEqual(len(FEATURE_CACHE), 0)  # nothing half-done is kept


if __name__ == "__main__":
    unittest.main()
