# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The mold flow result in the 3D view.

Drawn as the contour tools draw theirs (gui/contour/scene.py): a mesh just in
front of the body, which stays visible underneath (pushed out along the
normals by the display offset preference), split into chunks so picking
stays fast. The results are per mesh node, so colors are per vertex (smooth
shading: blended, with normals split at sharp edges) or per triangle (flat).

Colors update in place, which is what animates the fill: set_time(t) grays
out everything the melt hasn't reached by time t. Weld and meld lines, air
traps and the last place to fill are drawn over it, each in its own switch.
"""

import math

import numpy as np
from pivy import coin

import FreeCADGui as Gui  # type: ignore

from ...app.contour.colormap import get_colormap
from ..contour.scene import ContourNode, _offset_vertices, _pref_display_offset_fraction
from ..utils.view import get_active_view
from ..visuals import DirectionIndicator

UNFILLED_RGB = (0.80, 0.81, 0.83)  # not reached (yet): light gray
NO_VALUE_RGB = (0.62, 0.62, 0.62)
WELD_RGB = (0.06, 0.06, 0.08)
TRAP_RGB = (0.88, 0.15, 0.78)
LAST_RGB = (0.06, 0.06, 0.08)
GATE_RGB = (0.98, 0.70, 0.08)
_CHUNK_NAME = "DFMMoldFlowChunk_"
_TARGET_PER_CHUNK = 2500


def _lut(colormap, n=256):
    fn = get_colormap(colormap)
    return np.array([fn(i / (n - 1)) for i in range(n)], float)


def _marker(kind, size, fallback):
    """FreeCAD's marker index (bigger sizes than plain Coin's), or Coin's."""
    try:
        return Gui.getMarkerIndex(kind, size)
    except Exception:
        return getattr(coin.SoMarkerSet, fallback)


class GateMarker(DirectionIndicator):
    """An arrow pointing into the part, its tip on the gate: the nozzle.

    The pull direction arrow's geometry, moved back along itself so it ends
    at the point instead of starting there."""

    def __init__(self, label):
        super().__init__(GATE_RGB, label)
        self._tip_at_base = False

    def show(self, base_pnt, direction):
        if self.view_node is None:
            self._tip_at_base = False  # (re)built from scratch below
        super().show(base_pnt, direction)
        if not self._tip_at_base and self.arrow_group is not None:
            try:
                # (see DirectionIndicator: a 20 long shaft then an 8 long head)
                self.arrow_group.getChild(0).translation.setValue(0, -18.0, 0)
                self._tip_at_base = True
            except Exception:
                pass

    def _update_label_position(self):
        # DirectionIndicator puts the badge just past the tip. This arrow is
        # moved back to end at the point, so measure from the other way: the
        # badge then sits just past the tail, on the side away from the part.
        d = self.direction
        if d is None:
            return super()._update_label_position()
        self.direction = (-d[0], -d[1], -d[2])
        try:
            super()._update_label_position()
        finally:
            self.direction = d


