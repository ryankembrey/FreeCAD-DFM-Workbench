# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Additive manufacturing, batch 3: the resin and powder bed processes and
the rules they brought (also switched on for FDM where they apply).

* plumbing: the archetypes, which inputs each process asks for, the FDM
  text stroke from the nozzle, built-in processes by name;
* the cupping annotation of the recognition engine;
* each rule's levels and messages, with each process's own thresholds and
  wording: sealed hollows, escape holes, cupping, deep holes, gaps, pin
  slenderness and root fillets, text strokes, flat faces, long thin parts,
  bed edges.
"""

import math
import os
import tempfile
import unittest

import yaml

import dfm_test_support as S
from freecad.DFM.core.models import ProcessRequirement as Req, Severity
from freecad.DFM.core.processes.process import Process, ProcessArchetype as A, RuleLimit
from freecad.DFM.core.recognition import FeatureKind
from freecad.DFM.core.recognition.testing import fixtures as FX
from freecad.DFM.core.rules import Rulebook as R

B = S.B
PROCESSES = os.path.join(S.ROOT, "freecad", "DFM", "core", "processes")
FILES = {"fdm": "fdm_3d_printing.yaml", "resin": "resin_3d_printing.yaml", "powder": "powder_bed_fusion.yaml"}
_LOADED = {}


def process(name):
    if name not in _LOADED:
        with open(os.path.join(PROCESSES, FILES[name]), encoding="utf-8") as f:
            _LOADED[name] = Process.from_yaml(yaml.safe_load(f))
    return _LOADED[name]


def measure(rule, shape, direction=(0, 0, 1)):
    """The data of *rule*'s analyzer on *shape* (printed along *direction*, or
    with no print direction for None)."""
    from OCP.gp import gp_Dir

    import freecad.DFM.core.analyzers  # noqa: F401
    import freecad.DFM.core.checks  # noqa: F401
    from freecad.DFM.core.registries import get_analyzer_class, get_check_class
    from freecad.DFM.core.utils.geometry import EdgeIndex, FaceIndex

    kwargs = {"prefs": {}, "NOZZLE_DIAMETER": 0.4}
    if direction is not None:
        kwargs["PRINT_ORIENTATION"] = gp_Dir(*direction)
    analyzer = get_analyzer_class(get_check_class(rule)().required_analyzer_id)()
    return analyzer.execute(shape, FaceIndex(shape), EdgeIndex(shape), **kwargs)


def run(proc, rule, shape, direction=(0, 0, 1), material="Default", **limits):
    """*rule* of process *proc* on *shape*: its findings."""
    from freecad.DFM.core.processes.threshold_resolvers import get_resolver
    from freecad.DFM.core.registries import get_check_class

    p = process(proc)
    config = get_resolver(p.archetype).resolve(rule, p, p.materials.get(material), NOZZLE_DIAMETER=0.4)
    if limits:
        config = RuleLimit(target=str(limits.get("target", config.target)),
                           limit=str(limits.get("limit", config.limit)),
                           binary_severity=config.binary_severity)
    data = measure(rule, shape, direction)
    return get_check_class(rule)().run_check(data, config, rule, feedback=p.rule_feedback[rule],
                                              _archetype=p.archetype)


def severities(found):
    return [f.severity for f in found]


def faces(finding):
    return {n for kind, n in finding.failing_geometry if kind == "Face"}


BLOCK = (0, 0, 0, 40, 40, 20)


def hollow(*holes):
    """A 40 mm block with a 20 x 20 x 10 mm hollow, drilled with *holes*:
    (entry, axis, diameter, depth)."""
    s = B.cut(B.box(*BLOCK), B.box(10, 10, 5, 30, 30, 15))
    for entry, axis, d, depth in holes:
        s = B.cut(s, B.drill(entry, axis, d, depth))
    return s


TOP_HOLE = ((20, 20, 20), (0, 0, -1))
BOTTOM_HOLE = ((14, 14, 0), (0, 0, 1))  # off the top hole's axis: coaxial ones merge into one hole


def pins_part(*pins, plate=(60, 40, 3)):
    """A plate with pins standing on it: (x, y, diameter, height)."""
    x, y, z = plate
    return B.fuse(B.box(0, 0, 0, x, y, z), *(B.cylinder((px, py, z), d / 2, h) for px, py, d, h in pins))


# -- Plumbing --------------------------------------------------------------------------------------


class TestArchetypes(unittest.TestCase):
    def test_processes_load_with_their_archetypes(self):
        self.assertEqual(process("resin").archetype, A.RESIN)
        self.assertEqual(process("powder").archetype, A.POWDER_BED)
        self.assertEqual(process("resin").name, "Resin 3D Printing (SLA)")
        self.assertEqual(process("powder").name, "Powder Bed Fusion (SLS)")

    def test_labels_and_descriptions(self):
        for arch in A:
            self.assertTrue(arch.label and arch.description, arch)
        self.assertIn("SLS", A.POWDER_BED.description)

    def test_only_a_powder_bed_waives_the_print_orientation(self):
        self.assertEqual(A.POWDER_BED.waived_requirements, {Req.PRINT_ORIENTATION})
        for arch in (A.GENERIC, A.INJECTION_MOLDING, A.FDM, A.RESIN):
            self.assertEqual(arch.waived_requirements, set(), arch)


class TestRequiredInputs(unittest.TestCase):
    """What the setup asks for, from the active rules' analyzers."""

    def inputs(self, proc, *extra_rules):
        import copy

        from freecad.DFM.app.process_requirements import required_inputs

        p = copy.deepcopy(process(proc))
        p.active_rules = list(p.active_rules) + list(extra_rules)
        return required_inputs(p)

    def test_resin_asks_for_a_print_orientation_only(self):
        self.assertEqual(self.inputs("resin"), {Req.PRINT_ORIENTATION})

    def test_powder_bed_asks_for_nothing(self):
        self.assertEqual(self.inputs("powder"), set())

    def test_a_rule_that_needs_the_orientation_still_asks_for_it_on_a_powder_bed(self):
        self.assertEqual(self.inputs("powder", R.MAX_OVERHANG_ANGLE), {Req.PRINT_ORIENTATION})

    def test_fdm_asks_for_the_nozzle_and_layer_height_too(self):
        self.assertEqual(self.inputs("fdm"),
                         {Req.PRINT_ORIENTATION, Req.NOZZLE_DIAMETER, Req.LAYER_HEIGHT})

    def test_moulding_is_unchanged(self):
        from freecad.DFM.app.process_requirements import required_inputs

        self.assertEqual(required_inputs(S.moulding_process()),
                         {Req.PULL_DIRECTION, Req.SIDE_ACTIONS, Req.NOMINAL_WALL_THICKNESS})

    def test_print_feature_analyzer_can_run_without_a_direction(self):
        from freecad.DFM.core.registries import get_analyzer_class

        analyzer = get_analyzer_class("PRINT_FEATURE_ANALYZER")()
        self.assertEqual(analyzer.requirements, {Req.PRINT_ORIENTATION})
        self.assertEqual(analyzer.optional_requirements, {Req.PRINT_ORIENTATION})
        pf = measure(R.SEALED_CAVITY, hollow(), direction=None)
        self.assertIsNone(pf.directional)
        self.assertEqual(len(pf.features(FeatureKind.VOID)), 1)


