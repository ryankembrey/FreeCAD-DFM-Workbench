# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The asymmetry and deviation maps: the contour tools' colored mesh and
legend, with a value under the cursor.

Values with no measurement (infinity) are drawn grey: "Symmetric" or
"Unchanged". The legend's ends can be dragged, as on the contour tools.
"""

from __future__ import annotations

import math
import time
from typing import Callable, Optional

from pivy import coin
from PySide6 import QtCore, QtGui, QtWidgets

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ...core.inspection import geometry as G
from ..contour.legend import ContourLegend
from ..contour.scene import ContourNode
from ..utils.view import get_active_view, get_view_widget


def build_mesh(target_object, shape_occ, progress: Optional[Callable[[str], None]] = None):
    """(vertices, triangles, normals) for the map: the contour tools' uniform
    mesh when gmsh is there, else OpenCascade's own mesh made fine."""
    try:
        from ...app.contour.meshing import element_size_for, generate_uniform_mesh, get_default_resolution
        from ...app.contour.measures import outward_normals

        shape = target_object.Shape
        if progress:
            progress("Meshing the model")
        mesh = generate_uniform_mesh(shape, element_size_for(shape, get_default_resolution()))
        return list(mesh.vertices), [tuple(t) for t in mesh.triangles], outward_normals(shape, mesh)
    except Exception as exc:
        App.Console.PrintLog(f"DFM: uniform mesh unavailable ({exc}); using OpenCascade's.\n")
    diag = G.bounding_box(shape_occ, optimal=False).diagonal
    mesh = G.triangulate(shape_occ, max(diag / 600.0, 1e-3), 0.2)
    return mesh.vertices, mesh.triangles, [_normal(mesh.vertices, t) for t in mesh.triangles]


def _normal(vertices, tri):
    a, b, c = (vertices[i] for i in tri)
    u, v = G.vsub(b, a), G.vsub(c, a)
    n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    length = G.vlen(n)
    return G.vmul(n, 1.0 / length) if length > 1e-12 else (0.0, 0.0, 1.0)


class MapView:
    """One map on one model, with its legend and hover readout."""

    def __init__(self, target_object, title: str, no_value: str, format_value: Callable[[float], str]):
        self.target_object = target_object
        self.title = title
        self.no_value = no_value
        self.format_value = format_value
        self._node: Optional[ContourNode] = None
        self._legend: Optional[ContourLegend] = None
        self._label: Optional[QtWidgets.QLabel] = None
        self._hover_cb = None
        self._view = None
        self._last_t = 0.0
        self.range_changed = None  # callback(low, high)
        self.data = None

    @property
    def shown(self) -> bool:
        return self._node is not None

    def show(self, vertices, triangles, values, normals, low: float, high: float, colormap: str,
             smooth: bool = True) -> None:
        self.remove()
        self.data = (vertices, triangles, values, normals)
        node = ContourNode(self.target_object)
        finite = [v for v in values if math.isfinite(v)]
        span = (max(finite) - min(finite)) if finite else 1.0
        node.build(vertices, triangles, values, normals, low, high, colormap, 0.0, smooth,
                   0.5 * span if span > 0 else None, None)
        node.attach()
        self._node = node
        widget = get_view_widget()
        if widget is not None:
            legend = ContourLegend(widget)
            legend.set_no_value_label(self.no_value)
            dmin = min(finite) if finite else 0.0
            dmax = max(finite) if finite else 1.0
            lo_dom, hi_dom = (min(low, dmin), max(high, dmax))
            legend.configure(self.title, "mm", colormap, 0.0, lo_dom, hi_dom, low, high, dmin, dmax)
            legend.rangeChanged.connect(self._on_legend_range)
            try:
                from ..contour.document import auto_legend_text_rgb

                legend.set_text_color(auto_legend_text_rgb())
            except Exception:
                pass
            legend.set_orientation(True)
            pw = widget.width()
            w = min(max(320, pw // 3), max(200, pw - 40))
            legend.resize(w, 96)
            legend.move(max(8, min((pw - w) // 2, int(pw * 0.62) - w)), 12)
            legend.show()
            legend.raise_()
            self._legend = legend
        self._install_hover()

    def recolor(self, low: float, high: float, colormap: str) -> None:
        if self._node is not None:
            self._node.recolor(low, high, colormap, 0.0)
        if self._legend is not None:
            self._legend.set_range(low, high)
            self._legend.set_style(colormap, 0.0)

    def _on_legend_range(self, low: float, high: float) -> None:
        if self._node is not None and self._legend is not None:
            self._node.recolor(low, high, self._legend._colormap, 0.0)
        if self.range_changed is not None:
            self.range_changed(low, high)

    # -- hover -----------------------------------------------------------------

    def _install_hover(self) -> None:
        view = get_active_view()
        if view is None or self._hover_cb is not None:
            return
        try:
            self._hover_cb = view.addEventCallbackPivy(coin.SoLocation2Event.getClassTypeId(), self._on_hover)
            self._view = view
        except Exception:
            self._hover_cb = None

    def _on_hover(self, event_cb) -> None:
        now = time.monotonic()
        if now - self._last_t < 0.03 or self._node is None:
            return
        self._last_t = now
        result = None
        try:
            pos = event_cb.getEvent().getPosition()
            manager = self._view.getViewer().getSoRenderManager()
            action = coin.SoRayPickAction(manager.getViewportRegion())
            action.setPoint(coin.SbVec2s(int(pos[0]), int(pos[1])))
            action.setPickAll(True)
            action.apply(manager.getSceneGraph())
            picked = action.getPickedPointList()
            for i in range(picked.getLength()):
                result = self._node.pick_value(picked[i])
                if result is not None:
                    break
        except Exception:
            result = None
        label = self._hover_label()
        if result is None:
            if label is not None:
                label.hide()
            if self._legend is not None:
                self._legend.set_marker(None)
            return
        _key, value, _point = result
        text = self.format_value(value) if math.isfinite(value) else self.no_value
        if label is not None:
            label.setText(text)
            label.adjustSize()
            parent = label.parentWidget()
            p = parent.mapFromGlobal(QtGui.QCursor.pos()) + QtCore.QPoint(14, 14)
            label.move(p)
            label.show()
            label.raise_()
        if self._legend is not None:
            self._legend.set_marker(value if math.isfinite(value) else None)
        try:
            Gui.getMainWindow().statusBar().showMessage(f"{self.title}: {text}")
        except Exception:
            pass

    def _hover_label(self) -> Optional[QtWidgets.QLabel]:
        if self._label is not None:
            return self._label
        widget = get_view_widget()
        if widget is None:
            return None
        label = QtWidgets.QLabel(widget)
        label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.setStyleSheet("QLabel { background: rgba(20,20,22,180); color: #fff;"
                            " border: 1px solid rgba(255,255,255,70); border-radius: 4px;"
                            " padding: 2px 6px; font-weight: bold; }")
        self._label = label
        return label

    def remove(self) -> None:
        if self._hover_cb is not None and self._view is not None:
            try:
                self._view.removeEventCallbackPivy(coin.SoLocation2Event.getClassTypeId(), self._hover_cb)
            except Exception:
                pass
        self._hover_cb = None
        self._view = None
        if self._label is not None:
            self._label.hide()
            self._label.deleteLater()
            self._label = None
        if self._legend is not None:
            try:
                self._legend.cleanup()
                self._legend.setParent(None)
                self._legend.deleteLater()
            except Exception:
                pass
            self._legend = None
        if self._node is not None:
            self._node.remove()
            self._node = None
