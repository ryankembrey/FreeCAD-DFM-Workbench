# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The nominal wall: the thickness most of a moulded part's walls share.

Most moulding rules are ratios against it (a boss wall about 60% of it, a
rib at most 3 times it tall...), so it is set once per analysis, as the
``NOMINAL_WALL_THICKNESS`` process requirement. Its value is plain,
JSON-safe data, so it can be kept with a run's settings::

    {"mode": "auto"}                    measure it on the part
    {"mode": "manual", "value": 2.5}    use this (mm, > 0)

Measuring (``measure_nominal_wall``): rays are cast inward from a few points
on every plain wall face (faces no feature owns, or owned only by a pocket,
cutout, step, slot or an unrecognised protrusion or depression). A ray that leaves the part through a face turned the
opposite way has crossed a wall. The nominal wall is the area-weighted
median of those crossings, so a few thick bosses or thin ribs don't move it.

Pure OCP: no FreeCAD, so it is tested in plain Python.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from OCP.BRepTopAdaptor import BRepTopAdaptor_FClass2d
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.TopoDS import TopoDS_Face, TopoDS_Shape
from OCP.gp import gp_Lin

from ..recognition.model import FeatureKind, RecognitionResult
from .geometry import (
    FaceIndex,
    face_area,
    get_face_uv_center,
    get_face_uv_normal,
    get_point_from_uv,
    is_point_on_face,
    yield_face_uv_grid,
)

AUTO = "auto"
MANUAL = "manual"

#: Where a nominal wall came from (``PartFeatures.nominal_source``).
SOURCE_MEASURED = "measured"
SOURCE_MANUAL = "manual"
SOURCE_NONE = "none"

#: Features whose faces are walls of the part: their floors and side walls
#: are the part's own walls. So are the regions no recognizer claimed
#: (protrusions and depressions, such as a pad thickening a floor): they
#: are material added to or taken from a wall, not a feature with rules of
#: its own. Faces of every other feature (bosses, ribs, holes, blends,
#: snap fits...) are not used.
WALL_OWNERS = frozenset({FeatureKind.POCKET, FeatureKind.CUTOUT, FeatureKind.STEP, FeatureKind.SLOT,
                         FeatureKind.PROTRUSION, FeatureKind.DEPRESSION})
#: Features standing up from a wall: their caps and tips are not walls
#: either, though some (a flush rib top) are only their support faces.
_STANDING = frozenset({FeatureKind.BOSS, FeatureKind.RIB, FeatureKind.SNAP_FIT, FeatureKind.LIP})

#: Faces smaller than this share of the part's area are left out.
MIN_FACE_SHARE = 0.005
#: Most points sampled on one face.
SAMPLES_PER_FACE = 5
#: A crossing counts when the exit face is turned at least this far against
#: the face the ray came from (the two sides of one wall face each other).
OPPOSITE_DOT = -0.85
#: Hits this close are a ray grazing an edge, not a wall (as
#: ``EDGE_CROSSING_GAP`` in the ray thickness analyzer).
EDGE_GAP = 0.02
#: Fewer crossings than this and the part has no clear nominal wall.
MIN_SAMPLES = 3
#: Ray start, just inside the face, so the ray doesn't hit its own face.
_EPSILON = 1e-4
_INTERSECTOR_TOL = 1e-3


# -- The requirement value ------------------------------------------------------


def make_requirement(mode: str = AUTO, value: Optional[float] = None) -> dict:
    """The requirement value for *mode* ("auto" or "manual", with *value* in mm)."""
    if mode == MANUAL and value is not None:
        return {"mode": MANUAL, "value": float(value)}
    return {"mode": AUTO}


def parse_requirement(value: Any) -> tuple[str, Optional[float]]:
    """(mode, mm or None) from a requirement value.

    Anything missing or malformed, or a manual value that isn't a positive
    number, reads as Auto: the part is measured rather than the run failing.
    """
    if isinstance(value, dict) and value.get("mode") == MANUAL:
        try:
            mm = float(value.get("value"))
        except (TypeError, ValueError):
            return AUTO, None
        if mm > 0:
            return MANUAL, mm
    return AUTO, None


def describe(value: Optional[float], source: str) -> str:
    """The value with where it came from, for messages: "2.48 mm (measured)"."""
    if value is None:
        return "not measured"
    how = "entered" if source == SOURCE_MANUAL else "measured"
    return f"{value:.2f} mm ({how})"


# -- Resolving and measuring ----------------------------------------------------------


