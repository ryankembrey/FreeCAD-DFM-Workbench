# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Coin3d overlay for a per-triangle scalar field (any contour tool).

The mesh is split into a spatial grid of chunk separators so Coin prunes pick
rays by bounding box, keeping hover picking sub-linear even on ultra-fine
meshes. Colors are per triangle and update in place. A module-level registry
keeps live overlays referenced so they persist after a dialog closes.
"""

from pivy import coin

import math
from collections import defaultdict

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ...app.contour.colormap import value_to_color, DEFAULT_COLORMAP


_TARGET_PER_CHUNK = 2500
_NAME_PREFIX = "DFMContourChunk_"

# Blend colors only across edges smoother than this; sharper edges (corners,
# chamfers) stay crisp so a draft jump from 0 to 90 degrees is not smeared.
_CREASE_COS = math.cos(math.radians(35.0))


def _partition(vertices, triangles, values, normals, target=_TARGET_PER_CHUNK):
    n = len(triangles)
    if n <= target:
        return [{"tris": triangles, "values": values, "normals": normals}]

    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    min_x, span_x = min(xs), max(max(xs) - min(xs), 1e-9)
    min_y, span_y = min(ys), max(max(ys) - min(ys), 1e-9)
    min_z, span_z = min(zs), max(max(zs) - min(zs), 1e-9)
    grid = max(1, round((n / target) ** (1.0 / 3.0)))

    def cell(v, lo, span):
        return min(grid - 1, max(0, int((v - lo) / span * grid)))

    buckets = {}
    for i, (a, b, c) in enumerate(triangles):
        cx = (vertices[a][0] + vertices[b][0] + vertices[c][0]) / 3.0
        cy = (vertices[a][1] + vertices[b][1] + vertices[c][1]) / 3.0
        cz = (vertices[a][2] + vertices[b][2] + vertices[c][2]) / 3.0
        key = (cell(cx, min_x, span_x), cell(cy, min_y, span_y), cell(cz, min_z, span_z))
        buckets.setdefault(key, []).append(i)

    chunks = []
    for indices in buckets.values():
        chunks.append(
            {
                "tris": [triangles[i] for i in indices],
                "values": [values[i] for i in indices],
                "normals": [normals[i] for i in indices],
            }
        )
    return chunks


class ContourNode:
    """One chunked colored mesh overlay tied to one target object."""

    def __init__(self, target_object):
        self.target_object = target_object
        self._sep = None
        self._chunks = []  # [{'material':..., 'values':[...]}]
        self._name_to_chunk = {}
        self._view = None
        self._original_visibility = None
        self._smooth = False
        self._global_material = None
        self._vertex_values = None

    @staticmethod
    def _split_for_smoothing(
        vertices, triangles, values, normals, crease_cos=_CREASE_COS, value_gap=None
    ):
        """Duplicate vertices across sharp edges so smoothing groups blend
        within a surface (fillets) but stay crisp across creases (corners).

        Two triangles blend only if their normals agree within the crease AND
        their values are within value_gap (when given). The value check is what
        stops, e.g., +90 and -89 degrees blending, and it works for any measure
        (thickness in mm included) since the gap is passed in by the caller.

        Returns (new_vertices, new_triangles, per_vertex_values). Triangles keep
        their order, so per-triangle values/normals still line up by index.
        """
        incident = defaultdict(list)
        for ti, tri in enumerate(triangles):
            for corner in tri:
                incident[corner].append(ti)

        new_vertices = [tuple(v) for v in vertices]
        vertex_values = [0.0] * len(new_vertices)
        vertex_normals = [(0.0, 0.0, 1.0)] * len(new_vertices)
        tris = [list(t) for t in triangles]

        for v, tlist in incident.items():
            # Union incident triangles that share a smooth surface and value.
            parent = {ti: ti for ti in tlist}

            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            for i in range(len(tlist)):
                ni = normals[tlist[i]]
                if ni is None:
                    continue
                vi = values[tlist[i]]
                for j in range(i + 1, len(tlist)):
                    nj = normals[tlist[j]]
                    if nj is None:
                        continue
                    if ni[0] * nj[0] + ni[1] * nj[1] + ni[2] * nj[2] < crease_cos:
                        continue
                    if value_gap is not None and abs(vi - values[tlist[j]]) > value_gap:
                        continue
                    parent[find(tlist[i])] = find(tlist[j])

            clusters = defaultdict(list)
            for ti in tlist:
                clusters[find(ti)].append(ti)

            first = True
            for group in clusters.values():
                mean_val = sum(values[ti] for ti in group) / len(group)
                # Average the group's face normals for smooth (Gouraud) lighting.
                sx = sy = sz = 0.0
                for ti in group:
                    nn = normals[ti]
                    if nn is not None:
                        sx += nn[0]
                        sy += nn[1]
                        sz += nn[2]
                mag = math.sqrt(sx * sx + sy * sy + sz * sz)
                mean_nrm = (sx / mag, sy / mag, sz / mag) if mag > 1e-9 else (0.0, 0.0, 1.0)
                if first:
                    idx = v
                    first = False
                else:
                    idx = len(new_vertices)
                    new_vertices.append(new_vertices[v])  # duplicate coordinate
                    vertex_values.append(0.0)
                    vertex_normals.append((0.0, 0.0, 1.0))
                vertex_values[idx] = mean_val
                vertex_normals[idx] = mean_nrm
                for ti in group:
                    corner = tris[ti]
                    for k in range(3):
                        if corner[k] == v:
                            corner[k] = idx
                            break

        return new_vertices, [tuple(t) for t in tris], vertex_values, vertex_normals

    def build(
        self,
        vertices,
        triangles,
        values,
        normals,
        vmin,
        vmax,
        colormap=DEFAULT_COLORMAP,
        band_step=0.0,
        smooth=False,
        value_gap=None,
    ):
        self._smooth = smooth
        root = coin.SoSeparator()

        hints = coin.SoShapeHints()
        hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
        hints.shapeType = coin.SoShapeHints.UNKNOWN_SHAPE_TYPE
        hints.creaseAngle = 0.0
        root.addChild(hints)

        if smooth:
            # Split vertices at creases and value jumps, so color and lighting
            # blend along fillets but not across sharp edges or sign flips.
            (r_vertices, r_triangles, self._vertex_values, vertex_normals) = (
                self._split_for_smoothing(vertices, triangles, values, normals, value_gap=value_gap)
            )
        else:
            r_vertices, r_triangles = vertices, triangles

        coords = coin.SoCoordinate3()
        coords.point.setValues(0, len(r_vertices), r_vertices)
        root.addChild(coords)

        if smooth:
            gmat = coin.SoMaterial()
            colors = [
                value_to_color(v, vmin, vmax, colormap, band_step) for v in self._vertex_values
            ]
            gmat.diffuseColor.setValues(0, len(colors), colors)
            root.addChild(gmat)
            gbind = coin.SoMaterialBinding()
            gbind.value = coin.SoMaterialBinding.PER_VERTEX_INDEXED
            root.addChild(gbind)
            self._global_material = gmat

            # Smoothed per-vertex normals so lighting no longer shows facets.
            gnorm = coin.SoNormal()
            gnorm.vector.setValues(0, len(vertex_normals), vertex_normals)
            root.addChild(gnorm)
            gnbind = coin.SoNormalBinding()
            gnbind.value = coin.SoNormalBinding.PER_VERTEX_INDEXED
            root.addChild(gnbind)

        for i, chunk in enumerate(_partition(r_vertices, r_triangles, values, normals)):
            root.addChild(self._build_chunk(i, chunk, vmin, vmax, colormap, band_step, smooth))

        self._sep = root
        App.Console.PrintMessage(
            f"DFM contour: {len(triangles)} triangles in {len(self._chunks)} pick chunks.\n"
        )
        return root

    def _build_chunk(self, index, chunk, vmin, vmax, colormap, band_step, smooth=False):
        sep = coin.SoSeparator()

        chunk_material = None
        if not smooth:
            material = coin.SoMaterial()
            colors = [value_to_color(v, vmin, vmax, colormap, band_step) for v in chunk["values"]]
            material.diffuseColor.setValues(0, len(colors), colors)
            sep.addChild(material)

            mbind = coin.SoMaterialBinding()
            mbind.value = coin.SoMaterialBinding.PER_FACE
            sep.addChild(mbind)
            chunk_material = material

            normal_node = coin.SoNormal()
            normal_node.vector.setValues(0, len(chunk["normals"]), chunk["normals"])
            sep.addChild(normal_node)

            nbind = coin.SoNormalBinding()
            nbind.value = coin.SoNormalBinding.PER_FACE
            sep.addChild(nbind)

        face_set = coin.SoIndexedFaceSet()
        flat = []
        for a, b, c in chunk["tris"]:
            flat.extend((a, b, c, -1))
        face_set.coordIndex.setValues(0, len(flat), flat)
        name = f"{_NAME_PREFIX}{index}"
        face_set.setName(coin.SbName(name))
        sep.addChild(face_set)

        self._chunks.append({"material": chunk_material, "values": chunk["values"]})
        self._name_to_chunk[name] = index
        return sep

    def recolor(self, vmin, vmax, colormap=DEFAULT_COLORMAP, band_step=0.0):
        if self._smooth and self._global_material is not None:
            colors = [
                value_to_color(v, vmin, vmax, colormap, band_step)
                for v in (self._vertex_values or [])
            ]
            self._global_material.diffuseColor.setValues(0, len(colors), colors)
            return
        for chunk in self._chunks:
            material = chunk.get("material")
            if material is None:
                continue
            colors = [value_to_color(v, vmin, vmax, colormap, band_step) for v in chunk["values"]]
            material.diffuseColor.setValues(0, len(colors), colors)

    def pick_value(self, picked):
        """Resolve a coin picked point to (key, value, point) or None."""
        if picked is None:
            return None
        path = picked.getPath()
        if path is None:
            return None
        tail = path.getTail()
        if tail is None:
            return None
        chunk_index = self._name_to_chunk.get(tail.getName().getString())
        if chunk_index is None:
            return None
        detail = picked.getDetail()
        if detail is None or not detail.isOfType(coin.SoFaceDetail.getClassTypeId()):
            return None
        face_index = coin.cast(detail, "SoFaceDetail").getFaceIndex()
        values = self._chunks[chunk_index]["values"]
        if face_index < 0 or face_index >= len(values):
            return None
        point = picked.getPoint()
        return ((chunk_index, face_index), values[face_index], (point[0], point[1], point[2]))

    def attach(self):
        if self._sep is None:
            return
        gui_doc = Gui.ActiveDocument
        if gui_doc is None or gui_doc.ActiveView is None:
            raise RuntimeError("No active 3D view to display the contour in.")
        self._view = gui_doc.ActiveView
        self._view.getSceneGraph().addChild(self._sep)
        try:
            self._original_visibility = self.target_object.ViewObject.Visibility
            self.target_object.ViewObject.Visibility = False
        except Exception:
            self._original_visibility = None

    def remove(self):
        if self._view is not None and self._sep is not None:
            try:
                self._view.getSceneGraph().removeChild(self._sep)
            except Exception:
                pass
        if self._original_visibility is not None:
            try:
                self.target_object.ViewObject.Visibility = self._original_visibility
            except Exception:
                pass
        self._sep = None
        self._view = None


_ACTIVE_CONTOURS = []


def build_scene(
    vertices,
    triangles,
    values,
    normals,
    vmin,
    vmax,
    colormap=DEFAULT_COLORMAP,
    band_step=0.0,
    smooth=False,
    value_gap=None,
):
    """Build a standalone contour separator (for a view provider to own).

    Unlike ContourNode.attach(), this does not touch the live scene graph.
    """
    node = ContourNode(None)
    return node.build(
        vertices, triangles, values, normals, vmin, vmax, colormap, band_step, smooth, value_gap
    )


def register(node: ContourNode):
    _ACTIVE_CONTOURS.append(node)


def clear_all():
    while _ACTIVE_CONTOURS:
        _ACTIVE_CONTOURS.pop().remove()
    App.Console.PrintMessage("DFM contour: cleared.\n")
