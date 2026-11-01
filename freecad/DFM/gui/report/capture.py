# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Viewport images for the PDF report.

Each image highlights its findings with the results panel's overlay, sets
the camera directly and renders offscreen with ``saveImage``.

The camera node is written to directly rather than through
``viewIsometric()``, ``setViewDirection()`` or ``fitAll()``: those may
animate, and the offscreen render would then catch the camera halfway.
The offscreen render also doesn't recompute the clipping planes the way
the interactive view does, so they are set here to enclose the whole
model. The navigation cube is turned off while capturing.

The camera, the highlight, the navigation cube and the viewport markers
are put back afterwards, also when capturing is cancelled or fails.
"""

import math
import os
import tempfile
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from PySide6 import QtGui

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ...app.prefs import dfm_params
from ...core.models import CheckResult
from ..results.visuals import severity_color
from ..utils.view import get_active_view
from .data import ImageJob
from .settings import ReportSettings

Vec = tuple[float, float, float]

# Where the camera sits for the standard views (FreeCAD's conventions).
EYES: dict[str, Vec] = {
    "isometric": (1.0, -1.0, 1.0),
    "front": (0.0, -1.0, 0.0),
    "top": (0.0, 0.0, 1.0),
    "right": (1.0, 0.0, 0.0),
}

# The rest of the part in report images, when "fade the rest" is on.
_REST_FADED = (0.83, 0.84, 0.86, 0.0)


# ---------------------------------------------------------------------------
# Camera math (no FreeCAD or Coin needed, so it can be tested on its own)
# ---------------------------------------------------------------------------


def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _mul(a, s): return (a[0] * s, a[1] * s, a[2] * s)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(v) -> Optional[Vec]:
    length = math.sqrt(_dot(v, v))
    return (v[0] / length, v[1] / length, v[2] / length) if length > 1e-9 else None


def basis_for(direction: Vec, up_hint: Vec = (0.0, 0.0, 1.0)) -> tuple[Vec, Vec, Vec]:
    """Camera right, up and forward axes for looking along *direction*.

    Up follows *up_hint* (Z, so parts stand upright) unless the view is
    nearly straight up or down, where Y is up instead, as in FreeCAD's
    top view.
    """
    d = _norm(direction) or (0.0, 0.0, -1.0)
    if abs(_dot(d, _norm(up_hint) or (0.0, 0.0, 1.0))) > 0.97:
        up_hint = (0.0, 1.0, 0.0)
    right = _norm(_cross(d, up_hint)) or (1.0, 0.0, 0.0)
    up = _cross(right, d)
    return right, up, d


def quaternion(right: Vec, up: Vec, forward: Vec) -> tuple[float, float, float, float]:
    """(x, y, z, w) rotating the camera's local axes onto right, up and -forward."""
    z = _mul(forward, -1.0)  # a camera looks down its local -Z
    m00, m01, m02 = right[0], up[0], z[0]
    m10, m11, m12 = right[1], up[1], z[1]
    m20, m21, m22 = right[2], up[2], z[2]
    trace = m00 + m11 + m22
    if trace > 0:
        s = 0.5 / math.sqrt(trace + 1.0)
        return ((m21 - m12) * s, (m02 - m20) * s, (m10 - m01) * s, 0.25 / s)
    if m00 > m11 and m00 > m22:
        s = 2.0 * math.sqrt(1.0 + m00 - m11 - m22)
        return (0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s)
    if m11 > m22:
        s = 2.0 * math.sqrt(1.0 + m11 - m00 - m22)
        return ((m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s)
    s = 2.0 * math.sqrt(1.0 + m22 - m00 - m11)
    return ((m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s)


def rotate(q, v: Vec) -> Vec:
    """Rotate *v* by the unit quaternion *q* (x, y, z, w)."""
    qv = (q[0], q[1], q[2])
    t = _mul(_cross(qv, v), 2.0)
    return _add(_add(v, _mul(t, q[3])), _cross(qv, t))


@dataclass
class CameraSetup:
    position: Vec
    orientation: tuple[float, float, float, float]
    focal_distance: float
    near: float
    far: float
    height: float = 0.0  # orthographic view height


def frame(direction: Vec, target: Sequence[Vec], model: Sequence[Vec], aspect: float,
          padding: float, orthographic: bool, height_angle: float = math.pi / 4,
          up_hint: Vec = (0.0, 0.0, 1.0), min_fraction: float = 0.0) -> CameraSetup:
    """A camera looking along *direction* that fits the *target* points.

    *aspect* is the image width / height. The clipping planes enclose all
    of the *model* points. With *min_fraction*, the view is never tighter
    than that fraction of the whole model, so small faces keep some of the
    part around them.
    """
    right, up, d = basis_for(direction, up_hint)

    def extent(points):
        xs = [_dot(p, right) for p in points]
        ys = [_dot(p, up) for p in points]
        zs = [_dot(p, d) for p in points]
        return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))

    (x0, x1), (y0, y1), (z0, z1) = extent(target)
    need = max(y1 - y0, (x1 - x0) / max(aspect, 1e-6), 1e-6) * padding
    if min_fraction:
        (mx0, mx1), (my0, my1), _ = extent(model)
        need = max(need, max(my1 - my0, (mx1 - mx0) / max(aspect, 1e-6)) * min_fraction)
    center = _add(_add(_mul(right, (x0 + x1) / 2), _mul(up, (y0 + y1) / 2)), _mul(d, (z0 + z1) / 2))

    model_depths = [_dot(p, d) for p in model]
    span = max(model_depths) - min(model_depths)
    radius = max(math.dist(center, p) for p in model) if model else need
    if orthographic:
        # Stand in front of everything; the view size doesn't depend on distance.
        dist = (_dot(center, d) - min(model_depths)) + 0.2 * radius + 1.0
    else:
        half = max(height_angle / 2.0, 1e-3)
        dist = (need / 2.0) / math.tan(half) + (z1 - z0) / 2.0
    position = _sub(center, _mul(d, dist))
    depths = [_dot(_sub(p, position), d) for p in model] or [dist]
    near = max(min(depths) - 0.05 * (span + 1.0), dist * 0.01 if not orthographic else 0.001)
    far = max(depths) + 0.05 * (span + 1.0)
    return CameraSetup(position, quaternion(right, up, d), dist, near, max(far, near + 1e-3),
                       need if orthographic else 0.0)


