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

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ...contour.colormap import value_to_color, DEFAULT_COLORMAP


_TARGET_PER_CHUNK = 2500
_NAME_PREFIX = "DFMContourChunk_"


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
    ):
        root = coin.SoSeparator()

        hints = coin.SoShapeHints()
        hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
        hints.shapeType = coin.SoShapeHints.UNKNOWN_SHAPE_TYPE
        hints.creaseAngle = 0.0
        root.addChild(hints)

        coords = coin.SoCoordinate3()
        coords.point.setValues(0, len(vertices), vertices)
        root.addChild(coords)

        for i, chunk in enumerate(_partition(vertices, triangles, values, normals)):
            root.addChild(self._build_chunk(i, chunk, vmin, vmax, colormap, band_step))

        self._sep = root
        App.Console.PrintMessage(
            f"DFM contour: {len(triangles)} triangles in {len(self._chunks)} pick chunks.\n"
        )
        return root

    def _build_chunk(self, index, chunk, vmin, vmax, colormap, band_step):
        sep = coin.SoSeparator()

        material = coin.SoMaterial()
        colors = [value_to_color(v, vmin, vmax, colormap, band_step) for v in chunk["values"]]
        material.diffuseColor.setValues(0, len(colors), colors)
        sep.addChild(material)

        mbind = coin.SoMaterialBinding()
        mbind.value = coin.SoMaterialBinding.PER_FACE
        sep.addChild(mbind)

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

        self._chunks.append({"material": material, "values": chunk["values"]})
        self._name_to_chunk[name] = index
        return sep

    def recolor(self, vmin, vmax, colormap=DEFAULT_COLORMAP, band_step=0.0):
        for chunk in self._chunks:
            colors = [value_to_color(v, vmin, vmax, colormap, band_step) for v in chunk["values"]]
            chunk["material"].diffuseColor.setValues(0, len(colors), colors)

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
    vertices, triangles, values, normals, vmin, vmax, colormap=DEFAULT_COLORMAP, band_step=0.0
):
    """Build a standalone contour separator (for a view provider to own).

    Unlike ContourNode.attach(), this does not touch the live scene graph.
    """
    node = ContourNode(None)
    return node.build(vertices, triangles, values, normals, vmin, vmax, colormap, band_step)


def register(node: ContourNode):
    _ACTIVE_CONTOURS.append(node)


def clear_all():
    while _ACTIVE_CONTOURS:
        _ACTIVE_CONTOURS.pop().remove()
    App.Console.PrintMessage("DFM contour: cleared.\n")
