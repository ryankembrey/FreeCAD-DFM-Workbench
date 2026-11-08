# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Unit tests of the engine's parts: topology, the graph, the model, the
cache, options, hints, directional annotations and the corpus.

Run in plain Python (cadquery-ocp only)::

    python -m unittest discover -s freecad/DFM/core/recognition/tests -t .
"""

import json
import math
import os
import pickle
import shutil
import tempfile
import unittest

from OCP.BRepOffset import BRepOffset_Analyse
from OCP.ChFiDS import ChFiDS_TypeOfConcavity as C

from freecad.DFM.core.recognition import (
    ENGINE_VERSION, FeatureCache, FeatureKind, Hint, RecognitionOptions, RecognitionResult,
    annotate_directional, fingerprint, options_from_prefs, pref_key, recognize,
)
from freecad.DFM.core.recognition._graph import Pattern, match
from freecad.DFM.core.recognition.api import build_graph
from freecad.DFM.core.recognition.testing import fixtures as FX
from freecad.DFM.core.recognition.testing import shapes as B
from freecad.DFM.core.utils.geometry import EdgeIndex, FaceIndex


def _plate_with_hole():
    return B.cut(B.box(0, 0, 0, 100, 60, 10), B.drill((30, 30, 10), (0, 0, -1), 6, 10))


class TestTopology(unittest.TestCase):
    ORACLE = {C.ChFiDS_Convex: "convex", C.ChFiDS_Concave: "concave", C.ChFiDS_Tangential: "smooth"}

    def _check_oracle(self, shape):
        g = build_graph(shape)
        an = BRepOffset_Analyse(shape, math.radians(RecognitionOptions().smooth_angle))
        checked = 0
        for e in g.topo.iter_edges():
            if not e.is_manifold:
                continue
            types = list(an.Type(e.edge))
            if not types:
                continue
            want = self.ORACLE.get(types[0].Type())
            if want is None:
                continue
            self.assertEqual(e.convexity, want, f"edge {e.index} {e.faces}: {e.samples}")
            checked += 1
        return checked

    def test_convexity_matches_occt_oracle_on_fixtures(self):
        total = 0
        for name in ("hole_counterbore_blind", "boss_root_fillet", "rib_root_fillets", "pocket_floor_fillets",
                     "housing_shelled", "box_all_edges_rounded", "l_bracket_inner_chamfer", "bracket_with_gussets"):
            with self.subTest(name=name):
                shape = FX.by_name(name).build()
                total += self._check_oracle(shape)
                total += self._check_oracle(B.nurbs(shape))
        self.assertGreater(total, 300)

    def test_face_numbers_are_faceindex_numbers(self):
        shape = FX.by_name("housing_shelled").build()
        g = build_graph(shape)
        fi = FaceIndex(shape)
        ei = EdgeIndex(shape)
        self.assertEqual(g.topo.face_count, len(fi))
        self.assertEqual(g.topo.edge_count, len(ei))
        for info in g.topo.iter_faces():
            self.assertTrue(info.face.IsSame(fi.face_at(info.index)))
        for e in g.topo.iter_edges():
            self.assertTrue(e.edge.IsSame(ei.edge_at(e.index)))

    def test_internal_and_external_cylinders(self):
        g = build_graph(FX.by_name("boss_screw").build())
        kinds = {round(i.radius, 3): i.internal for i in g.topo.iter_faces() if i.kind == "cylinder"}
        self.assertIs(kinds[4.0], False)    # the boss wall
        self.assertIs(kinds[1.25], True)    # the screw hole

    def test_spans(self):
        g = build_graph(FX.by_name("hole_breaks_out_of_edge").build())
        cyl = next(i for i in g.topo.iter_faces() if i.kind == "cylinder")
        expected = 360 - 2 * math.degrees(math.acos(2 / 3))
        self.assertAlmostEqual(cyl.span, expected, delta=8.0)

    def test_bspline_faces_are_recognised_as_canonical(self):
        g = build_graph(B.nurbs(_plate_with_hole()))
        kinds = sorted(i.kind for i in g.topo.iter_faces())
        self.assertEqual(kinds, ["cylinder"] + ["plane"] * 6)
        self.assertTrue(all(i.native_kind == "bspline" for i in g.topo.iter_faces()))


class TestGraph(unittest.TestCase):
    def test_blend_suppression_restores_sharp_graph(self):
        """A filleted box's AAG with its fillets hidden is the sharp box's."""
        sharp = build_graph(B.box(0, 0, 0, 40, 30, 20))
        result_shape = FX.by_name("box_all_edges_rounded").build()
        from freecad.DFM.core.recognition._recognizers import blends
        from freecad.DFM.core.recognition._recognizers.common import Context
        g = build_graph(result_shape)
        ctx = Context(g.topo, g, RecognitionOptions(), True)
        accepted = [c for c in blends.find_blends(ctx) if ctx.accept(c)]
        blends.suppress(ctx, accepted)
        planes = [f for f in g.faces]
        self.assertEqual(len(planes), 6)
        for f in planes:
            arcs = g.arcs(f)
            self.assertEqual(len({a.other(f) for a in arcs}), 4)
            self.assertTrue(all(a.convexity == "convex" and a.virtual for a in arcs))
        self.assertEqual(sum(len(sharp.arcs(f)) for f in sharp.faces), sum(len(g.arcs(f)) for f in planes))

    def test_pattern_matching_finds_slot_walls(self):
        g = build_graph(FX.by_name("slot_open_both_ends").build())
        plane = lambda gr, f: gr.info(f).kind == "plane"
        pat = Pattern("u-channel").node("wall1", plane).node("floor", plane).node("wall2", plane)
        pat.arc("wall1", "floor", "concave").arc("floor", "wall2", "concave")
        pat.not_adjacent.append(("wall1", "wall2"))
        found = match(g, pat)
        self.assertEqual(len(found), 2)  # wall1/wall2 either way round
        self.assertEqual({m["floor"] for m in found}, {found[0]["floor"]})

    def test_graph_serialises(self):
        g = build_graph(FX.by_name("rib_cross").build())
        d = g.to_dict()
        json.dumps(d)
        self.assertEqual(len(d["nodes"]), g.topo.face_count)


class TestModel(unittest.TestCase):
    def setUp(self):
        self.shape = FX.by_name("housing_shelled").build()
        self.result = recognize(self.shape, strict=True)

    def test_json_and_pickle_round_trip(self):
        d = self.result.to_dict()
        again = RecognitionResult.from_dict(json.loads(json.dumps(d)))
        self.assertEqual(again.to_dict(), d)
        un = pickle.loads(pickle.dumps(self.result))
        self.assertEqual(un.to_dict(), d)
        self.assertEqual(un.owner_of(self.result.features[0].faces[0]).id, self.result.features[0].id)

    def test_each_face_owned_at_most_once(self):
        seen = {}
        for f in self.result.features:
            for face in f.faces:
                self.assertNotIn(face, seen, f"face {face} in {f.label} and feature {seen.get(face)}")
                seen[face] = f.id

    def test_queries(self):
        bosses = self.result.of_kind(FeatureKind.BOSS)
        self.assertEqual(len(bosses), 4)
        b = bosses[0]
        self.assertEqual(self.result.owner_of(b.faces[0]), b)
        self.assertIn(b, self.result.on_face(b.faces[0]))
        self.assertEqual(b.face_labels[0], f"Face{b.faces[0]}")
        self.assertTrue(all(1 <= f <= self.result.face_count for f in self.result.unowned_faces()))
        holes = [h for h in self.result.of_kind(FeatureKind.HOLE) if h.parent is not None]
        self.assertEqual(len(holes), 4)
        self.assertTrue(all(self.result.by_id(h.parent).kind == FeatureKind.BOSS for h in holes))

    def test_deterministic(self):
        again = recognize(self.shape, strict=True)
        a, b = self.result.to_dict(), again.to_dict()
        a.pop("timings"), b.pop("timings")
        self.assertEqual(a, b)

    def test_ids_and_order(self):
        ids = [f.id for f in self.result.features]
        self.assertEqual(ids, list(range(1, len(ids) + 1)))
        for f in self.result.features:
            for i in f.interacts_with:
                self.assertIsNotNone(self.result.by_id(i))

    def test_confidence_and_evidence(self):
        for f in self.result.features:
            self.assertGreaterEqual(f.confidence, 0.0)
            self.assertLessEqual(f.confidence, 1.0)
            self.assertTrue(f.evidence, f.label)


class TestApi(unittest.TestCase):
    def test_null_shape(self):
        from OCP.TopoDS import TopoDS_Shape
        with self.assertRaises(ValueError):
            recognize(TopoDS_Shape())

    def test_abort(self):
        with self.assertRaises(InterruptedError):
            recognize(_plate_with_hole(), check_abort=lambda: True)

    def test_progress_stages(self):
        stages = []
        recognize(_plate_with_hole(), progress=lambda name, frac: stages.append((name, frac)))
        names = [s for s, _ in stages]
        self.assertEqual(names[0], "topology")
        self.assertEqual(names[-1], "done")
        fracs = [f for _, f in stages]
        self.assertEqual(fracs, sorted(fracs))

    def test_disabled_recognizers(self):
        r = recognize(FX.by_name("boss_screw").build(), RecognitionOptions(disabled=("holes",)))
        self.assertFalse(r.of_kind(FeatureKind.HOLE))
        self.assertTrue(r.of_kind(FeatureKind.BOSS))

    def test_thresholds_change_results(self):
        shape = FX.by_name("rib_single").build()
        self.assertTrue(recognize(shape).of_kind(FeatureKind.RIB))
        self.assertFalse(recognize(shape, RecognitionOptions(max_rib_thickness=1.0)).of_kind(FeatureKind.RIB))
        self.assertFalse(recognize(shape, RecognitionOptions(min_rib_aspect=20.0)).of_kind(FeatureKind.RIB))

    def test_compound_of_two_solids(self):
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound
        comp = TopoDS_Compound()
        bld = BRep_Builder()
        bld.MakeCompound(comp)
        bld.Add(comp, _plate_with_hole())
        bld.Add(comp, B.moved(FX.by_name("boss_plain").build(), (0, 0, 100)))
        r = recognize(comp, strict=True)
        self.assertEqual(len(r.of_kind(FeatureKind.HOLE)), 1)
        self.assertEqual(len(r.of_kind(FeatureKind.BOSS)), 1)


class TestCache(unittest.TestCase):
    def test_fingerprint_stable_and_sensitive(self):
        a = _plate_with_hole()
        self.assertEqual(fingerprint(a), fingerprint(_plate_with_hole()))
        self.assertNotEqual(fingerprint(a), fingerprint(B.cut(B.box(0, 0, 0, 100, 60, 10),
                                                              B.drill((30, 30, 10), (0, 0, -1), 6.5, 10))))

    def test_cache_hits_and_keys(self):
        cache = FeatureCache(maxsize=2)
        s = _plate_with_hole()
        r1 = cache.get(s)
        r2 = cache.get(_plate_with_hole())
        self.assertIs(r1, r2)
        self.assertEqual(cache.hits, 1)
        r3 = cache.get(s, RecognitionOptions(max_rib_thickness=3.0))
        self.assertIsNot(r1, r3)
        cache.get(B.box(0, 0, 0, 1, 1, 1))
        self.assertEqual(len(cache), 2)
        self.assertEqual(r1.engine_version, ENGINE_VERSION)


class TestOptions(unittest.TestCase):
    def test_round_trip_and_prefs(self):
        o = RecognitionOptions(max_rib_thickness=4.5, use_history_hints=False, edge_samples=7)
        self.assertEqual(RecognitionOptions.from_dict(o.to_dict()), o)
        prefs = {pref_key(k): v for k, v in o.to_dict().items() if k != "disabled"}
        self.assertEqual(options_from_prefs(prefs), o)
        self.assertEqual(pref_key("max_rib_thickness"), "RecognitionMaxRibThickness")

    def test_every_preference_field_has_metadata(self):
        for f in RecognitionOptions.preference_fields():
            self.assertTrue(f.metadata.get("label"), f.name)
            self.assertIn("group", f.metadata)

    def test_key_changes_with_values(self):
        self.assertNotEqual(RecognitionOptions().key(), RecognitionOptions(max_draft=10.0).key())


class TestHints(unittest.TestCase):
    def setUp(self):
        self.shape = _plate_with_hole()

    def test_confirming_hint_raises_confidence_and_adds_thread(self):
        base = recognize(self.shape).of_kind(FeatureKind.HOLE)[0]
        hint = Hint(kinds=(FeatureKind.HOLE,), points=((33.0, 30.0, 5.0),),
                    params={"threaded": True, "thread_size": "M6", "diameter": 6.0},
                    source="PartDesign::Hole 'Hole'")
        h = recognize(self.shape, hints=[hint]).of_kind(FeatureKind.HOLE)[0]
        self.assertGreater(h.confidence, base.confidence)
        self.assertTrue(h.params["threaded"])
        self.assertEqual(h.params["thread_size"], "M6")
        self.assertEqual(h.subtype, "threaded")
        self.assertTrue(any("PartDesign::Hole" in e for e in h.evidence))

    def test_disagreeing_hint_lowers_confidence(self):
        base = recognize(self.shape).of_kind(FeatureKind.HOLE)[0]
        hint = Hint(kinds=(FeatureKind.BOSS,), points=((33.0, 30.0, 5.0),), source="test")
        h = recognize(self.shape, hints=[hint]).of_kind(FeatureKind.HOLE)[0]
        self.assertLess(h.confidence, base.confidence)

    def test_hint_on_unrecognised_faces_makes_a_feature(self):
        shape = B.box(0, 0, 0, 40, 30, 20)
        hint = Hint(kinds=(FeatureKind.POCKET,), points=((20.0, 15.0, 20.0),), source="user")
        r = recognize(shape, hints=[hint])
        self.assertEqual(len(r.features), 1)
        self.assertEqual(r.features[0].kind, FeatureKind.POCKET)
        self.assertLess(r.features[0].confidence, 0.7)

    def test_hints_can_be_turned_off(self):
        hint = Hint(kinds=(FeatureKind.HOLE,), points=((33.0, 30.0, 5.0),), params={"threaded": True})
        h = recognize(self.shape, RecognitionOptions(use_history_hints=False), hints=[hint]).of_kind(FeatureKind.HOLE)[0]
        self.assertNotIn("threaded", h.params)

    def test_hint_off_the_shape_warns(self):
        hint = Hint(kinds=(FeatureKind.HOLE,), points=((500.0, 500.0, 500.0),))
        r = recognize(self.shape, hints=[hint])
        self.assertTrue(any("none of its points" in w for w in r.warnings))

    def test_hint_dict_round_trip(self):
        hint = Hint(kinds=(FeatureKind.HOLE, FeatureKind.POCKET), points=((1.0, 2.0, 3.0),), params={"a": 1},
                    subtype="x", source="s", weight=0.5)
        self.assertEqual(Hint.from_dict(hint.to_dict()), hint)


class TestDirectional(unittest.TestCase):
    def test_face_draft_and_feature_walls(self):
        shape = FX.by_name("boss_tapered").build()
        r = recognize(shape)
        rep = annotate_directional(r, shape, pull=(0, 0, 1))
        boss = r.of_kind(FeatureKind.BOSS)[0]
        self.assertAlmostEqual(rep.annotations[boss.id]["draft_min_deg"], 1.5, places=2)
        self.assertTrue(rep.annotations[boss.id]["along_pull"])
        self.assertFalse(rep.annotations[boss.id]["needs_draft"])
        top = [f for f, a in rep.face_draft.items() if abs(a - 90) < 1e-6]
        self.assertTrue(top)
        json.dumps(rep.to_dict())

    def test_vertical_walls_need_draft(self):
        shape = FX.by_name("rib_single").build()
        r = recognize(shape)
        rep = annotate_directional(r, shape, pull=(0, 0, 1))
        rib = r.of_kind(FeatureKind.RIB)[0]
        self.assertTrue(rep.annotations[rib.id]["needs_draft"])

    def test_side_action_releases_undercut(self):
        shape = FX.by_name("housing_shelled").build()
        r = recognize(shape)
        rep = annotate_directional(r, shape, pull=(0, 0, 1), side_actions=[(-1, 0, 0)])
        uc = [f for f in rep.features if f.kind == FeatureKind.UNDERCUT]
        self.assertEqual(len(uc), 1)
        self.assertEqual(uc[0].params.get("side_action"), 1)
        self.assertTrue(uc[0].params["released"])
        self.assertEqual(uc[0].params["feature_kind"], "hole")

    def test_wrong_shape_is_refused(self):
        r = recognize(_plate_with_hole())
        with self.assertRaises(ValueError):
            annotate_directional(r, B.box(0, 0, 0, 1, 1, 1), pull=(0, 0, 1))


def _random_part(rng):
    """A plate with random holes, bosses, ribs, pockets and side holes,
    overlapping each other as they fall."""
    s = B.box(0, 0, 0, 100, 80, 20)
    for _ in range(rng.randint(3, 9)):
        k, x, y = rng.random(), rng.uniform(5, 95), rng.uniform(5, 75)
        if k < 0.3:
            s = B.cut(s, B.drill((x, y, 20), (0, 0, -1), rng.uniform(2, 12), rng.uniform(3, 20)))
        elif k < 0.5:
            s = B.fuse(s, B.cylinder((x, y, 20), rng.uniform(2, 8), rng.uniform(3, 15)))
        elif k < 0.7:
            w, length, h = rng.uniform(1, 4), rng.uniform(10, 50), rng.uniform(5, 15)
            if rng.random() < 0.5:
                s = B.fuse(s, B.box(x, y, 20, min(x + length, 99), y + w, 20 + h))
            else:
                s = B.fuse(s, B.box(x, y, 20, x + w, min(y + length, 79), 20 + h))
        elif k < 0.85:
            s = B.cut(s, B.box(x, y, rng.uniform(5, 15), min(x + rng.uniform(5, 30), 99),
                               min(y + rng.uniform(5, 30), 79), 21))
        else:
            s = B.cut(s, B.drill((rng.choice([-1, 101]), y, rng.uniform(4, 16)), (1, 0, 0), rng.uniform(2, 6), 102))
    return s


class TestRobustness(unittest.TestCase):
    """Random overlapping features: no crash, no warning, no face owned
    twice, and the same answer twice."""

    def test_random_parts(self):
        import random
        rng = random.Random(20261107)
        done = 0
        for i in range(40):
            try:
                shape = _random_part(rng)
            except Exception:
                continue  # an OCP boolean refused the random input
            with self.subTest(part=i):
                r = recognize(shape, strict=True)
                self.assertFalse(r.warnings)
                seen = set()
                for f in r.features:
                    self.assertTrue(f.faces)
                    self.assertFalse(set(f.faces) & seen, f.label)
                    seen |= set(f.faces)
                    self.assertTrue(0.0 <= f.confidence <= 1.0)
                again = recognize(shape).to_dict()
                first = r.to_dict()
                first.pop("timings"), again.pop("timings")
                self.assertEqual(first, again)
                done += 1
        self.assertGreater(done, 30)


class TestCorpus(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dfm_corpus_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_golden_approve_check_and_regression(self):
        from freecad.DFM.core.recognition.testing import corpus
        shape = FX.by_name("boss_screw").build()
        r = recognize(shape)
        golden = corpus.make_golden("boss_screw", r, {f.id: "correct" for f in r.features}, status="approved",
                                    reviewer="test")
        path = corpus.save_golden(self.dir, "boss_screw", shape, golden)
        self.assertTrue(os.path.exists(path[: -len(corpus.GOLDEN_SUFFIX)] + ".step"))
        ok = corpus.check_golden(path)
        self.assertTrue(ok.passed, ok.failures)
        # A golden expecting a feature that isn't there is a regression
        golden["features"].append({"id": 99, "kind": "rib", "subtype": "single", "faces": [1, 2],
                                   "params": {}, "verdict": "correct"})
        with open(path, "w") as fh:
            json.dump(golden, fh)
        bad = corpus.check_golden(path)
        self.assertFalse(bad.passed)
        self.assertTrue(any("regression" in f for f in bad.failures))

    def test_unreviewed_golden_never_fails(self):
        from freecad.DFM.core.recognition.testing import corpus
        shape = FX.by_name("rib_single").build()
        g = corpus.make_golden("rib", recognize(shape))
        g["features"].append({"id": 0, "kind": "boss", "faces": [1], "verdict": "correct"})
        path = corpus.save_golden(self.dir, "rib", shape, g)
        self.assertTrue(corpus.check_golden(path).passed)

    def test_export_fixtures(self):
        from freecad.DFM.core.recognition.testing import corpus
        n = corpus.export_fixtures(self.dir, FX.select_cases(pattern="hole_through_simple"))
        self.assertEqual(n, 1)
        with open(os.path.join(self.dir, "hole_through_simple.expected.json")) as fh:
            truth = json.load(fh)
        self.assertEqual(truth["expect"][0]["kind"], "hole")
        shape = B.read_brep(os.path.join(self.dir, "hole_through_simple.brep"))
        again = B.read_step(os.path.join(self.dir, "hole_through_simple.step"))
        self.assertEqual(len(recognize(shape).of_kind(FeatureKind.HOLE)), 1)
        self.assertEqual(len(recognize(again).of_kind(FeatureKind.HOLE)), 1)


if __name__ == "__main__":
    unittest.main()