class TestFdmTextStroke(unittest.TestCase):
    def resolve(self, nozzle):
        from freecad.DFM.core.processes.threshold_resolvers import get_resolver

        p = process("fdm")
        return get_resolver(p.archetype).resolve(R.MIN_PRINTED_TEXT_STROKE, p, None, NOZZLE_DIAMETER=nozzle)

    def test_two_and_one_extrusion_widths(self):
        config = self.resolve(0.6)
        self.assertEqual((float(config.target), float(config.limit)), (1.2, 0.6))
        self.assertIn(R.MIN_PRINTED_TEXT_STROKE, A.FDM.derived_rules)

    def test_without_a_usable_nozzle_the_material_values_apply(self):
        for nozzle in (None, "abc", 0):
            config = self.resolve(nozzle)
            self.assertEqual((config.target, config.limit), ("0.8", "0.4"), nozzle)

    def test_wall_thickness_is_still_derived(self):
        from freecad.DFM.core.processes.threshold_resolvers import get_resolver

        p = process("fdm")
        config = get_resolver(p.archetype).resolve(R.MIN_WALL_THICKNESS, p, None, NOZZLE_DIAMETER=0.4)
        self.assertEqual((float(config.target), float(config.limit)), (0.8, 0.4))


class TestBuiltinByName(unittest.TestCase):
    """A built-in process is known by the name in its file, so FDM (in
    fdm_3d_printing.yaml) can be restored and isn't deleted as custom."""

    def test_names_not_file_names(self):
        from freecad.DFM.core.registries.process_registry import ProcessRegistry

        registry = ProcessRegistry.__new__(ProcessRegistry)
        registry.processes = {}
        registry.dev_dir = __import__("pathlib").Path(PROCESSES)
        with tempfile.TemporaryDirectory() as user:
            registry.user_dir = __import__("pathlib").Path(user)
            registry.discover_processes()
            for name in ("FDM", "Resin 3D Printing (SLA)", "Powder Bed Fusion (SLS)", "Plastic Injection Molding"):
                self.assertTrue(registry.is_builtin(name), name)
            self.assertFalse(registry.is_builtin("My Process"))
            # A saved copy's file name: one plain file in the user folder.
            self.assertEqual(registry.get_process_filename("Resin 3D Printing (SLA)"),
                             "resin_3d_printing_(sla).yaml")
            self.assertEqual(registry.get_process_filename("Powder Bed Fusion (SLS)"),
                             "powder_bed_fusion_(sls).yaml")
            self.assertFalse(registry.delete_custom_process("FDM"))


