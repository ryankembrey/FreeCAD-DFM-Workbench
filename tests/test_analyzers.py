import unittest

from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCC.Core.gp import gp_Pnt, gp_Ax2, gp_Dir, gp_Vec
from OCC.Core.TopoDS import TopoDS_Shape

from dfm.analyzers.sphere_thickness_analyzer import SphereThicknessAnalyzer
from dfm.analyzers.ray_thickness_analyzer import RayThicknessAnalyzer


class TestSphereThicknessAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = SphereThicknessAnalyzer()
        self.cube: TopoDS_Shape = BRepPrimAPI_MakeBox(100.0, 100.0, 100.0).Shape()
        self.plate: TopoDS_Shape = BRepPrimAPI_MakeBox(100.0, 100.0, 5.0).Shape()
        self.cylinder: TopoDS_Shape = BRepPrimAPI_MakeCylinder(15.0, 100.0).Shape()

        box = BRepPrimAPI_MakeBox(100.0, 100.0, 100.0).Shape()
        axis = gp_Ax2(gp_Pnt(50.0, 50.0, -1.0), gp_Dir(0.0, 0.0, 1.0))
        drill = BRepPrimAPI_MakeCylinder(axis, 15.0, 102.0).Shape()
        cut = BRepAlgoAPI_Cut(box, drill)
        cut.Build()
        self.box_with_hole: TopoDS_Shape = cut.Shape()

    def test_cube_max_thickness(self):
        data = self.analyzer.execute(self.cube)
        self.assertEqual(len(data), 6, "Cube should have 6 faces")
        max_overall = max(max(v) for v in data.values() if v)
        self.assertAlmostEqual(max_overall, 100.0, places=1)

    def test_cube_all_faces_have_data(self):
        data = self.analyzer.execute(self.cube)
        for face, result_list in data.items():
            self.assertTrue(len(result_list) > 0, "A face returned no data")

    def test_cylinder_bounded_by_diameter(self):
        data = self.analyzer.execute(self.cylinder)
        max_overall = max(max(v) for v in data.values() if v)
        self.assertAlmostEqual(max_overall, 30.0, places=1)

    def test_box_with_hole_max_corner_thickness(self):
        data = self.analyzer.execute(self.box_with_hole)
        max_overall = max(max(v) for v in data.values() if v)
        self.assertAlmostEqual(max_overall, 46.15, places=1)

    def test_no_infinite_values(self):
        data = self.analyzer.execute(self.cube)
        for face, result_list in data.items():
            for t in result_list:
                self.assertFalse(t == float("inf"), f"Infinite thickness returned for a face")

    def test_all_values_positive(self):
        data = self.analyzer.execute(self.cube)
        for face, result_list in data.items():
            for t in result_list:
                self.assertGreater(t, 0.0, "Non-positive thickness returned")


class TestRayThicknessAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = RayThicknessAnalyzer()
        self.cube: TopoDS_Shape = BRepPrimAPI_MakeBox(100.0, 100.0, 100.0).Shape()
        self.cylinder: TopoDS_Shape = BRepPrimAPI_MakeCylinder(15.0, 100.0).Shape()

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
