# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Every rule is wired through all five touchpoints.

A rule needs a Rulebook entry, a registered check, an analyzer, a diagram
and its place in the process YAML (active, feedback, criticality, Default
thresholds). Missing one fails silently in the workbench (no card, no
picture, no thresholds), so this checks every active rule of the built-in
injection moulding process, and every rule with a check, at once.
"""

import os
import unittest
import xml.etree.ElementTree as ET

import dfm_test_support as S
from freecad.DFM.core.registries.analyzers_registry import get_analyzer_class
from freecad.DFM.core.registries.checks_registry import get_check_class
from freecad.DFM.core.rules import SHAPE_FIELD_ATTRS, Rulebook, RuleShape

PROCESSES = os.path.join(S.ROOT, "freecad", "DFM", "core", "processes")
YAML = os.path.join(PROCESSES, "plastic_injection_molding.yaml")
#: Every built-in process: each one's active rules must be fully wired.
YAMLS = (YAML, *(os.path.join(PROCESSES, name) for name in (
    "fdm_3d_printing.yaml", "resin_3d_printing.yaml", "powder_bed_fusion.yaml")))


def diagrams():
    """DIAGRAMS, imported with the GUI stand-ins (gui/__init__ needs FreeCADGui)."""
    import sys
    import types

    if "FreeCADGui" not in sys.modules:
        try:
            import FreeCADGui  # noqa: F401
        except ImportError:
            gui = types.ModuleType("FreeCADGui")
            gui.addCommand = lambda *a: None
            gui.addPreferencePage = lambda *a: None
            sys.modules["FreeCADGui"] = gui
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return None
    import importlib.util

    # Load the module file on its own: it only needs core.rules.
    path = os.path.join(S.ROOT, "freecad", "DFM", "gui", "rule_information", "_diagrams.py")
    spec = importlib.util.spec_from_file_location("freecad.DFM.gui.rule_information._diagrams", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DIAGRAMS


class TestRuleCompleteness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import copy

        import yaml

        from freecad.DFM.core.processes.process import Process

        cls.raws = []
        for path in YAMLS:
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            cls.raws.append((os.path.basename(path), raw, Process.from_yaml(copy.deepcopy(raw))))
        cls.raw = cls.raws[0][1]
        cls.diagrams = diagrams()

    def test_every_active_rule_is_wired(self):
        for file_name, raw, process in self.raws:
            self._wired(file_name, raw, process)

    def _wired(self, file_name, raw, process):
        defaults = raw["materials"]["Default"]["rule_limits"]
        for name in raw["active_rules"]:
            with self.subTest(process=file_name, rule=name):
                rule = Rulebook[name]
                check = get_check_class(rule)
                self.assertIsNotNone(check, "no registered check")
                self.assertIsNotNone(get_analyzer_class(check().required_analyzer_id), "no analyzer")
                self.assertTrue(check().progress_text, "no progress text")
                self.assertIn(name, raw.get("rule_feedback", {}), "no feedback")
                feedback = raw["rule_feedback"][name]
                self.assertEqual(set(feedback), {"warning_msg", "error_msg"})
                self.assertTrue(feedback["warning_msg"] or feedback["error_msg"], "empty feedback")
                self.assertIn(name, raw.get("rule_criticality", {}), "no criticality")
                self.assertIn(name, defaults, "no Default thresholds")
                for attr in SHAPE_FIELD_ATTRS[rule.shape]:
                    self.assertTrue(str(defaults[name].get(attr, "")).strip(), f"Default {attr} missing")
                    if rule.shape != RuleShape.BINARY:
                        float(defaults[name][attr])
                self.assertIn(rule, process.active_rules)  # parses as a Process too

    def test_messages_use_only_their_checks_placeholders(self):
        """Every {placeholder} in a process's messages is one its check fills
        (and the rule feedback editor offers): a mistyped or unfilled one
        would show as "{size}" in the results."""
        import re

        for file_name, raw, _process in self.raws:
            for name, feedback in raw["rule_feedback"].items():
                rule = Rulebook[name]
                check = get_check_class(rule)
                if check is None:
                    continue
                offered = set(check().message_placeholders(rule))
                for kind, text in feedback.items():
                    used = set(re.findall(r"{(\w+)}", text or ""))
                    with self.subTest(process=file_name, rule=name, message=kind):
                        self.assertLessEqual(used, offered, sorted(used - offered))

    def test_new_rules_use_target_and_limit_or_binary(self):
        # MIN_AND_MAX thresholds are dropped by the resolver and when saving.
        for name in (n for _f, raw, _p in self.raws for n in raw["active_rules"]):
            self.assertIn(Rulebook[name].shape, (RuleShape.TARGET_AND_LIMIT, RuleShape.BINARY,
                                                RuleShape.TARGET_ONLY, RuleShape.LIMIT_ONLY), name)

    def test_target_is_no_looser_than_limit(self):
        for _file, raw, _process in self.raws:
            self._targets(raw)

    def _targets(self, raw):
        defaults = raw["materials"]["Default"]["rule_limits"]
        for material, data in raw["materials"].items():
            for name, limits in (data.get("rule_limits") or {}).items():
                rule = Rulebook[name]
                if rule.shape != RuleShape.TARGET_AND_LIMIT:
                    continue
                target = float(limits.get("target") or defaults[name]["target"])
                limit = float(limits.get("limit") or defaults[name]["limit"])
                with self.subTest(material=material, rule=name):
                    if rule.comparison == "max":
                        self.assertLessEqual(target, limit)
                    else:
                        self.assertGreaterEqual(target, limit)

    def test_every_rule_with_a_check_has_a_diagram(self):
        if self.diagrams is None:
            self.skipTest("PySide6 not installed")
        for rule in Rulebook:
            if get_check_class(rule) is None:
                continue
            with self.subTest(rule=rule.name):
                self.assertIn(rule, self.diagrams)
                diagram = self.diagrams[rule]
                self.assertTrue(diagram.brief.strip())
                root = ET.fromstring(diagram.svg)
                self.assertEqual(root.get("viewBox"), "0 0 340 165")
                text = diagram.svg.decode()
                for banned in ("clipPath", "<filter", "Gradient", "<style"):
                    self.assertNotIn(banned, text)
                for placeholder in ("__STROKE__", "__BODY__", "__BAD__", "__GOOD__"):
                    self.assertIn(placeholder, text)

    def test_unit_suffix_for_ratio_rules(self):
        for rule in Rulebook:
            if rule.unit in ("×",) or (rule.unit == "%" and rule.name.endswith(("_RATIO", "_DEPTH"))):
                self.assertTrue(rule.unit_suffix, f"{rule.name}: a ratio needs its reference")


class TestUserCopyGetsNewRules(unittest.TestCase):
    """A user's saved copy of the process predates the new rules: they are added, turned off."""

    def test_merge(self):
        import copy

        import yaml

        from freecad.DFM.core.processes.process import Process
        from freecad.DFM.core.registries.process_registry import merge_new_rules

        with open(YAML, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        old_rules = ["MIN_DRAFT_ANGLE", "MIN_WALL_THICKNESS", "MAX_WALL_THICKNESS", "NO_UNDERCUTS",
                     "SHARP_INTERNAL_CORNERS", "SHARP_EXTERNAL_CORNERS"]
        user = copy.deepcopy(raw)
        user["active_rules"] = old_rules[:-1]  # the user had turned one off
        for key in ("rule_feedback", "rule_criticality"):
            user[key] = {k: v for k, v in user[key].items() if k in old_rules}
        for material in user["materials"].values():
            material["rule_limits"] = {k: v for k, v in (material.get("rule_limits") or {}).items()
                                       if k in old_rules}
        user["materials"]["Default"]["rule_limits"]["MIN_WALL_THICKNESS"]["target"] = "1.7"
        process, builtin = Process.from_yaml(user), Process.from_yaml(copy.deepcopy(raw))

        added = merge_new_rules(process, builtin)

        self.assertIn(Rulebook.MAX_BOSS_WALL_RATIO, added)
        self.assertNotIn(Rulebook.SHARP_EXTERNAL_CORNERS, added)  # mentioned, so the user's choice stands
        self.assertNotIn(Rulebook.MAX_BOSS_WALL_RATIO, process.active_rules)  # added turned off
        self.assertEqual(process.rule_feedback[Rulebook.MAX_BOSS_WALL_RATIO],
                         builtin.rule_feedback[Rulebook.MAX_BOSS_WALL_RATIO])
        self.assertEqual(process.materials["Default"].rule_limits[Rulebook.MAX_BOSS_WALL_RATIO].target, "60")
        self.assertEqual(process.materials["Default"].rule_limits[Rulebook.MIN_WALL_THICKNESS].target, "1.7")
        self.assertEqual(merge_new_rules(process, builtin), [])  # once only


if __name__ == "__main__":
    unittest.main()
