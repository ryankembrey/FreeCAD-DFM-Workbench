# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The recognition package stays pure OCP and self-contained.

* No FreeCAD, FreeCADGui, Part or Qt import anywhere in it.
* From the rest of the workbench it may import only ``core.utils.geometry``
  and ``core.utils.mold`` (pure-OCP helpers).
* Importing it does not load FreeCAD (``core/__init__.py`` has no side
  effects).
"""

import ast
import os
import subprocess
import sys
import unittest

PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORBIDDEN = {"FreeCAD", "FreeCADGui", "Part", "PySide", "PySide2", "PySide6", "Mesh", "Draft"}
ALLOWED_OUTSIDE = {"utils.geometry", "utils.mold", "utils"}


def _python_files():
    for root, _dirs, files in os.walk(PACKAGE):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


class TestPurity(unittest.TestCase):
    def test_no_freecad_or_qt_imports(self):
        for path in _python_files():
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names = [node.module.split(".")[0]]
                for n in names:
                    self.assertNotIn(n, FORBIDDEN, f"{os.path.relpath(path, PACKAGE)}:{node.lineno} imports {n}")

    def test_only_shared_utils_from_the_workbench(self):
        for path in _python_files():
            rel = os.path.relpath(path, PACKAGE)
            depth = rel.count(os.sep)  # files in subpackages are one level deeper
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level > depth + 1:
                    target = "." * (node.level - depth - 1) + (node.module or "")
                    module = (node.module or "")
                    self.assertIn(module, ALLOWED_OUTSIDE,
                                  f"{rel}:{node.lineno} imports {target} from outside the package")
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and \
                        node.module.startswith("freecad.DFM") and not node.module.startswith("freecad.DFM.core.recognition"):
                    self.assertIn(node.module, {"freecad.DFM.core.utils.geometry", "freecad.DFM.core.utils.mold"},
                                  f"{rel}:{node.lineno} imports {node.module}")

    def test_import_does_not_load_freecad(self):
        root = os.path.abspath(os.path.join(PACKAGE, "..", "..", "..", ".."))
        code = ("import sys; import freecad.DFM.core.recognition as r; "
                "r.recognize; bad = [m for m in ('FreeCAD', 'FreeCADGui', 'Part') if m in sys.modules]; "
                "print(bad); sys.exit(1 if bad else 0)")
        env = dict(os.environ)
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=root)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
