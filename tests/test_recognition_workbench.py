# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The workbench side of feature recognition, without FreeCAD: history
hints and the developer bench (Qt, offscreen). Each runs in its own
interpreter with FreeCAD stand-ins (tests/recognition_support)."""

import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SUPPORT = os.path.join(HERE, "recognition_support")


def _run(script: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    return subprocess.run([sys.executable, os.path.join(SUPPORT, script)], env=env, cwd=ROOT,
                          capture_output=True, text=True, timeout=600)


class TestRecognitionWorkbench(unittest.TestCase):
    def test_history_hints(self):
        proc = _run("hints_script.py")
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-4000:])

    def test_bench_panel(self):
        try:
            import PySide6  # noqa: F401
        except ImportError:
            self.skipTest("PySide6 not installed")
        proc = _run("bench_script.py")
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