# -- Cupping annotation ------------------------------------------------------------------------------


class TestCupAnnotation(unittest.TestCase):
    def cups(self, shape, direction=(0, 0, 1)):
        pf = measure(R.MAX_CUP_AREA, shape, direction)
        return {f.kind.value: (pf.annotation(f.id).get("cup"), pf.annotation(f.id).get("cup_area"))
                for f in pf.result.features if f.kind in (FeatureKind.HOLE, FeatureKind.POCKET,
                                                          FeatureKind.CUTOUT)}

    def test_blind_hole_open_to_the_tank(self):
        shape = B.cut(B.box(*BLOCK), B.drill((10, 10, 20), (0, 0, -1), 6, 8))
        where, area = self.cups(shape)["hole"]
        self.assertEqual(where, "tank")
        self.assertAlmostEqual(area, math.pi * 9, delta=0.01)
        self.assertEqual(self.cups(shape, (0, 0, -1))["hole"][0], "platform")  # upside down, on the platform
        self.assertIsNone(self.cups(shape, (1, 0, 0))["hole"][0])  # sideways: open to the resin

    def test_pocket_open_to_the_tank_or_on_the_platform(self):
        self.assertEqual(self.cups(B.cut(B.box(*BLOCK), B.box(10, 10, 12, 30, 30, 21)))["pocket"], ("tank", 400.0))
        self.assertEqual(self.cups(B.cut(B.box(*BLOCK), B.box(10, 10, -1, 30, 30, 8)))["pocket"],
                         ("platform", 400.0))

    def test_through_openings_cup_only_on_the_platform(self):
        self.assertEqual(self.cups(B.cut(B.box(*BLOCK), B.drill((10, 10, 0), (0, 0, 1), 6, 21)))["hole"][0],
                         "platform")
        self.assertEqual(self.cups(B.cut(B.box(*BLOCK), B.box(10, 10, -1, 30, 30, 21)))["cutout"],
                         ("platform", 400.0))
        # A hole through a ledge above the platform is open at both ends.
        ledge = B.fuse(B.box(0, 0, 0, 40, 10, 20), B.box(0, 0, 15, 40, 40, 20))
        ledge = B.cut(ledge, B.drill((20, 25, 21), (0, 0, -1), 6, 7))
        self.assertIsNone(self.cups(ledge)["hole"][0])

    def test_vented_pocket_does_not_cup(self):
        shape = B.cut(B.box(*BLOCK), B.box(10, 10, 12, 30, 30, 21), B.drill((20, 20, 13), (0, 0, -1), 3, 14))
        pockets = [v for k, v in self.cups(shape).items() if k == "pocket"]
        self.assertTrue(all(where is None for where, _a in pockets), pockets)


