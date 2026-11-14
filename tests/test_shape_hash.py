# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The shape hash saved with results and checks: it follows the geometry,
not the mesh drawn from it."""

import unittest

import dfm_test_support  # noqa: F401  (FreeCAD stand-ins, sys.path)

from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools

from freecad.DFM.app import document_object as DO
from freecad.DFM.core.recognition.testing import shapes as B


class _Shape:
    """Enough of Part.Shape: an OCP shape, written with whatever mesh it has."""

    def __init__(self, ocp):
        self.ocp = ocp

    def exportBrep(self, path):  # noqa: N802 (FreeCAD naming)
        BRepTools.Write_s(self.ocp, path)

    def exportBrepToString(self):  # noqa: N802
        import os
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".brep")
        os.close(fd)
        try:
            self.exportBrep(path)
            with open(path) as fh:
                return fh.read()
        finally:
            os.remove(path)


def _meshed(shape, deflection):
    BRepTools.Clean_s(shape)
    BRepMesh_IncrementalMesh(shape, deflection, False, 0.3, True)
    return _Shape(shape)


class TestShapeHash(unittest.TestCase):
    def test_a_new_mesh_is_not_a_change(self):
        coarse = DO.compute_shape_hash(_meshed(B.cylinder((0, 0, 0), 5, 10), 1.0))
        fine = DO.compute_shape_hash(_meshed(B.cylinder((0, 0, 0), 5, 10), 0.01))
        plain = DO.compute_shape_hash(_Shape(B.cylinder((0, 0, 0), 5, 10)))
        self.assertEqual(coarse, fine)
        self.assertEqual(coarse, plain)
        self.assertTrue(coarse.startswith("g") and len(coarse) == 32)

    def test_a_new_shape_is_a_change(self):
        a = DO.compute_shape_hash(_Shape(B.cylinder((0, 0, 0), 5, 10)))
        self.assertNotEqual(a, DO.compute_shape_hash(_Shape(B.cylinder((0, 0, 0), 5, 11))))
        self.assertNotEqual(a, DO.compute_shape_hash(_Shape(B.cylinder((3, 0, 0), 5, 10))))

    def test_hashes_saved_by_older_versions_still_match(self):
        shape = _meshed(B.box(0, 0, 0, 10, 10, 10), 0.5)
        old = DO.legacy_shape_hash(shape)
        self.assertTrue(DO.shape_matches(shape, old))
        self.assertTrue(DO.shape_matches(shape, DO.compute_shape_hash(shape)))
        self.assertFalse(DO.shape_matches(_Shape(B.box(0, 0, 0, 10, 10, 11)), DO.compute_shape_hash(shape)))
        self.assertFalse(DO.shape_matches(shape, ""))


if __name__ == "__main__":
    unittest.main()
