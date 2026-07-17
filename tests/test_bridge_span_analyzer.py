# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import unittest

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.TopoDS import TopoDS_Face, TopoDS_Shape

from freecad.DFM.core.analyzers.bridge_span_analyzer import BridgeSpanAnalyzer
from freecad.DFM.core.models import ProcessRequirement
from freecad.DFM.core.utils.geometry import (
    EdgeIndex,
    FaceIndex,
    get_face_uv_center,
    get_face_uv_normal,
    calculate_bed_height,
)


PRINT_DIR = ProcessRequirement.PRINT_ORIENTATION.name


# =============================================================================


def _box(x0, y0, z0, x1, y1, z1) -> TopoDS_Shape:
    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def _cut(a: TopoDS_Shape, b: TopoDS_Shape) -> TopoDS_Shape:
    op = BRepAlgoAPI_Cut(a, b)
    op.Build()
    return op.Shape()


def _fuse(a: TopoDS_Shape, b: TopoDS_Shape) -> TopoDS_Shape:
    op = BRepAlgoAPI_Fuse(a, b)
    op.Build()
    return op.Shape()


def make_bridge() -> TopoDS_Shape:
    """Two legs and a beam. The beam underside sits at z=30 and spans x 20..80,
    so the expected bridge span is 60mm.

    Note the y=0 side of this solid is a single coplanar face covering both legs
    and the beam front. The open tunnel ends must not be read as walls just
    because that shared face descends at the legs.
    """
    return _cut(_box(0, 0, 0, 100, 20, 60), _box(20, -1, -1, 80, 21, 30))


def make_long_bridge() -> TopoDS_Shape:
    """Same 60mm gap in x, but 200mm long in y. The bridge runs across the gap
    (60), not along the tunnel (200)."""
    return _cut(_box(0, 0, 0, 100, 200, 60), _box(20, -1, -1, 80, 201, 30))


def make_enclosed_opening() -> TopoDS_Shape:
    """A cavity closed on all four sides, 60 wide in x and 160 in y. A slicer
    bridges the short way, so the span is 60."""
    return _cut(_box(0, 0, 0, 100, 200, 60), _box(20, 20, -1, 80, 180, 30))


def make_round_tunnel() -> TopoDS_Shape:
    """A cylindrical bore through a block. Its ceiling is a curved underside."""
    axis = gp_Ax2(gp_Pnt(50, -1, 30), gp_Dir(0, 1, 0))
    drill = BRepPrimAPI_MakeCylinder(axis, 10.0, 22.0).Shape()
    return _cut(_box(0, 0, 0, 100, 20, 60), drill)


def make_bridge_with_pillar() -> TopoDS_Shape:
    """make_bridge() plus a pillar under the middle of the span, not reaching the
    full depth, so it forms an inner wire on the underside face."""
    return _fuse(make_bridge(), _box(45, 5, 0, 55, 15, 30))


# =============================================================================


def _run(shape: TopoDS_Shape, **kwargs) -> dict:
    """Full analyzer pass over a shape."""
    return BridgeSpanAnalyzer().execute(shape, FaceIndex(shape), EdgeIndex(shape), **kwargs)


def _prime(
    analyzer: BridgeSpanAnalyzer,
    shape: TopoDS_Shape,
    print_dir: gp_Dir = gp_Dir(0, 0, 1),
) -> BridgeSpanAnalyzer:
    """Set up analyzer state exactly as execute() does, but without running the
    face loop, so individual stages can be called in isolation.

    Keep in sync with BridgeSpanAnalyzer.execute().
    """
    analyzer.resolve_prefs({})
    analyzer.print_orientation = print_dir
    analyzer.face_index = FaceIndex(shape)
    analyzer.bed_height = calculate_bed_height(shape, print_dir)
    analyzer.solid_classifier = BRepClass3d_SolidClassifier(shape)
    return analyzer


def _downward_face_at(shape: TopoDS_Shape, z: float, tol: float = 1e-3) -> TopoDS_Face:
    """The planar downward-facing face whose centre sits at height z.

    Raises rather than returning None, so a broken fixture fails loudly and the
    return type stays concrete for callers.
    """
    for face in FaceIndex(shape):
        u, v = get_face_uv_center(face)
        normal = get_face_uv_normal(face, u, v)
        if not normal or not normal.IsEqual(gp_Dir(0, 0, -1), 1e-4):
            continue
        if abs(BRepAdaptor_Surface(face).Value(u, v).Z() - z) < tol:
            return face
    raise AssertionError(f"fixture error: no downward-facing face at z={z}")


# =============================================================================


class TestBridgeCandidates(unittest.TestCase):
    def setUp(self):
        self.analyzer = BridgeSpanAnalyzer()

    def test_beam_underside_is_a_candidate(self):
        shape = make_bridge()
        _prime(self.analyzer, shape)
        self.assertTrue(self.analyzer._is_bridge_candidate(_downward_face_at(shape, 30.0)))

    def test_bed_face_is_not_a_candidate(self):
        shape = make_bridge()
        _prime(self.analyzer, shape)
        self.assertFalse(self.analyzer._is_bridge_candidate(_downward_face_at(shape, 0.0)))

    def test_bed_height_of_raised_box(self):
        self.analyzer.resolve_prefs({})
        self.analyzer.print_orientation = gp_Dir(0, 0, 1)
        self.assertAlmostEqual(
            calculate_bed_height(_box(0, 0, 10, 50, 50, 60), gp_Dir(0, 0, 1)), 10.0, places=3
        )

    def test_solid_box_has_no_bridges(self):
        """Bottom face is on the bed; nothing else faces down."""
        self.assertEqual(_run(_box(0, 0, 0, 100, 100, 100)), {})


