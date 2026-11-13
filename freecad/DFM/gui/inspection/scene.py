# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""What the inspection tools draw in the 3D view, beside the model's node.

* The model itself, drawn by the results panel's :class:`HighlightScene`
  with the faces of each difference in its color (the model is hidden
  meanwhile, as in the results panel).
* Solids: material to show in a color (a boss with no partner, a rib
  added in B), drawn just in front of the faces they share with the model.
* Ghosts: where a partner would be, or what was removed: see-through, with
  a dashed outline that shows through the part like a hidden line.
* The mirror plane (a see-through square with a dashed edge) or the axis,
  on their own: the panel names them, so there is no tag in the view.
* The blue info icon on each difference, as the results panel marks
  findings; the selected one is ringed.

Everything sits next to the model's own node, so a model inside an
App::Part container is drawn with the container's placement.
"""

from __future__ import annotations

from typing import Optional, Sequence

from pivy import coin
from PySide6 import QtCore, QtGui

import FreeCAD as App  # type: ignore

from ...core.inspection import geometry as G
from ..results.highlight_scene import HighlightScene, hex_to_rgb
from ..utils.badge import qimage_to_soimage
from ..utils.view import device_pixel_ratio, get_active_view
from .widgets import INFO_HEX, INFO_ICON

PLANE_RGB = (0.18, 0.47, 0.77)
DASH = 0xF0F0


def _material(rgb, transparency: float = 0.0, emissive: float = 0.0):
    m = coin.SoMaterial()
    m.diffuseColor.setValue(*rgb)
    m.ambientColor.setValue(*(c * 0.35 for c in rgb))
    m.emissiveColor.setValue(*(c * emissive for c in rgb))
    m.transparency.setValue(transparency)
    return m


def _faces_node(mesh: G.Mesh):
    sep = coin.SoSeparator()
    coords = coin.SoCoordinate3()
    coords.point.setValues(0, len(mesh.vertices), mesh.vertices)
    sep.addChild(coords)
    hints = coin.SoShapeHints()
    hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
    hints.shapeType = coin.SoShapeHints.UNKNOWN_SHAPE_TYPE
    hints.creaseAngle = 0.5
    sep.addChild(hints)
    faces = coin.SoIndexedFaceSet()
    flat = []
    for a, b, c in mesh.triangles:
        flat.extend((a, b, c, -1))
    faces.coordIndex.setValues(0, len(flat), flat)
    sep.addChild(faces)
    return sep


def _edges_node(polylines: Sequence[Sequence[G.Vec]]):
    sep = coin.SoSeparator()
    pts, counts = [], []
    for line in polylines:
        if len(line) >= 2:
            pts.extend(line)
            counts.append(len(line))
    if not counts:
        return sep
    coords = coin.SoCoordinate3()
    coords.point.setValues(0, len(pts), pts)
    sep.addChild(coords)
    lines = coin.SoLineSet()
    lines.numVertices.setValues(0, len(counts), counts)
    sep.addChild(lines)
    return sep


def edge_polylines(shape, deflection: float) -> list[list[G.Vec]]:
    """Each edge of *shape* as points along it."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.TopoDS import TopoDS

    out = []
    edges = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edges)
    for k in range(1, edges.Extent() + 1):
        edge = TopoDS.Edge_s(edges.FindKey(k))
        try:
            curve = BRepAdaptor_Curve(edge)
            disc = GCPnts_QuasiUniformDeflection(curve, deflection)
            if not disc.IsDone() or disc.NbPoints() < 2:
                continue
            line = []
            for i in range(1, disc.NbPoints() + 1):
                p = disc.Value(i)
                line.append((p.X(), p.Y(), p.Z()))
            out.append(line)
        except Exception:
            continue
    return out


def _no_depth(sep):
    db = coin.SoDepthBuffer()
    db.test.setValue(False)
    sep.addChild(db)


def _unpickable(sep):
    style = coin.SoPickStyle()
    style.style = coin.SoPickStyle.UNPICKABLE
    sep.addChild(style)


def _line_style(width: float, pattern: Optional[int] = None):
    ds = coin.SoDrawStyle()
    ds.lineWidth.setValue(width)
    if pattern is not None:
        ds.linePattern.setValue(pattern)
    return ds


def _base_color_light():
    lm = coin.SoLightModel()
    lm.model.setValue(coin.SoLightModel.BASE_COLOR)
    return lm


