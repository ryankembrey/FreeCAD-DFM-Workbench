# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The documentation is in step with the code.

docs/build.py --check (in its own interpreter, as it installs FreeCAD
stand-ins) fails when:

* docs/_data/citations.yaml points at a process, material, rule, value or
  source that doesn't exist (exit 2), for example after a value was removed
  from a process file;
* a page or diagram generated from the code is out of date or missing
  (exit 1): a rule, process, message or value changed and `pixi run docs`
  wasn't run.
"""

import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class TestDocs(unittest.TestCase):
    def test_generated_docs_are_up_to_date(self):
        try:
            import PySide6  # noqa: F401
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PySide6 or PyYAML not installed")
        env = dict(os.environ)
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "docs", "build.py"), "--check"],
            env=env, cwd=ROOT, capture_output=True, text=True, timeout=600,
        )
        output = proc.stdout[-4000:] + proc.stderr[-4000:]
        self.assertNotEqual(proc.returncode, 2, "docs/_data/citations.yaml has problems:\n" + output)
        self.assertEqual(proc.returncode, 0, "Run `pixi run docs` and commit docs/:\n" + output)


if __name__ == "__main__":
    unittest.main()
