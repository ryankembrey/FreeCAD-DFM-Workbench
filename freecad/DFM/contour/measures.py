# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Contour measures: turn a UniformMesh into a per-triangle scalar field."""

import math

import FreeCAD as App  # type: ignore


class BoolOption:
    """A simple on/off option a measure exposes to the panel as a checkbox."""

    def __init__(self, id, label, default=False, tooltip=""):
        self.id = id
        self.label = label
        self.default = default
        self.tooltip = tooltip


class ChoiceOption:
    """A pick-one option a measure exposes to the panel as a dropdown."""

    def __init__(self, id, label, choices, default=None, tooltip=""):
        self.id = id
        self.label = label
        self.choices = list(choices)
        self.default = (
            default if default is not None else (self.choices[0] if self.choices else None)
        )
        self.tooltip = tooltip


class ContourMeasure:
    """Base interface for a per-triangle scalar field."""

    id = "measure"
    label = "Value"
    unit = ""
    default_colormap = "Turbo"
    default_range = (-1.0, 1.0)
    # Spin-box step and decimals for the range control (per unit).
    range_step = 1.0
    range_decimals = 0
    # Fixed domain shown on the legend, or None to use the data's own min/max.
    range_limits = None
    needs_pull_direction = False
    # Optional on/off toggles (list of BoolOption) rendered by the panel.
    options = []

    def value_limits(self, opts):
        """Legend domain given the current option values (None for auto)."""
        return self.range_limits

    def initial_range(self, opts):
        """Default [low, high] given the current option values."""
        return self.default_range

    def format_value(self, value: float) -> str:
        return f"{value:+.1f} {self.unit}".strip()

    def measure(self, shape, mesh, pull=None, options=None, progress_cb=None, check_abort=None):
        """Return (values, normals): one scalar and one outward unit normal per
        triangle. Normals are used for the overlay's lighting."""
        raise NotImplementedError


# --- geometry helpers (shared by measures that need outward orientation) ------


def triangle_normal(vertices, tri):
    """Unit facet normal from the edge cross product, or None for a sliver."""
    ia, ib, ic = tri
    ax, ay, az = vertices[ia]
    bx, by, bz = vertices[ib]
    cx, cy, cz = vertices[ic]
    e1x, e1y, e1z = bx - ax, by - ay, bz - az
    e2x, e2y, e2z = cx - ax, cy - ay, cz - az
    nx = e1y * e2z - e1z * e2y
    ny = e1z * e2x - e1x * e2z
    nz = e1x * e2y - e1y * e2x
    nlen = math.sqrt(nx * nx + ny * ny + nz * nz)
    if nlen < 1e-12:
        return None
    return (nx / nlen, ny / nlen, nz / nlen)


def _signed_volume6(vertices, triangles) -> float:
    total = 0.0
    for ia, ib, ic in triangles:
        ax, ay, az = vertices[ia]
        bx, by, bz = vertices[ib]
        cx, cy, cz = vertices[ic]
        total += ax * (by * cz - bz * cy) + ay * (bz * cx - bx * cz) + az * (bx * cy - by * cx)
    return total


