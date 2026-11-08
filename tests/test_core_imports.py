# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""``freecad.DFM.core`` imports without side effects, and the registries
still find every check and analyzer (now loaded on the first lookup).

Each test runs in a fresh interpreter so sys.modules starts clean. Where
FreeCAD isn't installed, the registry test uses the same stand-ins the
parallel workers use.
"""

import os
import subprocess
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

STAND_INS = r"""
import sys, types
try:
    import FreeCAD  # noqa: F401
except ImportError:
    app = types.ModuleType("FreeCAD")
    class _Any:
        def __getattr__(self, name):
            if name.startswith("Get"):
                return lambda key=None, default=None: default
            return lambda *a, **k: None
    app.Console = _Any(); app.GuiUp = False; app.ParamGet = lambda path: _Any()
    app.getUserAppDataDir = lambda: "/tmp/"
    sys.modules["FreeCAD"] = app
    part = types.ModuleType("Part"); part.Shape = object; sys.modules["Part"] = part
"""


def _run(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-c", code], env=env, cwd=ROOT, capture_output=True, text=True)


class TestCoreImports(unittest.TestCase):
    def test_core_import_has_no_side_effects(self):
        proc = _run("import sys, freecad.DFM.core, freecad.DFM.core.utils.geometry; "
                    "sys.exit(1 if 'FreeCAD' in sys.modules or 'freecad.DFM.core.checks' in sys.modules else 0)")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_registries_load_checks_and_analyzers_on_first_lookup(self):
        try:
            import yaml  # noqa: F401  (process_registry needs it)
        except ImportError:
            self.skipTest("yaml not installed")
        code = STAND_INS + r"""
from freecad.DFM.core.rules import Rulebook
from freecad.DFM.core.registries import get_check_class, get_analyzer_class
found = [r for r in Rulebook if get_check_class(r)]
assert len(found) >= 10, found
for r in found:
    assert get_analyzer_class(get_check_class(r)().required_analyzer_id) is not None, r
print(len(found))
"""
        proc = _run(code)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