# -- Hollows ------------------------------------------------------------------------------------------


class TestSealedCavity(unittest.TestCase):
    def test_an_error_in_resin_and_a_warning_in_powder(self):
        found = run("resin", R.SEALED_CAVITY, hollow())
        self.assertEqual(severities(found), [Severity.ERROR])
        self.assertIn("liquid resin", found[0].message)
        self.assertIn("20.00 mm across", found[0].message)
        found = run("powder", R.SEALED_CAVITY, hollow(), direction=None)
        self.assertEqual(severities(found), [Severity.WARNING])
        self.assertIn("unfused powder", found[0].message)

    def test_a_vented_hollow_is_not_sealed(self):
        self.assertEqual(run("resin", R.SEALED_CAVITY, hollow((*TOP_HOLE, 4, 6))), [])


class TestEscapeHoles(unittest.TestCase):
    def test_one_small_hole(self):
        found = run("resin", R.MIN_ESCAPE_HOLE_DIAMETER, hollow((*TOP_HOLE, 2, 6)))
        self.assertEqual(severities(found), [Severity.ERROR, Severity.WARNING])
        size, single = found
        self.assertEqual(size.value, 2.0)
        self.assertIn("one escape hole, Ø2.00 mm", size.message)
        self.assertIn("one escape hole", single.overview)
        self.assertEqual(faces(size), faces(single))

    def test_the_holes_are_highlighted_with_the_hollow(self):
        pf = measure(R.MIN_ESCAPE_HOLE_DIAMETER, hollow((*TOP_HOLE, 2, 6)))
        hole = pf.features(FeatureKind.HOLE)[0]
        void = pf.features(FeatureKind.VOID)[0]
        found = run("resin", R.MIN_ESCAPE_HOLE_DIAMETER, hollow((*TOP_HOLE, 2, 6)))
        self.assertTrue(set(hole.faces) <= faces(found[0]))
        self.assertTrue(set(void.faces) <= faces(found[0]))

    def test_two_holes_graded_by_the_smallest(self):
        shape = hollow((*TOP_HOLE, 3, 6), (*BOTTOM_HOLE, 6, 6))
        found = run("resin", R.MIN_ESCAPE_HOLE_DIAMETER, shape)
        self.assertEqual(severities(found), [Severity.WARNING])  # 3 mm: under 3.5, over 2.5
        self.assertIn("2 escape holes, the smallest Ø3.00 mm", found[0].message)
        self.assertEqual(run("resin", R.MIN_ESCAPE_HOLE_DIAMETER, hollow((*TOP_HOLE, 4, 6), (*BOTTOM_HOLE, 4, 6))),
                         [])

    def test_powder_and_tpu(self):
        shape = hollow((*TOP_HOLE, 4, 6), (*BOTTOM_HOLE, 4, 6))
        found = run("powder", R.MIN_ESCAPE_HOLE_DIAMETER, shape, direction=None)
        self.assertEqual(severities(found), [Severity.WARNING])  # under the 5 mm aim
        found = run("powder", R.MIN_ESCAPE_HOLE_DIAMETER, shape, direction=None, material="TPU")
        self.assertEqual(severities(found), [Severity.ERROR])  # Materialise TPU: 10 mm


