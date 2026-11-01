# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Camera moves for inspecting results, using the report's framing.

* Zoom to: fit the findings, keeping the current viewing angle.
* Face: turn so the findings face you, tilted towards isometric so the
  part around them still reads as 3D. The zoom stays as it is.
* Zoom and face: both, framed with some of the part around the findings,
  like the report's close-ups.
* Whole model, and the standard views.

Moves animate. The camera orbits: the point it looks at and its distance
are interpolated while its orientation turns smoothly, so the part stays
in view throughout instead of swinging out of frame.
"""

import math
import time
from typing import Iterable, Optional

from PySide6.QtCore import QTimer

import FreeCAD as App  # type: ignore

from ...app.prefs import dfm_params
from ..report.capture import (
    EYES, Vec, _add, _mul, _norm, _sub, best_direction, bound_box_corners, frame, rotate,
)
from ..utils.view import get_active_view

# Standard views: where the camera sits, as in FreeCAD.
VIEWS: dict[str, Vec] = {
    **EYES,
    "rear": (0.0, 1.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
    "bottom": (0.0, 0.0, -1.0),
}
VIEW_LABELS = {
    "isometric": "Isometric", "front": "Front", "top": "Top", "right": "Right",
    "rear": "Rear", "left": "Left", "bottom": "Bottom",
}

Ref = tuple[str, int]  # ("Face" | "Edge", 0-based index)


def refs_of(findings: Iterable) -> list[Ref]:
    """The faces and edges of *findings*, as (type, index)."""
    return [(ref.type, ref.index) for f in findings for ref in f.refs
            if ref.type in ("Face", "Edge")]


class ResultsCamera:
    """Moves the 3D view's camera onto findings of *target_object*."""

    FPS = 60

    def __init__(self, target_object):
        self.target_object = target_object
        self._timer: Optional[QTimer] = None

    # -- Moves -------------------------------------------------------------

    def zoom_to(self, refs: list[Ref]) -> None:
        self._move(refs, face=False, zoom=True)

    def face(self, refs: list[Ref]) -> None:
        self._move(refs, face=True, zoom=False)

    def zoom_and_face(self, refs: list[Ref]) -> None:
        self._move(refs, face=True, zoom=True)

    def whole_model(self) -> None:
        self._move([], face=False, zoom=True)

    def standard_view(self, name: str) -> None:
        eye = VIEWS.get(name)
        if eye is not None:
            self._move([], face=False, zoom=True, direction=_mul(_norm(eye), -1.0))

    # -- Geometry ----------------------------------------------------------

    def _subshapes(self, refs: list[Ref]):
        shape = getattr(self.target_object, "Shape", None)
        if shape is None:
            return
        faces, edges = shape.Faces, shape.Edges  # each access builds a list: read once
        for kind, index in refs:
            try:
                yield faces[index] if kind == "Face" else edges[index]
            except IndexError:
                continue

    def _mean_normal(self, refs: list[Ref]) -> Optional[Vec]:
        """Area-weighted outward normal of the findings' faces.

        Edges have no normal of their own, so an edge counts with the faces
        that meet at it, which is the side you want to look at it from.
        """
        shape = self.target_object.Shape
        total = (0.0, 0.0, 0.0)
        for sub in self._subshapes(refs):
            faces = [sub]
            if not hasattr(sub, "normalAt"):
                try:
                    import Part  # type: ignore

                    faces = shape.ancestorsOfType(sub, Part.Face)
                except Exception:
                    faces = []
            for face in faces:
                try:
                    u0, u1, v0, v1 = face.ParameterRange
                    n = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
                    area = max(float(face.Area), 1e-9)
                except Exception:
                    continue
                total = _add(total, _mul((n.x, n.y, n.z), area))
        return _norm(total)

    # -- Placing the camera -----------------------------------------------

    def _move(self, refs: list[Ref], face: bool, zoom: bool, direction: Optional[Vec] = None) -> None:
        view = get_active_view()
        shape = getattr(self.target_object, "Shape", None)
        if view is None or shape is None:
            return
        try:
            cam = view.getCameraNode()
        except Exception:
            cam = None
        if cam is None:
            return

        q0 = tuple(cam.orientation.getValue().getValue())
        dir0 = rotate(q0, (0.0, 0.0, -1.0))
        up0 = rotate(q0, (0.0, 1.0, 0.0))
        ortho = "Orthographic" in str(cam.getTypeId().getName())
        focal0 = float(cam.focalDistance.getValue())
        pos0 = tuple(cam.position.getValue().getValue())
        center0 = _add(pos0, _mul(dir0, focal0))
        height0 = float(cam.height.getValue()) if ortho else 0.0

        model = bound_box_corners(shape.BoundBox)
        target = [c for sub in self._subshapes(refs) for c in bound_box_corners(sub.BoundBox)]
        if target:
            padding = max(dfm_params().GetFloat("ResultsCameraPadding", 1.7), 1.2)
            # Edges are thin, so frame more of the part around them than around faces.
            only_edges = all(kind == "Edge" for kind, _ in refs)
            min_fraction = 0.55 if only_edges else 0.35
        else:
            target, padding, min_fraction = model, 1.12, 0.0

        if direction is None:
            direction = best_direction(self._mean_normal(refs)) if face and refs else dir0
        # Keep the current "up" while only zooming, so the part doesn't roll.
        up_hint = up0 if not face and direction is dir0 else (0.0, 0.0, 1.0)

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
        setup = frame(direction, target, model, aspect, padding, ortho, angle, up_hint, min_fraction)
        dir1 = rotate(setup.orientation, (0.0, 0.0, -1.0))
        center1 = _add(setup.position, _mul(dir1, setup.focal_distance))
        focal1, height1 = setup.focal_distance, setup.height

        if not zoom:
            # Turn to face, but keep how close the view is.
            if ortho:
                height1 = height0
            else:
                focal1 = focal0

        self._animate(cam, ortho, (q0, center0, focal0, height0),
                      (setup.orientation, center1, focal1, height1))

    def _animate(self, cam, ortho: bool, start, end) -> None:
        from pivy import coin

        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        q0, c0, f0, h0 = start
        q1, c1, f1, h1 = end
        r0, r1 = coin.SbRotation(*q0), coin.SbRotation(*q1)
        duration = max(dfm_params().GetFloat("ResultsCameraAnimDuration", 0.6), 0.0)
        began = time.perf_counter()

        def apply(t: float) -> None:
            s = t * t * (3.0 - 2.0 * t)  # smoothstep
            rot = coin.SbRotation.slerp(r0, r1, s)
            q = tuple(rot.getValue())
            d = rotate(q, (0.0, 0.0, -1.0))
            center = _add(c0, _mul(_sub(c1, c0), s))
            focal = f0 + (f1 - f0) * s
            cam.orientation.setValue(rot)
            cam.position.setValue(*_sub(center, _mul(d, focal)))
            cam.focalDistance.setValue(focal)
            if ortho:
                cam.height.setValue(h0 + (h1 - h0) * s)

        if duration <= 0:
            apply(1.0)
            return

        def tick() -> None:
            t = min((time.perf_counter() - began) / duration, 1.0)
            try:
                apply(t)
            except Exception as exc:  # the view was closed
                App.Console.PrintWarning(f"DFM: camera move stopped: {exc}\n")
                t = 1.0
            if t >= 1.0 and self._timer is not None:
                self._timer.stop()
                self._timer = None

        timer = QTimer()
        timer.setInterval(max(1, int(1000 / self.FPS)))
        timer.timeout.connect(tick)
        timer.start()
        self._timer = timer
