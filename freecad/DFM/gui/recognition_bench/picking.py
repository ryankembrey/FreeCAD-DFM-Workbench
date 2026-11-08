# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Clicking faces while the bench draws the model.

The bench hides the model and draws it with its own (unpickable) scene, as
the results view does, so FreeCAD's selection can't see the faces. A click
is instead turned into a ray through the 3D view and cast at the shape that
was recognised, and the first face it hits is reported by its FaceIndex
number: the same number the result uses, so there is no translation to get
wrong.

A click is a press and release in the same spot; a drag (orbiting) is not.
"""

from __future__ import annotations

from typing import Callable, Optional

_CLICK_SLOP = 4  # pixels


def first_face_hit(shape_occ, origin, direction) -> Optional[int]:
    """FaceIndex number (1-based) of the first face along the ray, or None."""
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

    from ...core.utils.geometry import FaceIndex

    inter = IntCurvesFace_ShapeIntersector()
    inter.Load(shape_occ, 1e-6)
    inter.Perform(gp_Lin(gp_Pnt(*origin), gp_Dir(*direction)), 0.0, 1e100)
    if not inter.IsDone() or inter.NbPnt() == 0:
        return None
    best = min(range(1, inter.NbPnt() + 1), key=inter.WParameter)
    index = FaceIndex(shape_occ).index_of(inter.Face(best))
    return index or None


class FacePicker:
    """Calls *on_pick(face, additive)* for clicks on the model in the active
    3D view. *face* is None for a click on empty space; *additive* is true
    with Ctrl held."""

    def __init__(self, on_pick: Callable[[Optional[int], bool], None]):
        self.on_pick = on_pick
        self._view = None
        self._callback = None
        self._down = None
        self._target = None
        self._shape = None
        self.enabled = True

    def attach(self, target_object, shape_occ) -> None:
        self._target = target_object
        self._shape = shape_occ
        if self._callback is not None:
            return
        try:
            from ..utils.view import get_active_view
            view = get_active_view()
            if view is None:
                return
            self._callback = view.addEventCallback("SoMouseButtonEvent", self._event)
            self._view = view
        except Exception:
            self._view = None
            self._callback = None

    def detach(self) -> None:
        if self._view is not None and self._callback is not None:
            try:
                self._view.removeEventCallback("SoMouseButtonEvent", self._callback)
            except Exception:
                pass
        self._view = None
        self._callback = None

    # -- Events --------------------------------------------------------------

    def _event(self, info: dict) -> None:
        if not self.enabled or self._shape is None or info.get("Button") != "BUTTON1":
            return
        pos = tuple(info.get("Position", (0, 0)))
        if info.get("State") == "DOWN":
            self._down = pos
            return
        if info.get("State") != "UP" or self._down is None:
            return
        down, self._down = self._down, None
        if abs(pos[0] - down[0]) > _CLICK_SLOP or abs(pos[1] - down[1]) > _CLICK_SLOP:
            return
        additive = bool(info.get("CtrlDown")) or bool(info.get("ShiftDown"))
        try:
            face = self._pick(pos)
        except Exception:
            face = None
        # Not from inside Coin's event handling: the answer redraws the scene.
        try:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self.on_pick(face, additive))
        except Exception:
            self.on_pick(face, additive)

    def _pick(self, pos) -> Optional[int]:
        view = self._view
        point = view.getPoint(int(pos[0]), int(pos[1]))
        cam = view.getCameraNode()
        if "Perspective" in str(view.getCameraType()):
            eye = cam.position.getValue()
            origin = (eye[0], eye[1], eye[2])
            d = (point.x - origin[0], point.y - origin[1], point.z - origin[2])
        else:
            vd = view.getViewDirection()
            d = (vd.x, vd.y, vd.z)
            back = 1e6
            n = (d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5 or 1.0
            origin = (point.x - d[0] / n * back, point.y - d[1] / n * back, point.z - d[2] / n * back)
        origin, d = self._to_shape(origin, d)
        return first_face_hit(self._shape, origin, d)

    def _to_shape(self, origin, d):
        """World ray into the shape's frame: the shape carries the object's
        own placement but not that of a container it sits in."""
        try:
            import FreeCAD as App  # type: ignore
            extra = self._target.getGlobalPlacement().multiply(self._target.Placement.inverse())
            inv = extra.inverse()
            o = inv.multVec(App.Vector(*origin))
            v = inv.Rotation.multVec(App.Vector(*d))
            return (o.x, o.y, o.z), (v.x, v.y, v.z)
        except Exception:
            return origin, d