class TestCupping(unittest.TestCase):
    def test_levels(self):
        found = run("resin", R.MAX_CUP_AREA, B.cut(B.box(*BLOCK), B.box(2, 2, 12, 38, 38, 21)))
        self.assertEqual(severities(found), [Severity.ERROR])  # 1,296 mm², past 900
        self.assertIn("1,296 mm² open", found[0].message)
        self.assertIn("tank's film", found[0].message)
        found = run("resin", R.MAX_CUP_AREA, B.cut(B.box(*BLOCK), B.box(10, 10, 12, 30, 30, 21)))
        self.assertEqual(severities(found), [Severity.WARNING])
        self.assertEqual(found[0].value, 400.0)
        self.assertIn("400 mm² > 100 mm²", found[0].overview)
        found = run("resin", R.MAX_CUP_AREA, B.cut(B.box(*BLOCK), B.box(5, 5, 12, 35, 35, 21)))
        self.assertEqual(severities(found), [Severity.WARNING])  # 900 mm²: at the limit, not past it
        self.assertEqual(run("resin", R.MAX_CUP_AREA, B.cut(B.box(*BLOCK), B.drill((20, 20, 20), (0, 0, -1), 6, 6))),
                         [])  # 28 mm²: a small cup usually prints

    def test_on_the_platform_is_a_warning_at_most(self):
        found = run("resin", R.MAX_CUP_AREA, B.cut(B.box(*BLOCK), B.box(2, 2, -1, 38, 38, 8)))
        self.assertEqual(severities(found), [Severity.WARNING])
        self.assertIn("on the platform", found[0].overview)
        self.assertIn("printed directly on it", found[0].message)


class TestPrintedHoleDepth(unittest.TestCase):
    def test_blind_and_through(self):
        shape = S.plate_with_holes([(20, 30, 1.0, 9), (40, 30, 2.0, 7), (60, 30, 1.0, None), (80, 30, 4, 8)])
        found = run("resin", R.MAX_PRINTED_HOLE_DEPTH, shape)
        self.assertEqual([(f.severity, f.value) for f in found],
                         [(Severity.ERROR, 9.0), (Severity.WARNING, 3.5), (Severity.WARNING, 10.0)])
        self.assertIn("through hole: longer allowed", found[2].overview)
        self.assertIn("10× > 8×", found[2].overview)  # the through hole's 8/3 of the blind 3×
        self.assertIn("powder", run("powder", R.MAX_PRINTED_HOLE_DEPTH, shape, direction=None)[0].message.lower())


# -- Gaps ------------------------------------------------------------------------------------------------


class TestPrintGap(unittest.TestCase):
    def slot(self, width):
        return B.cut(B.box(0, 0, 0, 40, 20, 10), B.box(10, -1, 2, 10 + width, 21, 11))

    def test_levels_per_process(self):
        found = run("fdm", R.MIN_PRINT_GAP, self.slot(0.4))
        self.assertEqual(severities(found), [Severity.WARNING])
        self.assertIn("the gap between Face", found[0].message)
        self.assertIn(" and Face", found[0].message)
        self.assertEqual(len(faces(found[0])), 2)
        self.assertEqual(severities(run("fdm", R.MIN_PRINT_GAP, self.slot(0.25))), [Severity.ERROR])
        self.assertEqual(run("fdm", R.MIN_PRINT_GAP, self.slot(0.6)), [])
        self.assertEqual(severities(run("powder", R.MIN_PRINT_GAP, self.slot(0.8), direction=None)),
                         [Severity.WARNING])

    def test_a_pin_in_a_hole_is_a_gap_but_a_bore_is_not(self):
        plate = B.cut(B.box(0, 0, 0, 30, 30, 10), B.drill((15, 15, 10), (0, 0, -1), 6.6, 8))
        found = run("powder", R.MIN_PRINT_GAP, B.fuse(plate, B.cylinder((15, 15, 2), 3, 12)), direction=None)
        self.assertEqual(severities(found), [Severity.ERROR])
        self.assertAlmostEqual(found[0].value, 0.3, delta=0.01)
        # A small hole on its own: its diameter is the hole rule's, not a gap.
        self.assertEqual(run("powder", R.MIN_PRINT_GAP, S.plate_with_holes([(20, 30, 0.4, None)]),
                             direction=None), [])

    def test_mould_steel_is_unchanged(self):
        # The moulding analyzer still leaves the pin and hole to the hole rules.
        from freecad.DFM.core.registries import get_analyzer_class
        from freecad.DFM.core.utils.geometry import EdgeIndex, FaceIndex
        from OCP.gp import gp_Dir

        plate = B.cut(B.box(0, 0, 0, 30, 30, 10), B.drill((15, 15, 10), (0, 0, -1), 6.6, 8))
        shape = B.fuse(plate, B.cylinder((15, 15, 2), 3, 12))
        steel = get_analyzer_class("MOLD_STEEL_ANALYZER")().execute(
            shape, FaceIndex(shape), EdgeIndex(shape), prefs={}, PULL_DIRECTION=gp_Dir(0, 0, 1))
        self.assertTrue(all(v[0] > 1.0 for v in steel.values()), steel)
        self.assertTrue(all(len(v) == 3 for v in steel.values()))


