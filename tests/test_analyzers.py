import unittest

from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCC.Core.TopoDS import TopoDS_Shape

from dfm.analyzers.sphere_thickness_analyzer import SphereThicknessAnalyzer
from dfm.analyzers.ray_thickness_analyzer import RayThicknessAnalyzer


class TestSphereThicknessAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = SphereThicknessAnalyzer()
        self.cube: TopoDS_Shape = BRepPrimAPI_MakeBox(100.0, 100.0, 100.0).Shape()
        self.plate: TopoDS_Shape = BRepPrimAPI_MakeBox(100.0, 100.0, 5.0).Shape()
        self.cylinder: TopoDS_Shape = BRepPrimAPI_MakeCylinder(20.0, 100.0).Shape()

    def test_cube_returns_exact_dimensions(self):
        data = self.analyzer.execute(self.cube)

        self.assertEqual(len(data), 6, "Cube should have 6 faces in data")
        for face, result_list in data.items():
            self.assertTrue(len(result_list) > 0, "Face returned no data")
            self.assertAlmostEqual(max(result_list), 100.0, places=1)

    def test_thin_plate_does_not_over_inflate(self):
        data = self.analyzer.execute(self.plate)

        for face, result_list in data.items():
            if result_list:
                self.assertLessEqual(max(result_list), 5.001)

    def test_cylinder_is_bounded_by_diameter(self):
        data = self.analyzer.execute(self.cylinder)

        max_overall = 0.0
        for face, thicks in data.items():
            if thicks:
                max_overall = max(max_overall, max(thicks))

        self.assertAlmostEqual(max_overall, 40.0, places=1)


class TestRayThicknessAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = RayThicknessAnalyzer()
        self.cube: TopoDS_Shape = BRepPrimAPI_MakeBox(100.0, 100.0, 100.0).Shape()
        self.cylinder: TopoDS_Shape = BRepPrimAPI_MakeCylinder(20.0, 100.0).Shape()

    def test_cube_returns_exact_dimensions(self):
        data = self.analyzer.execute(self.cube)

        self.assertEqual(len(data), 6)
        for face, result_list in data.items():
            self.assertTrue(len(result_list) > 0)
            self.assertAlmostEqual(max(result_list), 100.0, places=1)

    def test_cylinder_measures_full_height(self):
        data = self.analyzer.execute(self.cylinder)

        max_overall = 0.0
        for face, thicks in data.items():
            if thicks:
                max_overall = max(max_overall, max(thicks))

        self.assertAlmostEqual(max_overall, 100.0, places=1)


if __name__ == "__main__":
    unittest.main()
