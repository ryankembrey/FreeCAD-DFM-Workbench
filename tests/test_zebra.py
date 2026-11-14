# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Zebra Stripes: the mesh (app/contour/zebra.py, on parts built with OCP)
and the stripe math of both modes."""

import unittest

import numpy as np

import dfm_test_support  # noqa: F401  (FreeCAD stand-ins, sys.path)

from freecad.DFM.app.contour import zebra as Z
from freecad.DFM.core.recognition.testing import shapes as B


def _outward(mesh, inside):
    """Share of triangles whose normal points out of the part (*inside(points) -> bool array*)."""
    tri = mesh.triangles
    centre = mesh.points[tri].mean(axis=1)
    normal = mesh.normals[tri].sum(axis=1)
    normal /= np.linalg.norm(normal, axis=1)[:, None]
    return 1.0 - np.mean(inside(centre + 0.05 * normal))


class TestMesh(unittest.TestCase):
    def setUp(self):
        # A 60 x 40 x 20 block with an 8 mm hole through it.
        self.shape = B.cut(B.box(0, 0, 0, 60, 40, 20), B.cylinder((30, 20, -1), 8, 30))

        def inside(p):
            return ((p[:, 0] > 0) & (p[:, 0] < 60) & (p[:, 1] > 0) & (p[:, 1] < 40) & (p[:, 2] > 0)
                    & (p[:, 2] < 20) & (np.hypot(p[:, 0] - 30, p[:, 1] - 20) > 8))

        self.inside = inside

    def test_normals_point_out_and_match_the_winding(self):
        mesh = Z.tessellate(self.shape, "Fine")
        self.assertEqual(_outward(mesh, self.inside), 1.0)
        a, b, c = (mesh.points[mesh.triangles[:, k]] for k in range(3))
        facet = np.cross(b - a, c - a)
        self.assertTrue(np.all(np.einsum("ij,ij->i", facet, mesh.normals[mesh.triangles[:, 0]]) > 0))
        self.assertTrue(np.allclose(np.linalg.norm(mesh.normals, axis=1), 1.0))

    def test_faces_keep_their_own_vertices_at_an_edge(self):
        """A corner point appears once per face, each with that face's normal:
        that is what lets the stripes break at a crease."""
        mesh = Z.tessellate(self.shape, "Normal")
        corner = np.all(np.isclose(mesh.points, (60, 40, 20)), axis=1)
        normals = {tuple(np.round(n, 6)) for n in mesh.normals[corner]}
        self.assertEqual(normals, {(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)})

    def test_finer_quality_has_more_triangles(self):
        counts = [Z.tessellate(B.cylinder((0, 0, 0), 10, 30), q).triangle_count for q in Z.QUALITY]
        self.assertEqual(counts, sorted(counts))
        self.assertLess(counts[0], counts[-1])

    def test_only_the_chosen_faces(self):
        whole = Z.tessellate(self.shape, "Normal")
        some = Z.tessellate(self.shape, "Normal", faces=[1])
        self.assertLess(some.triangle_count, whole.triangle_count)
        self.assertGreater(some.triangle_count, 0)
        self.assertTrue(some.edges)
        self.assertLess(len(some.edges), len(whole.edges))

    def test_edges_follow_the_faces(self):
        mesh = Z.tessellate(B.box(0, 0, 0, 10, 10, 10), "Normal")
        self.assertEqual(len(mesh.edges), 12)
        for points, normals in mesh.edges:
            self.assertEqual(points.shape, normals.shape)
            # An edge of a cube: the mean of two face normals, 45 degrees off each.
            self.assertTrue(np.allclose(np.abs(normals).max(axis=1), 2 ** -0.5))


class TestStripes(unittest.TestCase):
    def test_fixed_is_the_angle_to_the_direction(self):
        normals = np.array([[0, 0, 1.0], [1, 0, 0], [0, 0, -1], [0, 2 ** -0.5, 2 ** -0.5]])
        t = Z.fixed_coordinates(normals, (0, 0, 2), 20)
        self.assertTrue(np.allclose(t - Z.PHASE, [0, 10, 20, 5]))

    def test_view_follows_the_camera(self):
        normals = np.array([[0, 0, 1.0], [2 ** -0.5, 0, 2 ** -0.5]])
        look, right = (0, 0, -1), (1, 0, 0)
        t = Z.view_coordinates(normals, look, right, 20) - Z.PHASE
        # A face toward the camera reflects the view straight back: 90 deg from the right axis.
        self.assertAlmostEqual(t[0], 10.0)
        self.assertAlmostEqual(t[1], 0.0)  # turned 45 deg: reflected along +x, 0 deg from it
        # Turning the camera moves the stripes; the fixed ones would not.
        turned = Z.view_coordinates(normals, (0, -1, 0), right, 20)
        self.assertFalse(np.allclose(turned, t))

    def test_perspective_uses_each_vertex_ray(self):
        normals = np.array([[0, 0, 1.0], [0, 0, 1.0]])
        points = np.array([[0, 0, 0.0], [50, 0, 0]])
        t = Z.view_coordinates(normals, (0, 0, -1), (1, 0, 0), 20, points, (0, 0, 100)) - Z.PHASE
        self.assertAlmostEqual(t[0], 10.0)
        self.assertNotAlmostEqual(t[1], 10.0)

    def test_tangent_join_keeps_stripes_continuous_and_a_crease_breaks_them(self):
        """On a fillet the two faces share their normal along the edge (G1), so
        the stripe coordinate matches on both sides; at a sharp edge it jumps."""
        block = B.box(0, 0, 0, 40, 40, 20)
        filleted = Z.tessellate(B.fillet(block, 6, B.edges_of(block)), "Fine")
        sharp = Z.tessellate(block, "Fine")

        def worst_jump(mesh):
            t = Z.fixed_coordinates(mesh.normals, (1, 0, 0), 20)
            key = np.round(mesh.points, 6)
            _, inverse = np.unique(key, axis=0, return_inverse=True)
            jumps = [np.ptp(t[inverse.ravel() == g]) for g in np.unique(inverse)]
            return max(jumps)

        self.assertGreater(worst_jump(sharp), 1.0)
        self.assertLess(worst_jump(filleted), 0.01)

    def test_round_angles_land_mid_stripe(self):
        """A flat face at 0 or 90 degrees sits in the middle of the light
        color, not on a stripe's edge, so it shows one clean color rather than
        a filtered gray."""
        t = Z.fixed_coordinates(np.array([[0, 0, 1.0], [1, 0, 0]]), (0, 0, 1), 20)
        self.assertTrue(np.allclose(t % 1.0, 0.75))

    def test_stripe_image(self):
        row = Z.stripe_image(0.25, (0, 0, 0), (255, 255, 255), size=8)
        self.assertEqual(len(row), 24)
        self.assertEqual(row[:6], bytes(6))
        self.assertEqual(row[6:], bytes([255] * 18))


if __name__ == "__main__":
    unittest.main()
