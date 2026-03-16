import sys
import os

WORKBENCH = os.path.expanduser("~/documents/git/FreeCAD-DFM-Workbench")
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
        import OCC.Core.TopoDS

        print(f"OCC found.")
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