def _marker_image(selected: bool, dpr: float) -> QtGui.QImage:
    """The info icon on a white disc (so it reads on any part); ringed when selected."""
    size = 26.0
    image = QtGui.QImage(int(round(size * dpr)), int(round(size * dpr)), QtGui.QImage.Format.Format_ARGB32)
    image.setDevicePixelRatio(dpr)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(image)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    centre = QtCore.QPointF(size / 2, size / 2)
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.setBrush(QtGui.QColor(0, 0, 0, 60))
    p.drawEllipse(centre + QtCore.QPointF(0, 1), 10.5, 10.5)
    p.setBrush(QtGui.QColor("#ffffff"))
    p.drawEllipse(centre, 10.0, 10.0)
    icon = QtGui.QIcon(INFO_ICON).pixmap(QtCore.QSize(18, 18), dpr)
    p.drawPixmap(QtCore.QPointF(size / 2 - 9, size / 2 - 9), icon)
    if selected:
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.setPen(QtGui.QPen(QtGui.QColor(INFO_HEX), 2.2))
        p.drawEllipse(centre, 11.6, 11.6)
    p.end()
    return image


_MARKERS: dict[tuple[bool, float], QtGui.QImage] = {}


def _marker_node(selected: bool):
    dpr = device_pixel_ratio()
    key = (selected, dpr)
    if key not in _MARKERS:
        _MARKERS[key] = _marker_image(selected, dpr)
    node, _w, _h = qimage_to_soimage(_MARKERS[key], dpr, h_align="center")
    return node


