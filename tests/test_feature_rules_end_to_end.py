# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The injection moulding process through AnalysisRunner, old rules and
feature rules together (tests/recognition_support/end_to_end_script.py,
in a fresh interpreter with FreeCAD stand-ins)."""

import os
import subprocess
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestEndToEnd(unittest.TestCase):
    def test_moulding_process(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        script = os.path.join(ROOT, "tests", "recognition_support", "end_to_end_script.py")
        proc = subprocess.run([sys.executable, script], env=env, cwd=ROOT, capture_output=True,
                              text=True, timeout=900)
        self.assertEqual(proc.returncode, 0, proc.stdout[-3000:] + proc.stderr[-4000:])
        self.assertIn("end to end: ok", proc.stdout)


if __name__ == "__main__":
    unittest.main()
