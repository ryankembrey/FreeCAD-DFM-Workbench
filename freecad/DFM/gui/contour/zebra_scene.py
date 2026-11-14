# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Coin3d overlay for Zebra Stripes.

A copy of the part's surface, pushed a hair off the body like the contour
overlays (see scene.py), textured with one repeating stripe pair. Each vertex
carries one texture coordinate, its stripe coordinate from
app/contour/zebra.py, so stripe edges stay sharp inside a triangle.

In Follow the view mode a sensor on the camera works the coordinates out
again whenever the view changes, before the next redraw.

The overlay can't be picked: clicks go through to the body, so faces can
still be selected (to pick a direction) while the stripes show.
"""

from typing import Optional, Sequence

import numpy as np
from pivy import coin

from ...app.contour import zebra as Z
from .scene import _pref_display_offset_fraction

_NAME = "DFMZebraStripes"
_IMAGE_SIZE = 256
_EDGE_COLOR = (0.08, 0.08, 0.1)


class ZebraOverlay:
    """The stripes on one mesh, in one view."""

    def __init__(self, mesh: Z.ZebraMesh):
        self.mesh = mesh
        self.mode = "view"  # or "fixed"
        self.horizontal = False
        self.direction = (0.0, 0.0, 1.0)
        self.count = 20.0
        self.width_share = 0.5
        self.colors = ((0, 0, 0), (255, 255, 255))
        self.view = None
        self._camera = None
        self._sensor: Optional[coin.SoNodeSensor] = None
        self._last_camera = None

        offset = mesh.diagonal * _pref_display_offset_fraction()
        self.root = coin.SoSeparator()
        self.root.setName(_NAME)
        pick = coin.SoPickStyle()
        pick.style = coin.SoPickStyle.UNPICKABLE
        self.root.addChild(pick)

        faces = coin.SoSeparator()
        # Lit like the part itself, so its shape still reads under the
        # stripes: the light color shades to gray away from the light, the
        # dark one stays dark.
        material = coin.SoMaterial()
        material.diffuseColor.setValue(0.85, 0.85, 0.85)
        material.ambientColor.setValue(0.35, 0.35, 0.35)
        material.specularColor.setValue(0.0, 0.0, 0.0)
        faces.addChild(material)
        polygon_offset = coin.SoPolygonOffset()
        polygon_offset.factor = -1.0
        polygon_offset.units = -1.0
        faces.addChild(polygon_offset)
        complexity = coin.SoComplexity()
        complexity.textureQuality = 1.0  # filtered and mipmapped: no shimmer when stripes are dense
        faces.addChild(complexity)
        self.texture = coin.SoTexture2()
        self.texture.model = coin.SoTexture2.MODULATE
        self.texture.wrapS = coin.SoTexture2.REPEAT
        self.texture.wrapT = coin.SoTexture2.CLAMP
        faces.addChild(self.texture)
        self.texcoords = coin.SoTextureCoordinate2()
        faces.addChild(self.texcoords)
        binding = coin.SoTextureCoordinateBinding()
        binding.value = coin.SoTextureCoordinateBinding.PER_VERTEX_INDEXED
        faces.addChild(binding)
        coords = coin.SoCoordinate3()
        pts = (mesh.points + mesh.normals * offset).astype(np.float32)
        coords.point.setValues(0, len(pts), pts)
        faces.addChild(coords)
        normals = coin.SoNormal()
        nrm = mesh.normals.astype(np.float32)
        normals.vector.setValues(0, len(nrm), nrm)
        faces.addChild(normals)
        normal_binding = coin.SoNormalBinding()
        normal_binding.value = coin.SoNormalBinding.PER_VERTEX_INDEXED
        faces.addChild(normal_binding)
        face_set = coin.SoIndexedFaceSet()
        index = np.hstack([mesh.triangles, -np.ones((len(mesh.triangles), 1), dtype=np.int32)]).ravel()
        index = index.astype(np.int32).tolist()
        face_set.coordIndex.setValues(0, len(index), index)
        face_set.textureCoordIndex.setValues(0, len(index), index)
        face_set.normalIndex.setValues(0, len(index), index)
        faces.addChild(face_set)
        self.root.addChild(faces)

        # The faces' edges, a little further out than the stripes so they
        # stay on top of them at creases.
        self.edges = coin.SoSwitch()
        self.edges.whichChild = coin.SO_SWITCH_ALL
        edge_sep = coin.SoSeparator()
        style = coin.SoDrawStyle()
        style.lineWidth = 1.5
        edge_sep.addChild(style)
        edge_light = coin.SoLightModel()
        edge_light.model = coin.SoLightModel.BASE_COLOR
        edge_sep.addChild(edge_light)
        color = coin.SoBaseColor()
        color.rgb.setValue(*_EDGE_COLOR)
        edge_sep.addChild(color)
        if mesh.edges:
            edge_pts = np.vstack([p + n * (2.0 * offset) for p, n in mesh.edges]).astype(np.float32)
            edge_coords = coin.SoCoordinate3()
            edge_coords.point.setValues(0, len(edge_pts), edge_pts)
            edge_sep.addChild(edge_coords)
            lines = coin.SoLineSet()
            counts = [len(p) for p, _n in mesh.edges]
            lines.numVertices.setValues(0, len(counts), counts)
            edge_sep.addChild(lines)
        self.edges.addChild(edge_sep)
        self.root.addChild(self.edges)

        self._update_image()

    # -- settings -------------------------------------------------------

    def set_style(self, count=None, width_share=None, colors=None) -> None:
        if count is not None:
            self.count = float(count)
        if width_share is not None:
            self.width_share = float(width_share)
        if colors is not None:
            self.colors = tuple(tuple(int(c) for c in rgb) for rgb in colors)
        if width_share is not None or colors is not None:
            self._update_image()
        if count is not None:
            self.update()

    def set_mode(self, mode: str, horizontal: Optional[bool] = None,
                 direction: Optional[Sequence[float]] = None) -> None:
        self.mode = mode
        if horizontal is not None:
            self.horizontal = bool(horizontal)
        if direction is not None:
            self.direction = tuple(float(c) for c in direction)
        self._watch_camera(self.mode == "view")
        self.update()

    def set_edges_visible(self, visible: bool) -> None:
        self.edges.whichChild = coin.SO_SWITCH_ALL if visible else coin.SO_SWITCH_NONE

    # -- the view ---------------------------------------------------------

    def attach(self, view) -> None:
        self.view = view
        view.getSceneGraph().addChild(self.root)
        self._watch_camera(self.mode == "view")
        self.update()

    def remove(self) -> None:
        self._watch_camera(False)
        if self.view is not None:
            try:
                self.view.getSceneGraph().removeChild(self.root)
            except Exception:
                pass
        self.view = None

    def _watch_camera(self, on: bool) -> None:
        if self._sensor is not None:
            self._sensor.detach()
            self._sensor = None
        self._camera = None
        if not on or self.view is None:
            return
        self._camera = self.view.getCameraNode()
        self._sensor = coin.SoNodeSensor(self._on_camera, None)
        self._sensor.attach(self._camera)

    def _on_camera(self, *_args) -> None:
        if self.view is not None and self.view.getCameraNode() is not self._camera:
            self._watch_camera(True)  # the view swapped cameras (orthographic <-> perspective)
        self.update()

    def camera_frame(self):
        """(view direction, right, up, eye or None) of the current camera, in world axes."""
        cam = self.view.getCameraNode() if self.view is not None else None
        if cam is None:
            return (0.0, 0.0, -1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), None
        rot = cam.orientation.getValue()
        look = rot.multVec(coin.SbVec3f(0, 0, -1)).getValue()
        right = rot.multVec(coin.SbVec3f(1, 0, 0)).getValue()
        up = rot.multVec(coin.SbVec3f(0, 1, 0)).getValue()
        eye = None
        if cam.isOfType(coin.SoPerspectiveCamera.getClassTypeId()):
            eye = cam.position.getValue().getValue()
        return tuple(look), tuple(right), tuple(up), (tuple(eye) if eye is not None else None)

    def coordinates(self) -> np.ndarray:
        """The stripe coordinate of every vertex, for the current settings and view."""
        if self.mode == "fixed":
            return Z.fixed_coordinates(self.mesh.normals, self.direction, self.count)
        look, right, up, eye = self.camera_frame()
        axis = up if self.horizontal else right
        if eye is not None:
            return Z.view_coordinates(self.mesh.normals, look, axis, self.count, self.mesh.points, eye)
        return Z.view_coordinates(self.mesh.normals, look, axis, self.count)

    def update(self) -> None:
        if self.mode == "view":
            state = (self.camera_frame(), self.count, self.horizontal)
            if state == self._last_camera:
                return
            self._last_camera = state
        else:
            self._last_camera = None
        t = self.coordinates()
        uv = np.empty((len(t), 2), dtype=np.float32)
        uv[:, 0] = t
        uv[:, 1] = 0.5
        self.texcoords.point.setValues(0, len(uv), uv)
        n = self.texcoords.point.getNum()
        if n > len(uv):
            self.texcoords.point.deleteValues(len(uv), n - len(uv))

    def _update_image(self) -> None:
        first, second = self.colors
        data = Z.stripe_image(self.width_share, first, second, _IMAGE_SIZE)
        self.texture.image.setValue(coin.SbVec2s(_IMAGE_SIZE, 1), 3, data)
