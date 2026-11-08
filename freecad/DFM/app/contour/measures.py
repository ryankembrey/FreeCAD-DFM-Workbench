# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Contour measures: turn a UniformMesh into a per-triangle scalar field.

A ContourMeasure is the one piece each contour tool provides. The shared task
panel reads its metadata (label, unit, colormap, range, whether it needs a pull
direction) to build the UI, and calls measure() to compute the values.

A measure is either continuous (draft, thickness, curvature: a color map over a
range) or categorical (undercut: each value is a category code with a fixed
color, listed in an interactive legend). A value may also be "no value"
(math.inf), such as a flat face for curvature: it is drawn neutral gray.
"""

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


class Category:
    """One category of a categorical measure: its code, name and color."""

    def __init__(self, code, label, rgb, tooltip="", navigable=False):
        self.code = int(code)
        self.label = label
        self.rgb = tuple(rgb)
        self.tooltip = tooltip
        # The legend offers to step the camera through its regions.
        self.navigable = navigable


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
    # What the progress bar says while measuring.
    progress_text = "Measuring"
    # Optional on/off toggles (list of BoolOption) rendered by the panel.
    options = []
    # The mesh must carry per-triangle curvature (see meshing.py).
    needs_curvature = False
    # The panel shows a side actions list; their directions arrive in
    # options["side_actions"] as [{"dir": (x, y, z), "ref": "+X"}, ...].
    needs_side_actions = False
    # Shown in the legend next to the gray used for values with no value (inf).
    no_value_label = None

    def categories_for(self, opts):
        """The categories (list of Category) of a categorical measure, else None."""
        return None

    def is_categorical(self):
        return bool(self.categories_for({}))

    def data_domain(self, dmin, dmax, opts):
        """Legend domain once the data is known (dmin/dmax: finite values only)."""
        return self.value_limits(opts) or (dmin, dmax)

    def initial_range_for_data(self, values, opts):
        """First [low, high] when initial_range() gives None; None = data min/max."""
        return None

    def probe_text(self, value):
        """Text for a probe placed on this value, or None for the default."""
        return None

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
    return outward_normals_and_flip(shape, mesh)[0]


def outward_normals_and_flip(shape, mesh):
    """(outward normals, flip): flip is True when the triangles' winding
    normals point into the part, so values signed against the winding (such
    as curvature) must change sign to be signed against the outside."""
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
        c = cad[idx] if (has_cad and cad is not None) else None
        if c is None:
            normals.append((fx, fy, fz))
        else:
            nx, ny, nz = c
            if nx * fx + ny * fy + nz * fz < 0.0:
                nx, ny, nz = -nx, -ny, -nz
            normals.append((nx, ny, nz))
    return normals, flip


def draft_angle_for_normal(nx, ny, nz, pull) -> float:
    """Signed draft in degrees: 0 = vertical wall, +90 = normal along pull,
    -90 = normal against pull. Continuous across the whole range (no jump at the
    poles), so adjacent faces never differ wildly for near-identical normals."""
    dot = nx * pull.x + ny * pull.y + nz * pull.z
    dot = max(-1.0, min(1.0, dot))
    return 90.0 - math.degrees(math.acos(dot))


class DraftMeasure(ContourMeasure):
    id = "draft"
    label = "Draft Angle"
    unit = "°"
    default_colormap = "Cool-Warm"
    default_range = (-3.0, 3.0)
    range_limits = (-90.0, 90.0)
    needs_pull_direction = True
    progress_text = "Measuring draft angles"
    options = [
        BoolOption(
            "magnitude",
            "Magnitude only",
            False,
            "Color by |draft| so both mold-half orientations read the same; "
            "the scale becomes 0 to 90.",
        )
    ]

    def value_limits(self, opts):
        return (0.0, 90.0) if opts.get("magnitude") else (-90.0, 90.0)

    def initial_range(self, opts):
        return (0.0, 3.0) if opts.get("magnitude") else (-3.0, 3.0)

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
    default_colormap = "Viridis"
    default_range = None  # auto: taken from the data's own min/max
    range_limits = None
    range_step = 0.5
    range_decimals = 2
    needs_pull_direction = False
    progress_text = "Measuring wall thickness"
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

        vertices = mesh.vertices
        triangles = mesh.triangles
        tasks = [
            (_triangle_centroid(vertices, tri), normals[idx], _triangle_scale(vertices, tri))
            for idx, tri in enumerate(triangles)
        ]

        try:
            from .thickness_parallel import measure_thickness
        except Exception as exc:
            raise RuntimeError(f"Thickness analysis backend unavailable. {exc}")

        values = measure_thickness(
            shape, tasks, method, progress_cb=progress_cb, check_abort=check_abort
        )
        return values, normals


# -- Undercut ------------------------------------------------------------------

_MOLD_TWO = "Two halves (±pull)"
_MOLD_ONE = "One direction (+pull)"


class UndercutMeasure(ContourMeasure):
    """Which way each part of the surface releases from the mold.

    Categorical: see undercut_backend.py for the codes. Uses the same ray test
    as the DFM undercut rule, and the same side actions.
    """

    id = "undercut"
    label = "Release direction"
    unit = ""
    default_colormap = "Release direction"  # a categorical palette (colormap.py)
    default_range = (0.0, 9.0)
    range_limits = (0.0, 9.0)
    range_step = 1.0
    needs_pull_direction = True
    needs_side_actions = True
    progress_text = "Casting release rays"
    options = [
        ChoiceOption(
            "mold",
            "Mold",
            [_MOLD_TWO, _MOLD_ONE],
            _MOLD_TWO,
            "Two halves: a face is fine if it releases along or against the pull "
            "(core or cavity side). One direction: it must release along the pull, "
            "as for a fixed die half or 3-axis machining reach.",
        )
    ]

    def categories_for(self, opts):
        from .colormap import categorical_color

        def cat(code, label, tip, nav=False):
            return Category(code, label, categorical_color("Release direction", code), tip, nav)

        sides = (opts or {}).get("side_actions") or []
        two = (opts or {}).get("mold", _MOLD_TWO) != _MOLD_ONE
        if two:
            out = [
                cat(1, "Releases along pull (core)", "Open along the pull: formed by the core side."),
                cat(2, "Releases against pull (cavity)",
                    "Open against the pull: formed by the cavity side."),
                cat(0, "Either side (straddle)",
                    "Open both ways, such as an outer wall. The parting line can run here; "
                    "it needs draft either way."),
            ]
        else:
            out = [cat(1, "Reachable along pull", "Open along the pull direction.")]
        for i, _side in enumerate(sides):
            out.append(cat(4 + i, f"Side action {i + 1}",
                           f"Trapped along the pull, but side action {i + 1} pulls straight off it.",
                           True))
        out.append(cat(3, "Undercut",
                       "Blocked along and against the pull, and no side action frees it.", True))
        return out

    def value_limits(self, opts):
        return (0.0, 9.0)

    def initial_range(self, opts):
        return (0.0, 9.0)

    def _label_of(self, value):
        code = int(round(value))
        if code == 0:
            return "Either side"
        if code == 1:
            return "Releases along pull"
        if code == 2:
            return "Releases against pull"
        if code == 3:
            return "Undercut"
        return f"Side action {code - 3}"

    def format_value(self, value: float) -> str:
        return self._label_of(value)

    def probe_text(self, value):
        return self._label_of(value)

    def measure(self, shape, mesh, pull=None, options=None, progress_cb=None, check_abort=None):
        from ..prefs import dfm_params

        options = options or {}
        if pull is None:
            pull = App.Vector(0, 0, 1)
        pull = App.Vector(pull).normalize()
        normals = outward_normals(shape, mesh)

        vertices = mesh.vertices
        tasks = [
            (_triangle_centroid(vertices, tri), normals[idx], 0.5 * _triangle_scale(vertices, tri))
            for idx, tri in enumerate(mesh.triangles)
        ]
        sides = []
        for item in options.get("side_actions") or []:
            d = item.get("dir") if isinstance(item, dict) else item
            if d is not None:
                sides.append(tuple(float(c) for c in d))
        params = dfm_params()
        diag = shape.BoundBox.DiagonalLength
        config = {
            "pull": (pull.x, pull.y, pull.z),
            "two_sided": options.get("mold", _MOLD_TWO) != _MOLD_ONE,
            "side_actions": sides,
            "self_hit": params.GetFloat("UndercutSelfHitThreshold", 0.05),
            "lift": max(diag * 1e-6, 1e-4),
        }

        try:
            from .thickness_parallel import measure_points, UNDERCUT_BACKEND
        except Exception as exc:
            raise RuntimeError(f"Undercut ray backend unavailable. {exc}")

        values = measure_points(
            shape, tasks, config, UNDERCUT_BACKEND, progress_cb=progress_cb, check_abort=check_abort
        )
        return [float(v) for v in values], normals


# -- Curvature -----------------------------------------------------------------

_SHOW_BOTH = "Concave and convex"
_SHOW_CONCAVE = "Concave only (internal)"
_SHOW_CONVEX = "Convex only (external)"

# A face curving more gently than this many part diagonals is drawn as flat.
_FLAT_RADIUS_FACTOR = 10.0


def _nice_ceil(x):
    """The next 1, 2, 2.5 or 5 times a power of ten at or above x."""
    if x <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(x))
    for m in (1, 2, 2.5, 5, 10):
        if x <= m * mag * (1 + 1e-9):
            return m * mag
    return 10 * mag


class CurvatureMeasure(ContourMeasure):
    """Tightest radius of curvature, 1 / max |k|, at each point (mm).

    Curvature is evaluated on the exact CAD surfaces during meshing (see
    curvature.py), so it barely depends on the mesh resolution. Flat faces,
    and faces filtered out by "Show", have no value and are drawn gray.
    """

    id = "curvature"
    label = "Radius"
    unit = "mm"
    default_colormap = "Viridis"
    default_range = None  # from the data (initial_range_for_data)
    range_limits = None
    range_step = 0.5
    range_decimals = 2
    needs_curvature = True
    progress_text = "Measuring curvature"
    no_value_label = "Flat"
    options = [
        ChoiceOption(
            "show",
            "Show",
            [_SHOW_BOTH, _SHOW_CONCAVE, _SHOW_CONVEX],
            _SHOW_BOTH,
            "Concave: internal fillets and hole walls. Convex: external rounds and "
            "bosses. Faces left out are drawn gray, like flat faces.",
        )
    ]

    def data_domain(self, dmin, dmax, opts):
        return (0.0, max(dmax, 1e-3))

    def initial_range_for_data(self, values, opts):
        finite = sorted(v for v in values if math.isfinite(v))
        if not finite:
            return None
        p90 = finite[int(0.9 * (len(finite) - 1))]
        return (0.0, min(_nice_ceil(p90), _nice_ceil(finite[-1])))

    def format_value(self, value: float) -> str:
        if not math.isfinite(value):
            return "Flat"
        return f"R {value:.2f} mm"

    def probe_text(self, value):
        return self.format_value(value)

    def measure(self, shape, mesh, pull=None, options=None, progress_cb=None, check_abort=None):
        options = options or {}
        show = options.get("show", _SHOW_BOTH)
        curv = getattr(mesh, "curvatures", None)
        if curv is None or len(curv) != len(mesh.triangles):
            raise RuntimeError("The mesh has no curvature data; mesh it again.")
        normals, flip = outward_normals_and_flip(shape, mesh)
        diag = max(shape.BoundBox.DiagonalLength, 1e-9)
        k_flat = 1.0 / (_FLAT_RADIUS_FACTOR * diag)
        inf = math.inf
        n = len(curv)
        values = []
        for idx, k in enumerate(curv):
            if check_abort and (idx & 0x3FFF) == 0 and check_abort():
                break
            if k is None or not math.isfinite(k) or abs(k) < k_flat:
                values.append(inf)
                continue
            if flip:
                k = -k
            if (show == _SHOW_CONCAVE and k > 0.0) or (show == _SHOW_CONVEX and k < 0.0):
                values.append(inf)
                continue
            values.append(1.0 / abs(k))
            if progress_cb and (idx & 0x1FFF) == 0:
                progress_cb(idx, n)
        while len(values) < n:
            values.append(inf)
        if progress_cb:
            progress_cb(n, n)
        return values, normals


# -- Registry ------------------------------------------------------------------

MEASURES = {
    "draft": DraftMeasure,
    "thickness": ThicknessMeasure,
    "undercut": UndercutMeasure,
    "curvature": CurvatureMeasure,
}


def measure_for_id(measure_id):
    """A new measure for a saved analysis's Measure id (draft if unknown)."""
    return MEASURES.get(measure_id, DraftMeasure)()
