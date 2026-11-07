# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Regions of a categorical contour, and a camera that visits them.

A region is a connected patch of triangles of one category (triangles that
share a mesh node). The legend steps through a category's regions, largest
first, and the camera turns to face each one, like the results panel's
"zoom and face" on a finding.
"""

import math

from ..results.camera import ResultsCamera
from ..report.capture import _add, _mul, best_direction, bound_box_corners, frame, rotate
from ..utils.view import get_active_view
from ...app.prefs import dfm_params


class Region:
    __slots__ = ("code", "area", "corners", "normal", "count")

    def __init__(self, code, area, corners, normal, count):
        self.code = code
        self.area = area
        self.corners = corners
        self.normal = normal
        self.count = count


def _tri_area_normal(vertices, tri):
    a, b, c = (vertices[i] for i in tri)
    e1 = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    e2 = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    cx = e1[1] * e2[2] - e1[2] * e2[1]
    cy = e1[2] * e2[0] - e1[0] * e2[2]
    cz = e1[0] * e2[1] - e1[1] * e2[0]
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


def triangle_areas(vertices, triangles):
    return [_tri_area_normal(vertices, t) for t in triangles]


def area_shares(values, areas):
    """{category code: share of the total area (0..1)}."""
    total = sum(areas) or 1.0
    out = {}
    for v, a in zip(values, areas):
        if math.isfinite(v):
            code = int(round(v))
            out[code] = out.get(code, 0.0) + a
    return {k: a / total for k, a in out.items()}


class CategoryRegions:
    """Connected regions per category, worked out once on first use."""

    def __init__(self, vertices, triangles, values, normals, areas=None):
        self._vertices = vertices
        self._triangles = triangles
        self._values = values
        self._normals = normals
        self._areas = areas
        self._by_code = None

    def regions(self, code):
        if self._by_code is None:
            self._by_code = self._compute()
        return self._by_code.get(int(code), [])

    def _compute(self):
        tris, verts, vals = self._triangles, self._vertices, self._values
        n = len(tris)
        codes = [int(round(v)) if math.isfinite(v) else None for v in vals]
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        seen = {}
        for ti, tri in enumerate(tris):
            code = codes[ti]
            if code is None:
                continue
            for vi in tri:
                key = (vi, code)
                other = seen.get(key)
                if other is None:
                    seen[key] = ti
                else:
                    ra, rb = find(ti), find(other)
                    if ra != rb:
                        parent[ra] = rb

        areas = self._areas or triangle_areas(verts, tris)
        groups = {}
        for ti in range(n):
            if codes[ti] is None:
                continue
            groups.setdefault(find(ti), []).append(ti)

        by_code = {}
        for members in groups.values():
            code = codes[members[0]]
            area = 0.0
            nx = ny = nz = 0.0
            lo = [math.inf] * 3
            hi = [-math.inf] * 3
            for ti in members:
                a = areas[ti]
                area += a
                nrm = self._normals[ti] if self._normals is not None else None
                if nrm is not None:
                    nx += nrm[0] * a
                    ny += nrm[1] * a
                    nz += nrm[2] * a
                for vi in tris[ti]:
                    p = verts[vi]
                    for k in range(3):
                        if p[k] < lo[k]:
                            lo[k] = p[k]
                        if p[k] > hi[k]:
                            hi[k] = p[k]
            length = math.sqrt(nx * nx + ny * ny + nz * nz)
            normal = (nx / length, ny / length, nz / length) if length > 1e-12 else None
            corners = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                       for z in (lo[2], hi[2])]
            by_code.setdefault(code, []).append(Region(code, area, corners, normal, len(members)))
        for regions in by_code.values():
            regions.sort(key=lambda r: r.area, reverse=True)
        return by_code


class RegionCamera(ResultsCamera):
    """Turns to face a region and frames it, with some of the part around it."""

    def show(self, region):
        view = get_active_view()
        shape = getattr(self.target_object, "Shape", None)
        if view is None or shape is None or region is None:
            return
        try:
            cam = view.getCameraNode()
        except Exception:
            cam = None
        if cam is None:
            return

        q0 = tuple(cam.orientation.getValue().getValue())
        dir0 = rotate(q0, (0.0, 0.0, -1.0))
        ortho = "Orthographic" in str(cam.getTypeId().getName())
        focal0 = float(cam.focalDistance.getValue())
        pos0 = tuple(cam.position.getValue().getValue())
        center0 = _add(pos0, _mul(dir0, focal0))
        height0 = float(cam.height.getValue()) if ortho else 0.0

        model = bound_box_corners(shape.BoundBox)
        padding = max(dfm_params().GetFloat("ResultsCameraPadding", 1.7), 1.2)
        direction = best_direction(region.normal)
        try:
            w, h = view.getSize()
            aspect = w / max(h, 1)
        except Exception:
            aspect = 1.0
        angle = math.pi / 4
        if not ortho:
            try:
                angle = float(cam.heightAngle.getValue())
            except Exception:
                pass
        setup = frame(direction, region.corners, model, aspect, padding, ortho, angle,
                      (0.0, 0.0, 1.0), 0.35)
        dir1 = rotate(setup.orientation, (0.0, 0.0, -1.0))
        center1 = _add(setup.position, _mul(dir1, setup.focal_distance))
        self._animate(cam, ortho, (q0, center0, focal0, height0),
                      (setup.orientation, center1, setup.focal_distance, setup.height))