# -- Pins ------------------------------------------------------------------------------------------------


class TestPins(unittest.TestCase):
    def test_slenderness(self):
        shape = pins_part((10, 10, 1.0, 25), (30, 10, 2.0, 25), (50, 10, 4, 25))
        found = run("resin", R.MAX_PIN_SLENDERNESS, shape)
        self.assertEqual([(f.severity, f.value) for f in found],
                         [(Severity.ERROR, 25.0), (Severity.WARNING, 12.5)])
        self.assertIn("25.00 mm tall and 1.00 mm across", found[0].message)
        self.assertIn("25× > 20×", found[0].overview)

    def test_root_fillet(self):
        shape = pins_part((10, 10, 3, 15), (30, 10, 6, 15))
        found = run("fdm", R.PIN_ROOT_FILLET, shape)
        self.assertEqual(severities(found), [Severity.WARNING])  # only the 3 mm pin: under 5 mm
        self.assertIn("no fillet at its base", found[0].overview)
        filleted = B.fillet(shape, 0.8, B.edges_where(
            shape, lambda m, e: abs(m[2] - 3) < 1e-6 and abs(math.hypot(m[0] - 10, m[1] - 10) - 1.5) < 1e-3))
        self.assertEqual(run("fdm", R.PIN_ROOT_FILLET, filleted), [])

    def test_screw_bosses_are_not_pins(self):
        self.assertEqual(run("fdm", R.PIN_ROOT_FILLET, S.boss(4, 15, hole=2)), [])


# -- Text --------------------------------------------------------------------------------------------------


class TestPrintedText(unittest.TestCase):
    def test_stroke_levels(self):
        shape = FX.by_name("text_engraved").build()  # strokes 0.89 mm
        self.assertEqual(run("resin", R.MIN_PRINTED_TEXT_STROKE, shape), [])
        found = run("fdm", R.MIN_PRINTED_TEXT_STROKE, shape, target=1.2, limit=1.0)
        self.assertEqual(severities(found), [Severity.ERROR])
        self.assertIn("cut-in strokes 0.89 mm wide", found[0].message)
        self.assertIn("may fill in", found[0].message)
        found = run("fdm", R.MIN_PRINTED_TEXT_STROKE, FX.by_name("text_embossed").build(), target=1.2, limit=0.5)
        self.assertEqual(severities(found), [Severity.WARNING])

    def test_text_faces_are_taken_from_the_gap_rule(self):
        from freecad.DFM.core.registries import get_check_class

        shape = FX.by_name("text_engraved").build()
        pf = measure(R.MIN_PRINTED_TEXT_STROKE, shape)
        text = pf.features(FeatureKind.TEXT)[0]
        check = get_check_class(R.MIN_PRINTED_TEXT_STROKE)()
        self.assertEqual(check.supersedes, (R.MIN_PRINT_GAP,))
        self.assertEqual(check.covered_faces(pf), set(text.faces))

    def test_character_height_depends_on_the_process(self):
        from freecad.DFM.core.checks import print_feature_checks as P

        self.assertEqual(P.min_character_height(A.FDM), 3.5)
        self.assertEqual(P.min_character_height(A.RESIN), 2.0)
        self.assertEqual(P.min_character_height(A.POWDER_BED), 2.0)
        self.assertEqual(P.min_character_height(None), 3.5)


