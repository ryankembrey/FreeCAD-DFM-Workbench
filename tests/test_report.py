# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The PDF report's bookmarks, links and sections for saved analyses and
checks (Qt, offscreen), in their own interpreter with FreeCAD stand-ins: see
tests/recognition_support/report_script.py."""

import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SUPPORT = os.path.join(HERE, "recognition_support")


class TestReport(unittest.TestCase):
    def test_navigation_and_view_sections(self):
        try:
            import PySide6  # noqa: F401
        except ImportError:
            self.skipTest("PySide6 not installed")
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        proc = subprocess.run([sys.executable, os.path.join(SUPPORT, "report_script.py")], env=env, cwd=ROOT,
                              capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-4000:])
        self.assertIn("report: ok", proc.stdout)


if __name__ == "__main__":
    unittest.main()