def _probe_global_flip(shape, vertices, triangles, eps, tol, samples=64) -> bool:
    n_tri = len(triangles)
    if n_tri == 0:
        return False
    step = max(1, n_tri // samples)
    flip_votes = keep_votes = 0
    for idx in range(0, n_tri, step):
        normal = triangle_normal(vertices, triangles[idx])
        if normal is None:
            continue
        nx, ny, nz = normal
        ia, ib, ic = triangles[idx]
        ax, ay, az = vertices[ia]
        bx, by, bz = vertices[ib]
        cx, cy, cz = vertices[ic]
        gx = (ax + bx + cx) / 3.0
        gy = (ay + by + cy) / 3.0
        gz = (az + bz + cz) / 3.0
        test = App.Vector(gx + nx * eps, gy + ny * eps, gz + nz * eps)
        try:
            inside = shape.isInside(test, tol, True)
        except Exception:
            continue
        if inside:
            flip_votes += 1
        else:
            keep_votes += 1
    return flip_votes > keep_votes


def determine_global_flip(shape, vertices, triangles, eps, tol) -> bool:
    """True if geometric facet normals must be flipped to point outward."""
    v6 = _signed_volume6(vertices, triangles)
    bbox = shape.BoundBox
    bbox_volume = max(bbox.XLength * bbox.YLength * bbox.ZLength, 1e-9)
    if abs(v6) > bbox_volume * 1e-3:
        return v6 < 0.0
    return _probe_global_flip(shape, vertices, triangles, eps, tol)


def outward_normals(shape, mesh):
    """Per-triangle outward unit normals: the true CAD normal aligned to the
    outward-facing facet, falling back to the facet normal when no CAD normal
    is available."""
    vertices = mesh.vertices
    triangles = mesh.triangles
    cad = mesh.cad_normals
    diag = shape.BoundBox.DiagonalLength
    eps = max(diag * 1e-4, 1e-6)
    tol = eps * 0.5
    flip = determine_global_flip(shape, vertices, triangles, eps, tol)
    has_cad = cad is not None and len(cad) == len(triangles)

    normals = []
    for idx, tri in enumerate(triangles):
        facet = triangle_normal(vertices, tri)
        if facet is None:
            fx, fy, fz = 0.0, 0.0, 1.0
        else:
            fx, fy, fz = facet
            if flip:
                fx, fy, fz = -fx, -fy, -fz
        c = cad[idx] if has_cad else None
        if c is None:
            normals.append((fx, fy, fz))
        else:
            nx, ny, nz = c
            if nx * fx + ny * fy + nz * fz < 0.0:
                nx, ny, nz = -nx, -ny, -nz
            normals.append((nx, ny, nz))
    return normals


# --- draft --------------------------------------------------------------------


def draft_angle_for_normal(nx, ny, nz, pull) -> float:
    """Signed draft in degrees: 0 = vertical wall, +90 = normal along pull,
    -90 = normal against pull."""
    dot = nx * pull.x + ny * pull.y + nz * pull.z
    dot = max(-1.0, min(1.0, dot))
    angle = math.degrees(math.acos(dot))
    if angle < 1e-5:
        return 90.0
    if angle > 180.0 - 1e-5:
        return -90.0
    return angle - 90.0


class DraftMeasure(ContourMeasure):
    id = "draft"
    label = "Draft Angle"
    unit = "°"
    default_colormap = "Turbo"
    default_range = (-15.0, 15.0)
    range_limits = (-90.0, 90.0)
    needs_pull_direction = True
    options = [
        BoolOption(
            "magnitude",
            "Magnitude Only",
            False,
            "Color by |draft| so both mold-half orientations read the same; "
            "the scale becomes 0 to 90.",
        )
    ]

    def value_limits(self, opts):
        return (0.0, 90.0) if opts.get("magnitude") else (-90.0, 90.0)

    def initial_range(self, opts):
        return (0.0, 15.0) if opts.get("magnitude") else (-15.0, 15.0)

    def measure(self, shape, mesh, pull=None, options=None, progress_cb=None, check_abort=None):
        options = options or {}
        magnitude = bool(options.get("magnitude"))
        if pull is None:
            pull = App.Vector(0, 0, 1)
        pull = App.Vector(pull).normalize()
        normals = outward_normals(shape, mesh)
        n_tri = len(normals)
        values = []
        for idx, (nx, ny, nz) in enumerate(normals):
            if check_abort and (idx & 0x3FFF) == 0 and check_abort():
                break
            v = draft_angle_for_normal(nx, ny, nz, pull)
            values.append(abs(v) if magnitude else v)
            if progress_cb and (idx & 0x1FFF) == 0:
                progress_cb(idx, n_tri)
        if progress_cb:
            progress_cb(n_tri, n_tri)
        return values, normals


def _triangle_centroid(vertices, tri):
    a, b, c = tri
    ax, ay, az = vertices[a]
    bx, by, bz = vertices[b]
    cx, cy, cz = vertices[c]
    return ((ax + bx + cx) / 3.0, (ay + by + cy) / 3.0, (az + bz + cz) / 3.0)


def _triangle_scale(vertices, tri):
    """Mean edge length: a safe outward margin that clears the facet's sag."""
    a, b, c = tri
    pa, pb, pc = vertices[a], vertices[b], vertices[c]

    def d(p, q):
        return math.sqrt((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2)

    return (d(pa, pb) + d(pb, pc) + d(pc, pa)) / 3.0


class ThicknessMeasure(ContourMeasure):
    id = "thickness"
    label = "Thickness"
    unit = "mm"
    default_colormap = "Turbo"
    default_range = None  # auto: taken from the data's own min/max
    range_limits = None
    range_step = 0.5
    range_decimals = 2
    needs_pull_direction = False
    options = [
        ChoiceOption(
            "method",
            "Method",
            ["Rays (faster)", "Shrinking Sphere (slower)"],
            "Rays (faster)",
            "Rays are fast; the shrinking sphere is slower but more robust in "
            "tight or angled regions.",
        )
    ]

    def measure(self, shape, mesh, pull=None, options=None, progress_cb=None, check_abort=None):
        options = options or {}
        method = options.get("method", "Rays")

        # Outward normals give lighting and the inward measurement direction.
        normals = outward_normals(shape, mesh)

        try:
            from ..core.utils.conversion import freecad_to_ocp
            from .thickness_backend import make_backend

            ocp_shape = freecad_to_ocp(shape)
            backend = make_backend(method, ocp_shape)
        except Exception as exc:
            raise RuntimeError(
                f"Thickness analysis needs OCP and the FreeCAD-to-OCP conversion. {exc}"
            )

        vertices = mesh.vertices
        triangles = mesh.triangles
        n_tri = len(triangles)
        values = []
        for idx, tri in enumerate(triangles):
            if check_abort and (idx & 0x3FF) == 0 and check_abort():
                break
            centroid = _triangle_centroid(vertices, tri)
            margin = _triangle_scale(vertices, tri)
            try:
                thickness = backend.at(centroid, normals[idx], margin)
            except Exception:
                thickness = 0.0
            values.append(thickness)
            if progress_cb and (idx & 0xFF) == 0:
                progress_cb(idx, n_tri)
        if progress_cb:
            progress_cb(n_tri, n_tri)
        return values, normals
