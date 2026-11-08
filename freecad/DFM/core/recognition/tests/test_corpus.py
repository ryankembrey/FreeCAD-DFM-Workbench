# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The approved corpus (tests/recognition/corpus): real parts reviewed in
the bench. Every approved case must still pass, and the user's parts are
checked for what was wrong with them before."""

import os
import unittest

from freecad.DFM.core.recognition import FeatureKind as K, recognize
from freecad.DFM.core.recognition.testing import corpus
from freecad.DFM.core.recognition.testing import shapes as B

DIR = corpus.default_corpus_dir()


def _case(name):
    path = os.path.join(DIR, name + ".brep")
    if not os.path.exists(path):
        raise unittest.SkipTest(f"{name} not in the corpus")
    return B.read_brep(path)


class TestCorpus(unittest.TestCase):
    def test_every_approved_case(self):
        if not os.path.isdir(DIR):
            self.skipTest("no corpus")
        for path in corpus.list_goldens(DIR):
            with self.subTest(case=os.path.basename(path)):
                rep = corpus.check_golden(path)
                self.assertTrue(rep.passed, "\n".join(rep.failures))


class TestAntennaHolder(unittest.TestCase):
    """A moulded holder: a round cradle for an antenna rod cut across four
    walls (two of them ribs), four holes counterbored from both ends with
    lead-in chamfers, two pockets, a chamfered rim."""

    @classmethod
    def setUpClass(cls):
        cls.r = recognize(_case("antenna_holder"))

    def kinds(self, kind):
        return self.r.of_kind(kind)

    def test_cradle_is_not_a_hole(self):
        for h in self.kinds(K.HOLE):
            self.assertLess(h.params["diameter"], 10, h.label)
        saddles = [s for s in self.kinds(K.SLOT) if s.subtype == "saddle"]
        self.assertEqual(len(saddles), 1)
        self.assertAlmostEqual(saddles[0].params["diameter"], 13.4, places=2)
        self.assertEqual(saddles[0].params["rib_tips"], 2)

    def test_ribs_have_curved_tips_and_not_the_rim(self):
        ribs = self.kinds(K.RIB)
        self.assertEqual(len(ribs), 4)
        sig = self.r.face_signatures
        for rib in ribs:
            for face in rib.faces:   # the rim top (z = 9.2) is what they are flush with
                self.assertFalse(sig[face].kind == "plane" and abs(sig[face].centroid[2] - 9.2) < 1e-3, rib.label)
        curved = [r for r in ribs if any(sig[f].kind == "cylinder" for f in r.faces)]
        self.assertEqual(len(curved), 2)

    def test_holes_counterbored_both_ends_with_lead_in(self):
        holes = self.kinds(K.HOLE)
        self.assertEqual(len(holes), 4)
        for h in holes:
            self.assertEqual(h.subtype, "counterbored", h.label)
            self.assertIn("cb2_diameter", h.params)
            self.assertNotIn("cs_diameter", h.params)
        lead_ins = [c for c in self.kinds(K.CHAMFER) if c.params["width"] < 0.4]
        self.assertEqual(len(lead_ins), 4)

    def test_pockets_own_their_walls(self):
        pockets = self.kinds(K.POCKET)
        self.assertEqual(len(pockets), 2)
        for p in pockets:
            self.assertEqual(len(p.faces), 5, p.label)   # floor + four flat walls

    def test_chamfer_runs_over_the_cradle(self):
        rim = max(self.kinds(K.CHAMFER), key=lambda c: len(c.faces))
        self.assertTrue(any(self.r.face_signatures[f].kind == "bspline" for f in rim.faces))

    def test_form(self):
        self.assertEqual(self.r.form["form"], "mixed")
        self.assertEqual(tuple(round(c) for c in self.r.form["direction"]), (0, 0, 1))
        self.assertEqual(len(self.r.form["other_faces"]), 4)   # the cradle, across the walls


class TestForm(unittest.TestCase):
    def test_extrusion_25d_revolve(self):
        from freecad.DFM.core.recognition.testing import fixtures as F
        self.assertEqual(recognize(F.by_name("hole_through_simple").build()).form["form"], "extrusion")
        self.assertEqual(recognize(F.by_name("boss_stepped").build()).form["form"], "2.5d")
        shaft = B.fuse(B.cylinder((0, 0, 0), 10, 20), B.cylinder((0, 0, 20), 6, 15))
        f = recognize(shaft).form
        self.assertEqual(f["form"], "revolve")


if __name__ == "__main__":
    unittest.main()
