# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import sys
import os

# The repository this script is in (so a second checkout tests its own code).
WORKBENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# A FreeCAD build, if there is one; the tests use FreeCAD stand-ins without it.
FC_LIB = os.path.expanduser("~/documents/git/FreeCAD/build/debug/lib")

for p in [WORKBENCH, FC_LIB]:
    if p not in sys.path:
        sys.path.insert(0, p)


def run_wrapper():
    print("\n--DFM-TESTS-----------------------------------------------------------")
    print("Initializing CAD environment…")

    try:
        # import FreeCAD  # type: ignore
        # import Part  # type: ignore
        import OCP.TopoDS

        print(f"OCP found.")
    except ImportError as e:
        print(f"Environment Failure: {e}")
        return

    print("----------------------------------------------------------------------\n")
    import unittest

    loader = unittest.TestLoader()
    test_dir = os.path.join(WORKBENCH, "tests")
    suite = loader.discover(start_dir=test_dir, pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)


if __name__ == "__main__":
    run_wrapper()