class MoldFlowScene:
    """One result drawn in the 3D view."""

    def __init__(self):
        self._root = None
        self._view = None
        self._material = None  # per vertex (smooth)
        self._chunk_materials = []  # per triangle, one per chunk (flat)
        self._chunk_tris = []  # the triangles (indices into the result) of each chunk
        self._name_to_chunk = {}
        self._switches = {}
        self._smooth = True
        self._orig = None  # drawn vertex -> result node
        self._draw_vertices = None
        self.result = None
        self._values = None
        self._style = None
        self._time = None

    # -- Building -----------------------------------------------------------------

    def build(self, result, smooth=True):
        """The scene graph for *result* (app.moldflow.analysis.MoldFlowResult).
        Colors come from recolor()."""
        self.result = result
        self._smooth = bool(smooth)
        nodes = result.nodes
        tris = result.tris
        p = nodes[tris]
        cross = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        normals = cross / np.maximum(np.linalg.norm(cross, axis=1)[:, None], 1e-300)
        diag = float(np.linalg.norm(nodes.max(0) - nodes.min(0)))
        self._offset = _pref_display_offset_fraction() * diag
        self._diag = diag
        vertices = _offset_vertices(nodes.tolist(), tris.tolist(), normals.tolist(), self._offset)
        tri_list = [tuple(t) for t in tris.tolist()]
        nrm_list = [tuple(n) for n in normals.tolist()]

        root = coin.SoSeparator()
        hints = coin.SoShapeHints()
        hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
        hints.shapeType = coin.SoShapeHints.UNKNOWN_SHAPE_TYPE
        hints.creaseAngle = 0.0
        root.addChild(hints)
        offset = coin.SoPolygonOffset()
        offset.factor = -1.5
        offset.units = -2.0
        root.addChild(offset)

        if self._smooth:
            # Split vertices at sharp edges so lighting blends along fillets
            # but not round corners (the contour tools' own split).
            zeros = [0.0] * len(tri_list)
            r_vertices, r_tris, _vv, r_normals = ContourNode._split_for_smoothing(
                vertices, tri_list, zeros, nrm_list, value_gap=None)
            orig = np.zeros(len(r_vertices), np.int64)
            rt = np.asarray(r_tris, np.int64)
            orig[rt.ravel()] = tris.ravel()
            self._orig = orig
        else:
            r_vertices, r_tris, r_normals = vertices, tri_list, None
            self._orig = np.arange(len(vertices))
        self._draw_vertices = np.asarray(r_vertices, float)
        self._draw_tris = np.asarray(r_tris, np.int64)

        coords = coin.SoCoordinate3()
        coords.point.setValues(0, len(r_vertices), r_vertices)
        root.addChild(coords)

        self._material = None
        self._chunk_materials = []
        if self._smooth:
            mat = coin.SoMaterial()
            root.addChild(mat)
            mbind = coin.SoMaterialBinding()
            mbind.value = coin.SoMaterialBinding.PER_VERTEX_INDEXED
            root.addChild(mbind)
            self._material = mat
            nrm = coin.SoNormal()
            nrm.vector.setValues(0, len(r_normals), r_normals)
            root.addChild(nrm)
            nbind = coin.SoNormalBinding()
            nbind.value = coin.SoNormalBinding.PER_VERTEX_INDEXED
            root.addChild(nbind)

        self._chunk_tris = []
        self._name_to_chunk = {}
        for i, idx in enumerate(self._partition(self._draw_vertices, self._draw_tris)):
            sep = coin.SoSeparator()
            if not self._smooth:
                mat = coin.SoMaterial()
                sep.addChild(mat)
                mbind = coin.SoMaterialBinding()
                mbind.value = coin.SoMaterialBinding.PER_FACE
                sep.addChild(mbind)
                self._chunk_materials.append(mat)
                cn = coin.SoNormal()
                cn.vector.setValues(0, len(idx), [nrm_list[k] for k in idx.tolist()])
                sep.addChild(cn)
                nb = coin.SoNormalBinding()
                nb.value = coin.SoNormalBinding.PER_FACE
                sep.addChild(nb)
            faces = coin.SoIndexedFaceSet()
            flat = np.hstack([self._draw_tris[idx], -np.ones((len(idx), 1), np.int64)]).ravel()
            faces.coordIndex.setValues(0, len(flat), flat.tolist())
            name = f"{_CHUNK_NAME}{i}"
            faces.setName(coin.SbName(name))
            sep.addChild(faces)
            root.addChild(sep)
            self._chunk_tris.append(idx)
            self._name_to_chunk[name] = i

        root.addChild(self._build_overlays(result))
        self._root = root
        return root

    @staticmethod
    def _partition(vertices, tris, target=_TARGET_PER_CHUNK):
        """Triangle indices of each chunk: a spatial grid (pick rays skip
        chunks by bounding box)."""
        n = len(tris)
        if n <= target:
            return [np.arange(n)]
        cent = vertices[tris].mean(axis=1)
        lo = cent.min(0)
        span = np.maximum(cent.max(0) - lo, 1e-9)
        grid = max(1, round((n / target) ** (1.0 / 3.0)))
        cell = np.minimum(grid - 1, ((cent - lo) / span * grid).astype(int))
        key = cell[:, 0] * grid * grid + cell[:, 1] * grid + cell[:, 2]
        order = np.argsort(key, kind="stable")
        bounds = np.nonzero(np.diff(key[order]))[0] + 1
        return [chunk for chunk in np.split(order, bounds) if len(chunk)]

    def _build_overlays(self, result):
        group = coin.SoSeparator()
        light = coin.SoLightModel()
        light.model = coin.SoLightModel.BASE_COLOR
        group.addChild(light)

        nodes = result.nodes
        from scipy.spatial import cKDTree

        tree = cKDTree(nodes)
        tris = result.tris
        p = nodes[tris]
        cross = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        nn = np.zeros_like(nodes)
        for k in range(3):
            np.add.at(nn, tris[:, k], cross)
        nn /= np.maximum(np.linalg.norm(nn, axis=1)[:, None], 1e-300)
        lift = 2.5 * self._offset + 1e-4 * self._diag

        def lifted(points):
            pts = np.asarray(points, float).reshape(-1, 3)
            if not len(pts):
                return pts
            _d, k = tree.query(pts)
            return pts + nn[k] * lift

        # weld lines (solid) and meld lines (dashed)
        welds = coin.SoSwitch()
        wsep = coin.SoSeparator()
        welds.addChild(wsep)
        mat = coin.SoMaterial()
        mat.diffuseColor = WELD_RGB
        wsep.addChild(mat)
        style = coin.SoDrawStyle()
        style.lineWidth = 3.0
        wsep.addChild(style)
        if result.welds:
            seg = lifted([w[0:3] for w in result.welds] + [w[3:6] for w in result.welds])
            m = len(result.welds)
            pts = np.empty((2 * m, 3))
            pts[0::2] = seg[:m]
            pts[1::2] = seg[m:]
            c = coin.SoCoordinate3()
            c.point.setValues(0, len(pts), pts.tolist())
            wsep.addChild(c)
            ls = coin.SoLineSet()
            ls.numVertices.setValues(0, m, [2] * m)
            wsep.addChild(ls)
        if result.melds:
            msep = coin.SoSeparator()
            dstyle = coin.SoDrawStyle()
            dstyle.lineWidth = 2.5
            dstyle.linePattern = 0xF0F0
            msep.addChild(dstyle)
            all_pts, counts = [], []
            for line in result.melds:
                lp = lifted(line)
                all_pts.extend(lp.tolist())
                counts.append(len(lp))
            c = coin.SoCoordinate3()
            c.point.setValues(0, len(all_pts), all_pts)
            msep.addChild(c)
            ls = coin.SoLineSet()
            ls.numVertices.setValues(0, len(counts), counts)
            msep.addChild(ls)
            wsep.addChild(msep)
        group.addChild(welds)
        self._switches["welds"] = welds

        # air traps and the last place to fill: markers a constant size on screen
        for key, points, rgb, marker in (
                ("traps", [t[:3] for t in result.traps], TRAP_RGB,
                 _marker("CIRCLE_FILLED", 11, "CIRCLE_FILLED_9_9")),
                ("last", [result.last], LAST_RGB, _marker("CIRCLE_LINE", 15, "CIRCLE_LINE_9_9"))):
            sw = coin.SoSwitch()
            sep = coin.SoSeparator()
            sw.addChild(sep)
            if points:
                m = coin.SoMaterial()
                m.diffuseColor = rgb
                sep.addChild(m)
                pts = lifted(points)
                c = coin.SoCoordinate3()
                c.point.setValues(0, len(pts), pts.tolist())
                sep.addChild(c)
                ms = coin.SoMarkerSet()
                ms.markerIndex = marker
                sep.addChild(ms)
            group.addChild(sw)
            self._switches[key] = sw
        return group

    # -- Showing ---------------------------------------------------------------------

    def attach(self):
        if self._root is None:
            return
        view = get_active_view()
        if view is None:
            raise RuntimeError("No active 3D view to show the result in.")
        self._view = view
        view.getSceneGraph().addChild(self._root)

    def remove(self):
        if self._view is not None and self._root is not None:
            try:
                self._view.getSceneGraph().removeChild(self._root)
            except Exception:
                pass
        self._root = None
        self._view = None

    def set_overlay_visible(self, key, visible):
        sw = self._switches.get(key)
        if sw is not None:
            sw.whichChild.setValue(-3 if visible else -1)

    # -- Colors ------------------------------------------------------------------------

    def recolor(self, values, low, high, colormap="Viridis", categories=None, muted=(),
                time_cut=None, fill_time=None):
        """Color by *values* (one per result node; nan: no value).

        *categories* ({code: rgb}) draws category codes instead of a color
        map (muted codes gray). With *time_cut*, nodes the melt reaches after
        that time (by *fill_time*) are drawn unfilled."""
        self._values = np.asarray(values, float)
        self._style = (low, high, colormap, categories, frozenset(muted))
        self._time = time_cut
        self._fill_time = fill_time
        self._apply_colors()

    def set_time(self, t):
        """Show the fill at time t (None: all of it)."""
        if self._values is None:
            return
        self._time = t
        self._apply_colors()

    def node_colors(self):
        """rgb per result node, for the current coloring and time."""
        v = self._values
        low, high, colormap, categories, muted = self._style
        n = len(v)
        rgb = np.empty((n, 3))
        rgb[:] = NO_VALUE_RGB
        ok = np.isfinite(v)
        if categories:
            for code, color in categories.items():
                sel = ok & (np.rint(v) == code)
                rgb[sel] = NO_VALUE_RGB if code in muted else color
        else:
            lut = _lut(colormap)
            span = high - low
            t = np.zeros(n) if abs(span) < 1e-300 else (v - low) / span
            idx = np.clip(np.nan_to_num(t, nan=0.0) * (len(lut) - 1), 0, len(lut) - 1)
            rgb[ok] = lut[np.rint(idx[ok]).astype(int)]
        if self._time is not None and self._fill_time is not None:
            ft = self._fill_time
            pending = ~(np.isfinite(ft) & (ft <= self._time))
            rgb[pending] = UNFILLED_RGB
        return rgb

    def _apply_colors(self):
        if self._root is None and self._material is None and not self._chunk_materials:
            return
        rgb = self.node_colors()
        if self._smooth and self._material is not None:
            colors = rgb[self._orig]
            self._material.diffuseColor.setValues(0, len(colors), colors.tolist())
            return
        tris = self.result.tris
        tri_rgb = rgb[tris].mean(axis=1)
        if self._time is not None and self._fill_time is not None:
            # flat: a triangle shows filled once all its corners are
            ft = self._fill_time[tris]
            pending = ~np.all(np.isfinite(ft) & (ft <= self._time), axis=1)
            tri_rgb[pending] = UNFILLED_RGB
        for mat, idx in zip(self._chunk_materials, self._chunk_tris):
            colors = tri_rgb[idx]
            mat.diffuseColor.setValues(0, len(colors), colors.tolist())

    # -- Picking --------------------------------------------------------------------------

    def pick_value(self, picked):
        """(key, value, point) under a Coin picked point, or None: the value
        at the point, interpolated between the triangle's corners."""
        if picked is None or self._values is None:
            return None
        try:
            path = picked.getPath()
            tail = path.getTail() if path is not None else None
            if tail is None:
                return None
            chunk = self._name_to_chunk.get(tail.getName().getString())
            if chunk is None:
                return None
            detail = picked.getDetail()
            if detail is None or not detail.isOfType(coin.SoFaceDetail.getClassTypeId()):
                return None
            face = coin.cast(detail, "SoFaceDetail").getFaceIndex()
        except Exception:
            return None
        idx = self._chunk_tris[chunk]
        if face < 0 or face >= len(idx):
            return None
        tri = int(idx[face])
        point = picked.getPoint()
        pt = np.array([point[0], point[1], point[2]], float)
        corners = self._draw_vertices[self._draw_tris[tri]]
        w = _barycentric(pt, corners)
        vals = self._values[self.result.tris[tri]]
        if not np.all(np.isfinite(vals)):
            finite = vals[np.isfinite(vals)]
            value = float(finite.mean()) if len(finite) else math.nan
        elif self._style is not None and self._style[3]:
            value = float(vals[int(np.argmax(w))])  # categories don't interpolate
        else:
            value = float(np.dot(w, vals))
        return ((chunk, face), value, (pt[0], pt[1], pt[2]))


def _barycentric(p, tri):
    a, b, c = tri
    v0, v1, v2 = b - a, c - a, p - a
    d00, d01, d11 = v0 @ v0, v0 @ v1, v1 @ v1
    d20, d21 = v2 @ v0, v2 @ v1
    den = d00 * d11 - d01 * d01
    if abs(den) < 1e-300:
        return np.array([1 / 3, 1 / 3, 1 / 3])
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    out = np.clip(np.array([1.0 - v - w, v, w]), 0.0, 1.0)
    return out / max(out.sum(), 1e-300)