# -- Flat faces, proportions, bed edges ------------------------------------------------------------------


class TestFlatFaces(unittest.TestCase):
    def test_resin_judges_faces_flat_in_the_layers(self):
        plate = B.box(0, 0, 0, 80, 60, 5)
        found = run("resin", R.MAX_FLAT_FACE_AREA, plate)
        self.assertEqual(severities(found), [Severity.WARNING, Severity.WARNING])  # top and bottom
        self.assertIn("4,800 mm² > 2,500 mm²", found[0].overview)
        self.assertIn("peels off the tank", found[0].message)
        self.assertEqual(run("resin", R.MAX_FLAT_FACE_AREA, plate, direction=(1, 0, 0)), [])  # on edge
        tilted = B.moved(plate, axis=(1, 0, 0), angle_deg=35)
        self.assertEqual(run("resin", R.MAX_FLAT_FACE_AREA, tilted), [])

    def test_powder_judges_every_flat_face(self):
        found = run("powder", R.MAX_FLAT_FACE_AREA, B.box(0, 0, 0, 120, 100, 5), direction=None)
        self.assertEqual(len(found), 2)
        self.assertIn("12,000 mm² > 10,000 mm²", found[0].overview)
        self.assertEqual(run("powder", R.MAX_FLAT_FACE_AREA, B.box(0, 0, 0, 80, 60, 5), direction=None), [])
        self.assertIn("warp", found[0].message)
        self.assertEqual(run("powder", R.MAX_FLAT_FACE_AREA, B.cylinder((0, 0, 0), 20, 60), direction=None), [])


class TestLongThinPart(unittest.TestCase):
    def test_at_any_angle(self):
        bar = B.box(0, 0, 0, 150, 10, 4)
        found = run("powder", R.MAX_PART_ASPECT_RATIO, bar, direction=None)
        self.assertEqual(severities(found), [Severity.WARNING])
        self.assertEqual(found[0].failing_geometry, [])  # the whole part
        self.assertIn("150.0 × 10.0 × 4.0 mm", found[0].message)
        turned = B.moved(bar, axis=(0, 0, 1), angle_deg=30)
        self.assertAlmostEqual(run("powder", R.MAX_PART_ASPECT_RATIO, turned, direction=None)[0].value, 15.0,
                               delta=0.05)
        self.assertEqual(run("powder", R.MAX_PART_ASPECT_RATIO, B.box(0, 0, 0, 90, 10, 4), direction=None), [])


class TestBedEdges(unittest.TestCase):
    def test_sharp_edges_and_holes(self):
        found = run("fdm", R.BED_EDGE_CHAMFER, S.plate_with_holes([(20, 30, 4, None)]))
        self.assertEqual(severities(found), [Severity.WARNING])
        edges = [n for kind, n in found[0].failing_geometry if kind == "Edge"]
        self.assertEqual(len(edges), 5)  # 4 outside edges and the hole's
        self.assertTrue(found[0].overview.endswith("5 sharp edges on the bed"), found[0].overview)
        self.assertIn("elephant's foot", found[0].message)

    def test_chamfered_or_filleted_bases_pass(self):
        box = B.box(0, 0, 0, 40, 30, 10)
        bottom = B.edges_where(box, lambda m, e: abs(m[2]) < 1e-6)
        self.assertEqual(run("fdm", R.BED_EDGE_CHAMFER, B.chamfer(box, 0.8, bottom)), [])
        self.assertEqual(run("fdm", R.BED_EDGE_CHAMFER, B.fillet(box, 0.8, bottom)), [])

    def test_follows_the_print_direction(self):
        box = B.box(0, 0, 0, 40, 30, 10)
        top = B.edges_where(box, lambda m, e: abs(m[2] - 10) < 1e-6)
        chamfered_top = B.chamfer(box, 0.8, top)
        self.assertEqual(run("fdm", R.BED_EDGE_CHAMFER, chamfered_top, direction=(0, 0, -1)), [])
        self.assertEqual(len(run("fdm", R.BED_EDGE_CHAMFER, chamfered_top)), 1)


if __name__ == "__main__":
    unittest.main()
