# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The part's surface mesh, seen as a dual domain.

The contour tools' gmsh mesh (app/contour/meshing.py) is turned outward, and
then each triangle gets its wall's thickness, and each node the node facing
it across the wall (on the other skin), so the two skins of a wall can fill
together (as Moldflow's "Dual Domain" does):

* thickness: at each triangle's centroid, the largest ball inside the part
  touching it there (the "shrinking ball" medial axis method); for a
  triangle facing another skin, the distance across to it;
* caps: triangles on the rim of a wall (the narrow end face of a plate),
  which face no other skin. They take the thickness of the wall they cap,
  and take no part in the flow (the skins hold that wall's volume).
"""

import numpy as np
from scipy.spatial import cKDTree


class FlowMesh:
    """Nodes (mm) and triangles, outward, with thickness and skin pairs."""

    def __init__(self, nodes, tris):
        self.nodes = np.asarray(nodes, float).reshape(-1, 3)
        self.tris = np.asarray(tris, np.int64).reshape(-1, 3)
        self.tri_cap = np.zeros(len(self.tris), bool)
        self.pairs = np.zeros((0, 2), np.int64)
        self._geometry()

    @classmethod
    def from_uniform_mesh(cls, mesh):
        """From app.contour.meshing.UniformMesh (gmsh shares nodes between faces)."""
        flow = cls(mesh.vertices, mesh.triangles)
        flow._drop_degenerate()
        return flow.orient_outward()

    def _geometry(self):
        p = self.nodes[self.tris]
        cross = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        self.tri_area = 0.5 * np.linalg.norm(cross, axis=1)
        self.tri_normal = cross / np.maximum(2 * self.tri_area[:, None], 1e-300)
        normals = np.zeros_like(self.nodes)
        for k in range(3):
            np.add.at(normals, self.tris[:, k], cross)
        self.node_normal = normals / np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-300)
        self.node_area = np.zeros(len(self.nodes))
        for k in range(3):
            np.add.at(self.node_area, self.tris[:, k], self.tri_area / 3.0)

    def _drop_degenerate(self):
        keep = self.tri_area > 1e-12 * max(float(np.median(self.tri_area)), 1e-30)
        if not keep.all():
            self.tris = self.tris[keep]
            self._geometry()

    @property
    def element_size(self):
        return float(np.sqrt(2.3 * np.median(self.tri_area)))

    # -- Orientation -------------------------------------------------------------

    def orient_outward(self):
        """Make all triangles wind the same way, then point outward."""
        tris = self.tris
        edges = {}
        for t, (a, b, c) in enumerate(tris.tolist()):
            for u, v in ((a, b), (b, c), (c, a)):
                edges.setdefault((u, v) if u < v else (v, u), []).append(t)
        flipped = np.zeros(len(tris), bool)
        seen = np.zeros(len(tris), bool)
        tl = tris.tolist()

        def directed(t):
            a, b, c = tl[t]
            if flipped[t]:
                a, b = b, a
            return ((a, b), (b, c), (c, a))

        for start in range(len(tris)):
            if seen[start]:
                continue
            seen[start] = True
            stack = [start]
            component = [start]
            while stack:
                t = stack.pop()
                for u, v in directed(t):
                    for o in edges[(u, v) if u < v else (v, u)]:
                        if o == t or seen[o]:
                            continue
                        # consistent neighbours run the shared edge the other way
                        flipped[o] = (u, v) in directed(o)
                        seen[o] = True
                        stack.append(o)
                        component.append(o)
            # each closed body: positive signed volume means outward
            comp = np.array(component)
            t3 = tris[comp].copy()
            t3[flipped[comp]] = t3[flipped[comp]][:, [1, 0, 2]]
            p = self.nodes[t3]
            volume = np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum() / 6.0
            if volume < 0:
                flipped[comp] = ~flipped[comp]
        self.tris = tris.copy()
        self.tris[flipped] = self.tris[flipped][:, [1, 0, 2]]
        self._geometry()
        return self

    # -- Wall thickness --------------------------------------------------------------

    def measure_thickness(self, size=None):
        """Wall thickness per triangle (and per node, area-weighted)."""
        p = self.nodes[self.tris]
        cent = p.mean(axis=1)
        bary = [(2 / 3, 1 / 6, 1 / 6), (1 / 6, 2 / 3, 1 / 6), (1 / 6, 1 / 6, 2 / 3)]
        extra = [w[0] * p[:, 0] + w[1] * p[:, 1] + w[2] * p[:, 2] for w in bary]
        cloud = np.vstack([self.nodes, cent] + extra)
        self.tri_ball = 2.0 * _shrinking_ball(cent, -self.tri_normal, cloud)
        self.tri_centroid = cent

        # Which triangles face another skin across the wall.
        tree = cKDTree(cent)
        across = cent - self.tri_normal * self.tri_ball[:, None]
        dist, j = tree.query(across, k=1)
        facing = np.einsum("ij,ij->i", self.tri_normal, self.tri_normal[j]) < -0.5
        self.tri_faced = facing & (dist < 0.5 * self.tri_ball + 1e-9)

        thick = self.tri_ball.copy()
        # Faced triangles: the distance across to the other skin's plane. Near
        # an edge the ball is cut short by the corner (a 2 mm plate reads
        # 1.3 mm there), which would slow the flow along every edge (h^3).
        f = self.tri_faced
        across_d = np.abs(np.einsum("ij,ij->i", cent[f] - cent[j[f]], self.tri_normal[j[f]]))
        thick[f] = np.clip(across_d, self.tri_ball[f], 2.0 * self.tri_ball[f])
        # caps: the rim of a wall, whose volume the two skins already hold
        self.tri_cap = np.zeros(len(self.tris), bool)
        faced = np.nonzero(self.tri_faced)[0]
        if len(faced):
            ftree = cKDTree(cent[faced])
            lonely = np.nonzero(~self.tri_faced)[0]
            reach = 2.0 * (size if size else self.element_size)
            k = min(8, len(faced))
            d, idx = ftree.query(cent[lonely], k=k)
            d = d.reshape(len(lonely), -1)
            idx = idx.reshape(len(lonely), -1)
            for row, t in enumerate(lonely):
                near = idx[row][d[row] <= max(reach, 1.5 * self.tri_ball[t])]
                if len(near):
                    thick[t] = np.median(thick[faced[near]])
                    self.tri_cap[t] = True
        # A triangle near an edge may fail the test with its own (small) ball
        # but face the other skin across the wall's thickness: not a cap.
        across = cent - self.tri_normal * thick[:, None]
        dist, j = tree.query(across, k=1)
        facing = np.einsum("ij,ij->i", self.tri_normal, self.tri_normal[j]) < -0.5
        self.tri_cap &= ~(facing & (dist < 0.5 * thick + 1e-9))
        self.tri_thickness = thick

        node_t = np.zeros(len(self.nodes))
        for k in range(3):
            np.add.at(node_t, self.tris[:, k], thick * self.tri_area)
        self.thickness = node_t / np.maximum(self.node_area * 3.0, 1e-300)
        return self.tri_thickness

    def skin_normals(self):
        """Node normals from the skins only (not a wall's rim): a node on the
        edge of a wall faces the way its skin does, not half way round."""
        p = self.nodes[self.tris]
        cross = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        cross[self.tri_cap] = 0.0
        normals = np.zeros_like(self.nodes)
        for k in range(3):
            np.add.at(normals, self.tris[:, k], cross)
        length = np.linalg.norm(normals, axis=1)
        out = self.node_normal.copy()
        ok = length > 0
        out[ok] = normals[ok] / length[ok, None]
        return out

    def pair_skins(self, tolerance=0.35, max_angle_deg=60.0):
        """Pairs of nodes facing each other across a wall: (n, 2) array."""
        tree = cKDTree(self.nodes)
        normal = self.skin_normals()
        across = self.nodes - normal * self.thickness[:, None]
        dist, j = tree.query(across, k=1)
        cos_lim = -np.cos(np.radians(max_angle_deg))
        facing = np.einsum("ij,ij->i", normal, normal[j]) < cos_lim
        good = facing & (dist < tolerance * self.thickness) & (j != np.arange(len(j)))
        i = np.nonzero(good)[0]
        if len(i) == 0:
            self.pairs = np.zeros((0, 2), np.int64)
        else:
            self.pairs = np.unique(np.sort(np.stack([i, j[good]], 1), axis=1), axis=0)
        return self.pairs

    def prepare(self, size=None):
        """Thickness and skin pairs: everything the solver needs."""
        self.measure_thickness(size)
        self.pair_skins()
        return self

    # -- Neighbours ----------------------------------------------------------------------

    def edges(self):
        e = np.vstack([self.tris[:, [0, 1]], self.tris[:, [1, 2]], self.tris[:, [2, 0]]])
        return np.unique(np.sort(e, axis=1), axis=0)

    def rim_nodes(self):
        rim = np.zeros(len(self.nodes), bool)
        rim[self.tris[self.tri_cap].ravel()] = True
        return rim


def _shrinking_ball(points, inward, cloud, iterations=60):
    """Radius of the largest ball touching each point from the inside."""
    tree = cKDTree(cloud)
    diag = np.linalg.norm(cloud.max(0) - cloud.min(0))
    n = len(points)
    radius = np.full(n, 0.5 * diag)
    active = np.arange(n)
    eps = 1e-7 * diag
    for _ in range(iterations):
        if len(active) == 0:
            break
        x = points[active]
        m = inward[active]
        centre = x + m * radius[active, None]
        dist, idx = tree.query(centre, k=1)
        q = cloud[idx]
        d = q - x
        own = np.linalg.norm(d, axis=1) < eps
        done = own | (dist >= radius[active] - eps)
        denom = 2.0 * np.einsum("ij,ij->i", m, d)
        ok = (~done) & (denom > 1e-12)
        new_r = np.where(ok, np.einsum("ij,ij->i", d, d) / np.where(ok, denom, 1.0),
                         radius[active])
        shrinking = ok & (new_r < radius[active] - eps)
        radius[active[shrinking]] = new_r[shrinking]
        active = active[shrinking]
    return radius