def resolve_nominal_wall(
    shape: TopoDS_Shape,
    face_index: FaceIndex,
    result: RecognitionResult,
    requirement: Any,
) -> tuple[Optional[float], str]:
    """(mm or None, source) for the analysis: the entered value, or measured."""
    mode, mm = parse_requirement(requirement)
    if mode == MANUAL:
        return mm, SOURCE_MANUAL
    measured = measure_nominal_wall(shape, face_index, result)
    if measured is None:
        return None, SOURCE_NONE
    return measured, SOURCE_MEASURED


def measure_nominal_wall(
    shape: TopoDS_Shape, face_index: FaceIndex, result: RecognitionResult
) -> Optional[float]:
    """The nominal wall of *shape* in mm, or None when it has no clear walls.

    A solid block has no shell, so the rays cross the whole part: its value
    is its thickness across its largest faces (its smallest dimension, for a
    box), as the area weighting favours the largest faces.
    """
    if len(face_index) != result.face_count:
        return None
    excluded = not_wall_faces(result)
    areas = {i: abs(face_area(face_index.face_at(i))) for i in range(1, len(face_index) + 1)}
    total = sum(areas.values())
    if total <= 0:
        return None

    intersector = IntCurvesFace_ShapeIntersector()
    intersector.Load(shape, _INTERSECTOR_TOL)

    weighted: list[tuple[float, float]] = []
    for i in range(1, len(face_index) + 1):
        if i in excluded or areas[i] < MIN_FACE_SHARE * total:
            continue
        found = crossings(face_index.face_at(i), intersector, face_index, excluded)
        for thickness in found:
            weighted.append((thickness, areas[i] / len(found)))
    if len(weighted) < MIN_SAMPLES:
        return None
    return round(weighted_median(weighted), 4)


def not_wall_faces(result: RecognitionResult) -> set[int]:
    """Faces that are not plain walls: owned by a feature other than a pocket,
    cutout, step, slot or unclaimed region, or the cap or tip a boss or rib
    stands up to (its supports other than the base faces it names)."""
    out: set[int] = set()
    for feature in result.features:
        if feature.kind not in WALL_OWNERS:
            out.update(feature.faces)
        bases = feature.params.get("base_faces")
        if feature.kind in _STANDING and bases:
            # Its supports besides its base are what it stands up to (a cap, a
            # flush top). A feature that doesn't name its base (a snap fit, a
            # lip) only stands on its supports: they are walls.
            out.update(f for f in feature.support_faces if f not in set(bases))
    return out


def sample_points(face: TopoDS_Face, count: int = SAMPLES_PER_FACE) -> list[tuple[float, float]]:
    """Up to *count* (u, v) points on *face*: its centre, then a grid inside it."""
    classifier = BRepTopAdaptor_FClass2d(face, 1e-6)
    points = []
    u, v = get_face_uv_center(face)
    if is_point_on_face(u, v, face, classifier):
        points.append((u, v))
    for uv in yield_face_uv_grid(face, 2, margin=0.25):
        if len(points) >= count:
            break
        points.append(uv)
    return points


def crossings(
    face: TopoDS_Face,
    intersector: IntCurvesFace_ShapeIntersector,
    face_index: FaceIndex,
    excluded: Iterable[int] = (),
) -> list[float]:
    """The wall thicknesses measured by rays cast inward from points on *face*.

    A ray counts when the first face it leaves the part through is turned
    against *face* (dot < OPPOSITE_DOT) and is a wall face itself (not in
    *excluded*).
    """
    excluded = set(excluded)
    out = []
    for u, v in sample_points(face):
        normal = get_face_uv_normal(face, u, v)
        if normal is None:
            continue
        inward = normal.Reversed()
        start = get_point_from_uv(face, inward, u, v, _EPSILON)
        intersector.Perform(gp_Lin(start, inward), 0.0, 1e30)
        if not intersector.IsDone():
            continue
        hits = sorted(
            (intersector.WParameter(k), k) for k in range(1, intersector.NbPnt() + 1)
            if intersector.WParameter(k) > EDGE_GAP
        )
        if not hits:
            continue
        w, k = hits[0]
        exit_face = intersector.Face(k)
        exit_normal = get_face_uv_normal(exit_face, intersector.UParameter(k), intersector.VParameter(k))
        if exit_normal is None or normal.Dot(exit_normal) >= OPPOSITE_DOT:
            continue
        if face_index.index_of(exit_face) in excluded:
            continue
        out.append(w + _EPSILON)
    return out


def weighted_median(pairs: Iterable[tuple[float, float]]) -> float:
    """The value at half the total weight of (value, weight) pairs."""
    ordered = sorted(pairs)
    total = sum(w for _, w in ordered)
    if not ordered or total <= 0:
        raise ValueError("weighted_median of nothing")
    running = 0.0
    for value, weight in ordered:
        running += weight
        if running >= total / 2.0:
            return value
    return ordered[-1][0]