def best_direction(normal: Optional[Vec]) -> Vec:
    """View direction for faces with mean outward *normal*: facing them, tilted
    towards isometric so the part around them still reads as 3D."""
    iso = _norm(EYES["isometric"])
    eye = _norm(_add(normal, _mul(iso, 0.6))) if normal else None
    return _mul(eye or iso, -1.0)


def bound_box_corners(bb) -> list[Vec]:
    return [(x, y, z) for x in (bb.XMin, bb.XMax) for y in (bb.YMin, bb.YMax)
            for z in (bb.ZMin, bb.ZMax)]


# ---------------------------------------------------------------------------
# Capturing
# ---------------------------------------------------------------------------


class ViewportCapture:
    """Captures report images from the active 3D view.

    *hide_annotations* and *show_annotations* hide and restore the
    results panel's viewport markers while capturing.
    """

    _CAMERA_FIELDS = ("position", "orientation", "focalDistance", "nearDistance", "farDistance",
                      "height", "heightAngle")

    def __init__(self, bridge, settings: ReportSettings,
                 hide_annotations: Optional[Callable[[], None]] = None,
                 show_annotations: Optional[Callable[[], None]] = None):
        self.bridge = bridge
        self.settings = settings
        self._hide = hide_annotations
        self._show = show_annotations
        self._view = None
        self._camera: Optional[str] = None
        self._camera_fields: dict = {}
        self._start_q = None
        self._navi_cube = False
        self._highlight = ([], [])
        self._tmp = tempfile.mkdtemp(prefix="dfm_report_")

    @property
    def available(self) -> bool:
        return get_active_view() is not None

    # ------------------------------------------------------------------

    def begin(self) -> None:
        self._view = view = get_active_view()
        if view is None:
            return
        try:
            self._camera = view.getCamera()
        except Exception:
            self._camera = None
        cam = self._camera_node()
        if cam is not None:
            for name in self._CAMERA_FIELDS:
                field = getattr(cam, name, None)
                if field is not None:
                    try:
                        self._camera_fields[name] = field.getValue()
                    except Exception:
                        pass
            try:
                self._start_q = tuple(cam.orientation.getValue().getValue())
            except Exception:
                self._start_q = None
        self._navi_cube = self._set_navi_cube(False)
        self._highlight = self.bridge.current_highlight()
        self.bridge.remove_finding_overlay()
        if self.settings.hide_markers and self._hide:
            self._hide()

    def end(self) -> None:
        try:
            faces, edges = self._highlight
            self.bridge.set_display_style()  # back to the panel's own look
            self.bridge.highlight_faces_and_edges_by_index(faces, edges)
            self._restore_camera()
        finally:
            if self._navi_cube:
                self._set_navi_cube(True)
            if self.settings.hide_markers and self._show:
                self._show()
            try:
                os.rmdir(self._tmp)
            except OSError:
                pass

    def capture(self, job: ImageJob, width: int, height: int) -> Optional[QtGui.QImage]:
        view = self._view
        if view is None:
            return None
        # Images showing several findings make the part see-through, so
        # findings on the far side still show. Close-ups face their finding.
        see_through = 0 if job.key.startswith("finding:") else self.settings.see_through
        self._highlight_job(job.findings, see_through)

        overview = job.key in ("overview", "design")
        name = self.settings.overview_view if overview else self.settings.view_direction
        zoom = self.settings.framing == "zoom" and job.findings and not overview
        self._place_camera(name, job.findings, job.findings if zoom else [], width / max(height, 1))

        background = "#ffffff" if self.settings.background == "white" else "Current"
        path = os.path.join(self._tmp, "capture.png")
        try:
            Gui.updateGui()
            view.saveImage(path, int(width), int(height), background)
            image = QtGui.QImage(path)
        except Exception as exc:
            App.Console.PrintWarning(f"DFM: could not capture a report image: {exc}\n")
            return None
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        return None if image.isNull() else image

    # -- Highlight -------------------------------------------------------

    def _highlight_job(self, findings: list[CheckResult], see_through: int = 0) -> None:
        """Highlight *findings*: in full color, with the rest of the part plain or faded.

        *see_through* is the transparency of the rest of the part in percent;
        0 is opaque. Findings themselves always stay opaque.
        """
        faces, edges = [], []
        for f in findings:
            color = severity_color(f.severity)
            for ref in f.refs:
                if ref.type == "Face":
                    faces.append((ref.index, color))
                elif ref.type == "Edge":
                    edges.append((ref.index, color))
        if self.settings.fade_rest:
            rest = _REST_FADED[:3]
        else:
            try:
                rest = tuple(self.bridge.target_object.ViewObject.ShapeColor[:3])
            except Exception:
                rest = None
        self.bridge.set_display_style(
            rest_color=rest, rest_transparency=min(max(int(see_through), 0), 90) / 100.0)
        self.bridge.highlight_faces_and_edges_by_index(faces, edges)
        self.bridge.remove_finding_overlay()

    # -- Camera ----------------------------------------------------------

    def _camera_node(self):
        try:
            return self._view.getCameraNode() if self._view is not None else None
        except Exception:
            return None

    def _direction(self, name: str, findings: list[CheckResult]) -> Vec:
        if name == "current" and self._start_q:
            return rotate(self._start_q, (0.0, 0.0, -1.0))
        if name in EYES:
            return _mul(_norm(EYES[name]), -1.0)
        if name == "best" and findings:
            return best_direction(self._mean_normal(findings))
        return _mul(_norm(EYES["isometric"]), -1.0)

    def _place_camera(self, name: str, highlighted: list[CheckResult],
                      framed: list[CheckResult], aspect: float) -> None:
        """Look the way *name* says and fit the *framed* findings (the whole model if none)."""
        direction = self._direction(name, highlighted)
        cam = self._camera_node()
        shape = getattr(self.bridge.target_object, "Shape", None)
        if cam is None or shape is None:
            self._view.fitAll()
            return
        model = bound_box_corners(shape.BoundBox)
        target = [c for sub in self._subshapes(framed) for c in bound_box_corners(sub.BoundBox)]
        ortho = "Orthographic" in str(cam.getTypeId().getName())
        if target:
            padding = max(dfm_params().GetFloat("ResultsCameraPadding", 1.7), 1.2)
            # Edges are thin, so frame more of the part around them than around faces.
            only_edges = all(ref.type == "Edge" for f in framed for ref in f.refs)
            min_fraction = 0.55 if only_edges else 0.35
        else:
            target, padding, min_fraction = model, 1.12, 0.0
        up_hint = (0.0, 0.0, 1.0)
        if name == "current" and self._start_q:
            up_hint = rotate(self._start_q, (0.0, 1.0, 0.0))
        try:
            angle = float(cam.heightAngle.getValue()) if not ortho else math.pi / 4
        except Exception:
            angle = math.pi / 4
        setup = frame(direction, target, model, aspect, padding, ortho, angle, up_hint, min_fraction)

        from pivy import coin

        cam.orientation.setValue(coin.SbRotation(*setup.orientation))
        cam.position.setValue(*setup.position)
        cam.focalDistance.setValue(setup.focal_distance)
        cam.nearDistance.setValue(setup.near)
        cam.farDistance.setValue(setup.far)
        if ortho:
            cam.height.setValue(setup.height)

    def _restore_camera(self) -> None:
        cam = self._camera_node()
        if cam is not None and self._camera_fields:
            for name, value in self._camera_fields.items():
                try:
                    getattr(cam, name).setValue(value)
                except Exception:
                    pass
        elif self._view is not None and self._camera:
            self._view.setCamera(self._camera)

    def _set_navi_cube(self, on: bool) -> bool:
        """Show or hide the navigation cube; returns whether it was shown before."""
        try:
            viewer = self._view.getViewer()
            was = bool(viewer.isEnabledNaviCube())
            if was != on:
                viewer.setEnabledNaviCube(on)
            return was
        except Exception:
            return False

    # -- Geometry --------------------------------------------------------

    def _subshapes(self, findings: list[CheckResult]):
        shape = getattr(self.bridge.target_object, "Shape", None)
        if shape is None:
            return
        for f in findings:
            for ref in f.refs:
                try:
                    if ref.type == "Face":
                        yield shape.Faces[ref.index]
                    elif ref.type == "Edge":
                        yield shape.Edges[ref.index]
                except (IndexError, AttributeError):
                    continue

    def _mean_normal(self, findings: list[CheckResult]) -> Optional[Vec]:
        total = (0.0, 0.0, 0.0)
        for sub in self._subshapes(findings):
            if not hasattr(sub, "normalAt"):
                continue
            try:
                u0, u1, v0, v1 = sub.ParameterRange
                n = sub.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
                area = max(float(sub.Area), 1e-9)
            except Exception:
                continue
            total = _add(total, _mul((n.x, n.y, n.z), area))
        return _norm(total)