# =============================================================================


class TestBridgeDirection(unittest.TestCase):
    def setUp(self):
        self.analyzer = BridgeSpanAnalyzer()

    def test_material_below_a_leg_top_is_detected(self):
        shape = make_bridge()
        _prime(self.analyzer, shape)
        self.assertTrue(self.analyzer._has_material_below(gp_Pnt(10, 10, 30)))

    def test_no_material_below_mid_span(self):
        shape = make_bridge()
        _prime(self.analyzer, shape)
        self.assertFalse(self.analyzer._has_material_below(gp_Pnt(50, 10, 30)))

    def test_only_the_two_leg_walls_give_directions(self):
        """The tunnel's open ends share a coplanar face with the legs but have no
        material beneath them, so only the x=20 and x=80 walls count."""
        shape = make_bridge()
        _prime(self.analyzer, shape)

        directions = self.analyzer._bridge_directions(_downward_face_at(shape, 30.0))
        self.assertEqual(len(directions), 2, f"expected 2 walls, got {len(directions)}")

    def test_direction_is_perpendicular_to_the_walls(self):
        """Walls run along y, so the bridge runs along x."""
        shape = make_bridge()
        _prime(self.analyzer, shape)

        for direction in self.analyzer._bridge_directions(_downward_face_at(shape, 30.0)):
            self.assertAlmostEqual(abs(direction.X()), 1.0, places=6)
            self.assertAlmostEqual(direction.Z(), 0.0, places=6)


# =============================================================================


class TestBridgeExtent(unittest.TestCase):
    def setUp(self):
        self.analyzer = BridgeSpanAnalyzer()

    def test_extent_along_x_of_the_beam_underside(self):
        shape = make_bridge()
        _prime(self.analyzer, shape)
        extent = self.analyzer._extent_along(_downward_face_at(shape, 30.0), gp_Dir(1, 0, 0))
        self.assertAlmostEqual(extent, 60.0, places=6)

    def test_extent_along_y_of_the_beam_underside(self):
        shape = make_bridge()
        _prime(self.analyzer, shape)
        extent = self.analyzer._extent_along(_downward_face_at(shape, 30.0), gp_Dir(0, 1, 0))
        self.assertAlmostEqual(extent, 20.0, places=6)


# =============================================================================


class TestBridgeSpanMeasurement(unittest.TestCase):
    def test_single_bridge_face_is_reported(self):
        data = _run(make_bridge())
        self.assertEqual(len(data), 1, f"expected exactly one bridging face, got {len(data)}")

    def test_span_matches_the_gap_width_exactly(self):
        """Vertex projection is exact, so no tolerance is needed."""
        span = next(iter(_run(make_bridge()).values()))
        self.assertAlmostEqual(span, 60.0, places=6)

    def test_span_is_across_the_gap_not_along_the_tunnel(self):
        span = next(iter(_run(make_long_bridge()).values()))
        self.assertAlmostEqual(span, 60.0, places=6)

    def test_enclosed_opening_bridges_the_short_way(self):
        """Walls on all four sides disagree on direction; the smallest wins."""
        span = next(iter(_run(make_enclosed_opening()).values()))
        self.assertAlmostEqual(span, 60.0, places=6)

    def test_wider_gap_gives_larger_span(self):
        narrow = _cut(_box(0, 0, 0, 100, 20, 60), _box(40, -1, -1, 60, 21, 30))
        self.assertLess(
            next(iter(_run(narrow).values())),
            next(iter(_run(make_bridge()).values())),
        )


# =============================================================================


class TestBridgeSpanOrientation(unittest.TestCase):
    def test_explicit_orientation_kwarg_matches_default(self):
        self.assertEqual(len(_run(make_bridge(), **{PRINT_DIR: gp_Dir(0, 0, 1)})), 1)

    def test_inverted_orientation_finds_no_bridge(self):
        """Printed upside down, the beam underside is a ceiling, not a bridge."""
        self.assertEqual(_run(make_bridge(), **{PRINT_DIR: gp_Dir(0, 0, -1)}), {})


# =============================================================================


class TestBridgeSpanLimitations(unittest.TestCase):
    def test_curved_underside_is_not_evaluated(self):
        """Only planar faces are candidates, so a round bore's ceiling is missed."""
        self.assertEqual(_run(make_round_tunnel()), {})

    def test_interior_pillar_does_not_give_the_true_gap(self):
        """A pillar mid-span should reduce the bridge to the wall-to-pillar gap
        (25mm here), but the span is measured across the whole face extent, so
        the pillar's own walls only change which direction is measured. This
        documents the approximation rather than endorsing it."""
        span = max(_run(make_bridge_with_pillar()).values())
        self.assertNotAlmostEqual(span, 25.0, places=1)


if __name__ == "__main__":
    unittest.main()
