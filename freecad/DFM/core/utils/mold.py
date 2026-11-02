# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from enum import Enum, auto

from OCP.TopoDS import TopoDS_Face
from OCP.gp import gp_Dir, gp_Lin, gp_Vec
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

from ...core.utils import get_face_uv_normal, get_face_uv_center, get_point_from_uv


class MoldSide(Enum):
    CORE = auto()
    CAVITY = auto()


def moldside_of_face(
    face: TopoDS_Face, intersector: IntCurvesFace_ShapeIntersector, pull_direction: gp_Dir
) -> MoldSide:
    """
    Classifies a face as CORE or CAVITY strictly via Topological Visibility.
    """
    u, v = get_face_uv_center(face)
    norm = get_face_uv_normal(face, u, v)
    if not norm:
        return MoldSide.CAVITY

    offset = 1e-4
    point = get_point_from_uv(face, norm, u, v, offset)

    # Up (+ pull dir)
    intersector.Perform(gp_Lin(point, pull_direction), 0.0, float("inf"))
    hits_up = False
    if intersector.IsDone():
        for i in range(1, intersector.NbPnt() + 1):
            if not intersector.Face(i).IsSame(face):
                hits_up = True
                break

    # Down (+ pull dir)
    intersector.Perform(gp_Lin(point, pull_direction.Reversed()), 0.0, float("inf"))
    hits_down = False
    if intersector.IsDone():
        for i in range(1, intersector.NbPnt() + 1):
            if not intersector.Face(i).IsSame(face):
                hits_down = True
                break

    if hits_down and not hits_up:
        return MoldSide.CORE

    elif hits_up and not hits_down:
        return MoldSide.CAVITY

    else:  # Zero hits and double hits
        pull_vec = gp_Vec(pull_direction)
        norm_vec = gp_Vec(norm)

        # Get horizontal component of vector
        dot_product = norm_vec.Dot(pull_vec)
        vertical_component = pull_vec.Multiplied(dot_product)
        horiz_vec = norm_vec.Subtracted(vertical_component)

        # No horizontal component (flat)
        if horiz_vec.Magnitude() < 1e-4:
            return MoldSide.CORE if norm.Dot(pull_direction) > 0.0 else MoldSide.CAVITY

        horiz_dir = gp_Dir(horiz_vec)

        # Cast ray horizontally outward
        intersector.Perform(gp_Lin(point, horiz_dir), 0.0, float("inf"))
        hits_horiz = False

        if intersector.IsDone():
            for i in range(1, intersector.NbPnt() + 1):
                if not intersector.Face(i).IsSame(face):
                    hits_horiz = True
                    break

        return MoldSide.CORE if hits_horiz else MoldSide.CAVITY


# -- Side actions --------------------------------------------------------------
#
# A side action (slide, lifter, pickout or hand-loaded core) forms faces the
# main pull can't release, and moves in its own direction. A face it forms
# has to be drafted against that direction, not the main pull.


# A face can be released along a direction it faces or runs parallel to (the
# walls of a side hole run parallel to the slide that forms them). Only faces
# leaning back against the direction would lock the side action in place.
_PARALLEL = 1e-3


class SideActionValue(float):
    """A measured value for a face formed by a side action.

    A float, so checks compare it like any other value, that also says which
    side action (1-based) it was measured along.
    """

    side_action: int

    def __new__(cls, value: float, side_action: int):
        obj = super().__new__(cls, value)
        obj.side_action = side_action
        return obj

    def __reduce__(self):
        return (SideActionValue, (float(self), self.side_action))


def side_action_of(value) -> int:
    """The side action (1-based) a value was measured along, or 0 for the main pull."""
    return int(getattr(value, "side_action", 0) or 0)


def ray_is_clear(
    intersector: IntCurvesFace_ShapeIntersector, point, direction: gp_Dir, ignore_within: float
) -> bool:
    """True when nothing of the part is hit along *direction* from *point*.

    Hits closer than *ignore_within* (mm) are the face the ray starts on.
    """
    intersector.Perform(gp_Lin(point, direction), 0.0, float("inf"))
    if not intersector.IsDone():
        return True
    for i in range(1, intersector.NbPnt() + 1):
        if point.Distance(intersector.Pnt(i)) > ignore_within:
            return False
    return True


def releasing_side_action(
    face: TopoDS_Face,
    intersector: IntCurvesFace_ShapeIntersector,
    pull_direction: gp_Dir,
    side_actions: list,
    samples: int,
    ignore_within: float = 0.05,
) -> int:
    """The side action (1-based) that forms *face*, or 0 if the main pull does.

    The main pull keeps every face it can release: a side action only takes
    a face that is trapped along the pull (blocked both ways) at most of its
    points, and only if the side action can pull straight away from it: the
    face faces it or runs parallel to it, with nothing in the way. When several can, the one that
    releases the most of the face wins.
    """
    if not side_actions:
        return 0
    from ...core.utils import yield_face_uv_grid  # here, to avoid an import cycle

    # Quick test first: most faces are open along the pull at their centre,
    # and those stay with the main pull. Only the rest get the full grid.
    u, v = get_face_uv_center(face)
    normal = get_face_uv_normal(face, u, v)
    if normal:
        centre = get_point_from_uv(face, normal, u, v, 1e-3)
        if ray_is_clear(intersector, centre, pull_direction, ignore_within) or ray_is_clear(
            intersector, centre, pull_direction.Reversed(), ignore_within
        ):
            return 0

    points = []
    for u, v in yield_face_uv_grid(face, samples, 0.01):
        normal = get_face_uv_normal(face, u, v)
        if normal:
            points.append((get_point_from_uv(face, normal, u, v, 1e-3), normal))
    if not points:
        return 0

    trapped = sum(
        1 for p, _n in points
        if not ray_is_clear(intersector, p, pull_direction, ignore_within)
        and not ray_is_clear(intersector, p, pull_direction.Reversed(), ignore_within)
    )
    if trapped * 2 < len(points):
        return 0  # the main pull releases it

    best, best_count = 0, 0
    for k, direction in enumerate(side_actions, start=1):
        released = sum(
            1 for p, n in points
            if n.Dot(direction) > -_PARALLEL and ray_is_clear(intersector, p, direction, ignore_within)
        )
        if released * 2 >= len(points) and released > best_count:
            best, best_count = k, released
    return best


def point_released_by_side_action(
    intersector: IntCurvesFace_ShapeIntersector,
    point,
    normal: gp_Dir,
    side_actions: list,
    ignore_within: float = 0.05,
) -> bool:
    """True when some side action can pull straight away from a point on a face."""
    return any(
        normal.Dot(direction) > -_PARALLEL and ray_is_clear(intersector, point, direction, ignore_within)
        for direction in side_actions
    )