class InspectScene:
    """The inspection tools' drawing for one shown model."""

    def __init__(self, target_object):
        self.target_object = target_object
        self.model = HighlightScene(target_object) if target_object is not None else None
        self._view = None
        self._parent = None
        self._root = None
        self._parts: dict[str, coin.SoSwitch] = {}
        self._mesh_cache: dict[int, tuple] = {}
        self._target_visible: Optional[bool] = None
        self._diag = 100.0
        try:
            bb = target_object.Shape.BoundBox
            self._diag = max(bb.DiagonalLength, 1.0)
        except Exception:
            pass

    # -- the model -------------------------------------------------------------

    def show_model(self, faces: dict[int, str], rest_transparency: Optional[float] = None) -> None:
        """Draw the model with *faces* (1-based) in their colors, in place of the object."""
        if self.model is None:
            return
        self.model.set_style(rest_transparency=rest_transparency)
        self.model.set_highlight({i - 1: c for i, c in faces.items()}, {})
        self.model.show()
        if self.model.visible:
            self._set_target_visible(False)

    def hide_model(self) -> None:
        if self.model is not None:
            self.model.hide()
        self._restore_target()

    def _set_target_visible(self, visible: bool) -> None:
        try:
            vobj = self.target_object.ViewObject
            if self._target_visible is None:
                self._target_visible = bool(vobj.Visibility)
            vobj.Visibility = visible
        except Exception:
            pass

    def _restore_target(self) -> None:
        if self._target_visible is None:
            return
        try:
            self.target_object.ViewObject.Visibility = self._target_visible
        except Exception:
            pass
        self._target_visible = None

    # -- overlays ---------------------------------------------------------------

    def _ensure_root(self) -> bool:
        if self._root is not None:
            return True
        view = get_active_view()
        if view is None:
            return False
        parent = None
        if self.model is not None:
            try:
                parent = self.model._model_parent(view)
            except Exception:
                parent = None
        if parent is None:
            parent = view.getSceneGraph()
        self._root = coin.SoSeparator()
        self._root.setName(coin.SbName("DFM_InspectScene"))
        _unpickable(self._root)
        parent.addChild(self._root)
        self._view, self._parent = view, parent
        return True

    def _slot(self, name: str) -> Optional[coin.SoSwitch]:
        if not self._ensure_root():
            return None
        sw = self._parts.get(name)
        if sw is None:
            sw = coin.SoSwitch()
            sw.whichChild = -3
            self._root.addChild(sw)
            self._parts[name] = sw
        else:
            sw.removeAllChildren()
        return sw

    def set_visible(self, name: str, visible: bool) -> None:
        sw = self._parts.get(name)
        if sw is not None:
            sw.whichChild = -3 if visible else -1

    def _mesh(self, shape):
        key = id(shape)
        hit = self._mesh_cache.get(key)
        if hit is None or hit[0] is not shape:
            deflection = max(self._diag / 900.0, 1e-3)
            hit = (shape, G.triangulate(shape, deflection), edge_polylines(shape, deflection))
            self._mesh_cache[key] = hit
        return hit[1], hit[2]

    def set_solids(self, items: Sequence[tuple[object, str, bool]]) -> None:
        """Material in a color: (shape, hex color, emphasised)."""
        sw = self._slot("solids")
        if sw is None:
            return
        sep = coin.SoSeparator()
        offset = coin.SoPolygonOffset()
        offset.factor = -2.0
        offset.units = -3.0
        sep.addChild(offset)
        for shape, color, strong in items:
            mesh, edges = self._mesh(shape)
            rgb = hex_to_rgb(color)
            one = coin.SoSeparator()
            one.addChild(_material(rgb, 0.0, 0.25 if strong else 0.0))
            one.addChild(_faces_node(mesh))
            lines = coin.SoSeparator()
            lines.addChild(_base_color_light())
            lines.addChild(_material((0.16, 0.17, 0.19)))
            lines.addChild(_line_style(2.5 if strong else 1.2))
            lines.addChild(_edges_node(edges))
            one.addChild(lines)
            sep.addChild(one)
        sw.addChild(sep)

    def set_ghosts(self, items: Sequence[tuple[object, str, bool]]) -> None:
        """See-through shapes with dashed outlines: (shape, hex color, emphasised)."""
        sw = self._slot("ghosts")
        if sw is None:
            return
        sep = coin.SoSeparator()
        for shape, color, strong in items:
            mesh, edges = self._mesh(shape)
            rgb = hex_to_rgb(color)
            faces = coin.SoSeparator()
            faces.addChild(_material(rgb, 0.55 if strong else 0.7))
            faces.addChild(_faces_node(mesh))
            sep.addChild(faces)
            lines = coin.SoSeparator()
            _no_depth(lines)
            lines.addChild(_base_color_light())
            lines.addChild(_material(tuple(c * 0.75 for c in rgb)))
            lines.addChild(_line_style(2.2 if strong else 1.5, DASH))
            lines.addChild(_edges_node(edges))
            sep.addChild(lines)
        sw.addChild(sep)

    def set_plane(self, origin: G.Vec, normal: G.Vec, box: G.Box) -> None:
        """The mirror plane: a see-through square a little larger than the part."""
        sw = self._slot("plane")
        if sw is None:
            return
        n = G.vunit(normal)
        helper = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
        u = G.vunit(_cross(n, helper))
        v = _cross(n, u)
        corners = box.corners()
        su = [G.vdot(G.vsub(c, origin), u) for c in corners]
        sv = [G.vdot(G.vsub(c, origin), v) for c in corners]
        pad = 0.08 * box.diagonal
        u0, u1 = min(su) - pad, max(su) + pad
        v0, v1 = min(sv) - pad, max(sv) + pad
        quad = [G.vadd(origin, G.vadd(G.vmul(u, a), G.vmul(v, b))) for a, b in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
        sep = coin.SoSeparator()
        hints = coin.SoShapeHints()
        hints.shapeType = coin.SoShapeHints.UNKNOWN_SHAPE_TYPE
        sep.addChild(hints)
        sep.addChild(_material(PLANE_RGB, 0.82))
        sep.addChild(_faces_node(G.Mesh(quad, [(0, 1, 2), (0, 2, 3)])))
        outline = coin.SoSeparator()
        _no_depth(outline)
        outline.addChild(_base_color_light())
        outline.addChild(_material(PLANE_RGB))
        outline.addChild(_line_style(1.6, 0xFF00))
        outline.addChild(_edges_node([quad + [quad[0]]]))
        sep.addChild(outline)
        sw.addChild(sep)

    def set_axis(self, origin: G.Vec, direction: G.Vec, box: G.Box) -> None:
        """The axis: a dash-dot line through the part, a little longer than it."""
        sw = self._slot("plane")
        if sw is None:
            return
        d = G.vunit(direction)
        ts = [G.vdot(G.vsub(c, origin), d) for c in box.corners()]
        pad = 0.12 * box.diagonal
        a = G.vadd(origin, G.vmul(d, min(ts) - pad))
        b = G.vadd(origin, G.vmul(d, max(ts) + pad))
        sep = coin.SoSeparator()
        _no_depth(sep)
        sep.addChild(_base_color_light())
        sep.addChild(_material(PLANE_RGB))
        sep.addChild(_line_style(2.0, 0xFF18))
        sep.addChild(_edges_node([[a, b]]))
        sw.addChild(sep)

    def set_markers(self, items: Sequence[tuple[G.Vec, bool]]) -> None:
        """The info icon at each point: (point, selected)."""
        sw = self._slot("markers")
        if sw is None:
            return
        sep = coin.SoAnnotation()
        sep.addChild(_base_color_light())
        for point, selected in items:
            if point is None:
                continue
            one = coin.SoSeparator()
            t = coin.SoTranslation()
            t.translation.setValue(*point)
            one.addChild(t)
            try:
                one.addChild(_marker_node(selected))
            except Exception:
                continue
            sep.addChild(one)
        sw.addChild(sep)

    def clear_overlays(self) -> None:
        for name in list(self._parts):
            sw = self._parts[name]
            sw.removeAllChildren()

    def remove(self) -> None:
        if self._root is not None and self._parent is not None:
            try:
                self._parent.removeChild(self._root)
            except Exception:
                pass
        self._root = None
        self._parent = None
        self._parts = {}
        self._mesh_cache = {}
        if self.model is not None:
            self.model.remove()
        self._restore_target()


def _cross(a, b) -> G.Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def safe_remove(scene: Optional[InspectScene]) -> None:
    if scene is None:
        return
    try:
        scene.remove()
    except Exception as exc:
        App.Console.PrintWarning(f"DFM: could not clear the inspection view. {exc}\n")
