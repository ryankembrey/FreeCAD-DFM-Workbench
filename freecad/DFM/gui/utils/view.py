# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Access to the active 3D view and small view-space helpers."""

from typing import Optional

from pivy import coin
from PySide6 import QtGui, QtWidgets

import FreeCADGui as Gui  # type: ignore


def get_active_view():
    """The active 3D view of the active document, or None if there isn't one."""
    gui_doc = Gui.ActiveDocument
    if gui_doc is None:
        return None
    return getattr(gui_doc, "ActiveView", None)


def get_view_widget() -> Optional[QtWidgets.QWidget]:
    """The widget of the active MDI sub-window (the 3D view), or None."""
    try:
        mdi = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
        if mdi is None:
            return None
        sub = mdi.activeSubWindow() or mdi.currentSubWindow()
        return sub.widget() if sub is not None else None
    except Exception:
        return None


def viewport_height(view) -> int:
    """Height of the 3D view in pixels, or 0 if unknown."""
    try:
        size = view.getSize()
        if size and len(size) >= 2 and size[1] > 0:
            return int(size[1])
    except Exception:
        pass
    return 0


def device_pixel_ratio() -> float:
    """Device pixel ratio of the primary screen (at least 1.0)."""
    try:
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen:
            return max(1.0, float(screen.devicePixelRatio()))
    except Exception:
        pass
    return 1.0


def project_to_screen(view, point):
    """Project a world-space point to (x, y) pixels in ``view``, or None on failure."""
    try:
        camera = view.getCameraNode()
        size = view.getSize()
        if not camera or not size or size[0] <= 0 or size[1] <= 0:
            return None
        aspect = float(size[0]) / float(size[1])
        volume = camera.getViewVolume(aspect)
        matrix = volume.getMatrix()
        dst = matrix.multVecMatrix(coin.SbVec3f(point.x, point.y, point.z))
        nx, ny, _nz = dst.getValue()
        nx01 = (nx + 1.0) * 0.5
        ny01 = (ny + 1.0) * 0.5
        return nx01 * size[0], (1.0 - ny01) * size[1]
    except Exception:
        return None


def pick_document_object(view, event):
    """Ray-pick under ``event``; return ``(document_name, object_name)`` of the first
    FreeCAD object hit (via its SoFCSelection node), or None."""
    try:
        pos = event.getPosition()
        size = view.getSize()
        if not size or size[0] <= 0 or size[1] <= 0:
            return None
        render_manager = view.getViewer().getSoRenderManager()
        viewport_region = render_manager.getViewportRegion()
        scene = render_manager.getSceneGraph()

        action = coin.SoRayPickAction(viewport_region)
        action.setPoint(coin.SbVec2s(int(pos[0]), int(pos[1])))
        action.setPickAll(True)
        action.apply(scene)

        picked_list = action.getPickedPointList()
        try:
            count = picked_list.getLength()
        except Exception:
            count = len(picked_list)
        for i in range(count):
            path = picked_list[i].getPath()
            if path is None:
                continue
            for j in range(path.getLength()):
                node = path.getNode(j)
                try:
                    type_name = node.getTypeId().getName().getString()
                except Exception:
                    continue
                if type_name == "SoFCSelection":
                    try:
                        doc_name = node.documentName.getValue().getString()
                        obj_name = node.objectName.getValue().getString()
                    except Exception:
                        continue
                    if doc_name and obj_name:
                        return (doc_name, obj_name)
    except Exception:
        return None
    return None
