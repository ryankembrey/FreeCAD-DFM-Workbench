# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The test parts: each built fresh with OCP, with its ground truth.

A ``Case`` builds a shape and says which features must be found (kind,
subtype, parameters, and the faces, chosen geometrically by selectors).
Any other feature found is a false positive unless its kind is in
``allow``. Cases are tagged so the bench and the CLI can filter them.

All parts are in millimetres. The standard plate is 100 x 60 x 10 with its
top at z = 10.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..model import FeatureKind as K
from . import select as S
from . import shapes as B


@dataclass
class Expect:
    kind: K
    faces: S.Sel
    subtype: Any = None                 # str, tuple of allowed, or None
    params: dict = field(default_factory=dict)  # name: value or (value, tol)
    min_confidence: float = 0.5


@dataclass
class DirectionalExpect:
    pull: Optional[tuple] = None
    print_direction: Optional[tuple] = None
    side_actions: tuple = ()
    nozzle: float = 0.4
    features: list = field(default_factory=list)       # Expect for DRAFT / UNDERCUT
    #: (selector of a recognised feature's faces, annotation key, expected value)
    annotations: list = field(default_factory=list)
    allow: tuple = (K.DRAFT,)


@dataclass
class Case:
    name: str
    build: Callable[[], Any]
    expect: list
    allow: tuple = ()
    tags: tuple = ()
    notes: str = ""
    variants: tuple = ("base", "moved", "nurbs", "step")
    directional: Optional[DirectionalExpect] = None
    options: dict = field(default_factory=dict)
    max_seconds: float = 0.0             # 0 = no limit


CASES: list[Case] = []


def case(name, build, expect, **kw) -> Case:
    c = Case(name, build, expect, **kw)
    CASES.append(c)
    return c


def plate(x=100, y=60, z=10):
    return B.box(0, 0, 0, x, y, z)


def hole_sel(x, y, r, axis=(0, 0, 1)):
    return S.cyl(r, axis, (x, y, 0))


DOWN = (0, 0, -1)
Z = (0, 0, 1)

# =============================================================================
# Holes
# =============================================================================

case("hole_through_simple",
     lambda: B.cut(plate(), B.drill((30, 30, 10), DOWN, 6, 10)),
     [Expect(K.HOLE, hole_sel(30, 30, 3), "simple",
             {"diameter": (6, 1e-3), "depth": (10, 1e-3), "through": True})],
     tags=("hole",))

case("hole_blind_flat",
     lambda: B.cut(plate(), B.drill((30, 30, 10), DOWN, 6, 5)),
     [Expect(K.HOLE, hole_sel(30, 30, 3) | S.plane(Z, (30, 30, 5)) & S.inside(26, 26, 4, 34, 34, 6), "simple",
             {"diameter": (6, 1e-3), "depth": (5, 1e-3), "through": False, "bottom": "flat"})],
     tags=("hole",))

case("hole_blind_drill_point",
     lambda: B.cut(plate(), B.drill((30, 30, 10), DOWN, 6, 5, point_angle=118)),
     [Expect(K.HOLE, hole_sel(30, 30, 3) | S.cone(Z, (30, 30, 0)), "simple",
             {"diameter": (6, 1e-3), "depth": (5, 1e-3), "bottom": "drill_point", "bottom_angle": (118, 0.5)})],
     tags=("hole",))

case("hole_blind_spherical",
     lambda: B.cut(plate(), B.drill((30, 30, 10), DOWN, 6, 5, spherical=True)),
     [Expect(K.HOLE, hole_sel(30, 30, 3) | S.sphere(3), "simple",
             {"diameter": (6, 1e-3), "depth": (5, 1e-3), "bottom": "spherical"})],
     tags=("hole",))

case("hole_counterbore_through",
     lambda: B.cut(plate(), B.counterbore_tool((30, 30, 10), DOWN, 6, 10, 11, 4)),
     [Expect(K.HOLE, hole_sel(30, 30, 3) | hole_sel(30, 30, 5.5), "counterbored",
             {"diameter": (6, 1e-3), "cb_diameter": (11, 1e-3), "cb_depth": (4, 1e-3), "through": True})],
     tags=("hole",))

case("hole_counterbore_blind",
     lambda: B.cut(plate(), B.counterbore_tool((30, 30, 10), DOWN, 5, 8, 9, 3, point_angle=118)),
     [Expect(K.HOLE, hole_sel(30, 30, 2.5) | hole_sel(30, 30, 4.5), "counterbored",
             {"diameter": (5, 1e-3), "cb_diameter": (9, 1e-3), "cb_depth": (3, 1e-3), "through": False,
              "bottom": "drill_point"})],
     tags=("hole",))

case("hole_countersink_through",
     lambda: B.cut(plate(), B.countersink_tool((30, 30, 10), DOWN, 6, 10, 12, 90)),
     [Expect(K.HOLE, hole_sel(30, 30, 3) | S.cone(Z, (30, 30, 0)), "countersunk",
             {"diameter": (6, 1e-3), "cs_diameter": (12, 1e-2), "cs_angle": (90, 0.1), "through": True})],
     tags=("hole",))

case("hole_countersink_blind",
     lambda: B.cut(plate(), B.countersink_tool((30, 30, 10), DOWN, 4, 7, 8, 82)),
     [Expect(K.HOLE, hole_sel(30, 30, 2) | S.cone(Z, (30, 30, 0)), "countersunk",
             {"diameter": (4, 1e-3), "cs_diameter": (8, 1e-2), "cs_angle": (82, 0.1), "through": False})],
     tags=("hole",))


def _stepped():
    tool = B.fuse(B.drill((30, 30, 10), DOWN, 12, 2), B.drill((30, 30, 10), DOWN, 8, 5),
                  B.drill((30, 30, 10), DOWN, 4, 10), unify=False)
    return B.cut(plate(), tool)


case("hole_stepped_three",
     _stepped,
     [Expect(K.HOLE, hole_sel(30, 30, 2) | hole_sel(30, 30, 4) | hole_sel(30, 30, 6), "stepped",
             {"diameter": (4, 1e-3), "through": True, "step_diameters": (12.0, 8.0, 4.0)})],
     tags=("hole",))


def _counterdrilled():
    big = B.drill((30, 30, 10), DOWN, 10, 3)
    trans = B.cone((30, 30, 7), 5, 2.5, 2.5 / math.tan(math.radians(59)), DOWN)
    return B.cut(plate(), B.fuse(big, trans, B.drill((30, 30, 10), DOWN, 5, 10), unify=False))


case("hole_counterdrilled",
     _counterdrilled,
     [Expect(K.HOLE, hole_sel(30, 30, 2.5) | hole_sel(30, 30, 5), "counterdrilled",
             {"diameter": (5, 1e-3), "cb_diameter": (10, 1e-3), "through": True})],
     tags=("hole",))


def _pattern():
    s = plate()
    tools = [B.drill((15 + 20 * i, 12 + 18 * j, 10), DOWN, 5, 10) for i in range(4) for j in range(3)]
    return B.cut(s, B.fuse(*tools, unify=False))


case("hole_pattern_4x3",
     _pattern,
     [Expect(K.HOLE, hole_sel(15 + 20 * i, 12 + 18 * j, 2.5), "simple", {"diameter": (5, 1e-3), "through": True})
      for i in range(4) for j in range(3)],
     tags=("hole", "pattern"))

_INCL = (math.sin(math.radians(30)), 0.0, -math.cos(math.radians(30)))
case("hole_inclined_30",
     lambda: B.cut(plate(), B.drill((40, 30, 10), _INCL, 6, 20, extra=8)),
     [Expect(K.HOLE, S.cyl(3, _INCL, (40, 30, 10)), "simple", {"diameter": (6, 1e-3), "through": True})],
     tags=("hole",))

case("hole_radial_in_shaft",
     lambda: B.cut(B.cylinder((0, 0, 0), 15, 60), B.drill((-16, 0, 30), (1, 0, 0), 5, 32)),
     [Expect(K.HOLE, S.cyl(2.5, (1, 0, 0), (0, 0, 30)), "simple", {"diameter": (5, 1e-3), "through": True})],
     tags=("hole", "curved"))

case("hole_breaks_out_of_edge",
     lambda: B.cut(plate(), B.drill((2, 30, 10), DOWN, 6, 10)),
     [Expect(K.HOLE, S.cyl(3, Z, (2, 30, 0)), "simple", {"diameter": (6, 1e-3), "partial": True})],
     tags=("hole", "interacting"))


def _cross():
    blk = B.box(0, 0, 0, 40, 40, 40)
    return B.cut(blk, B.drill((20, 20, 40), DOWN, 10, 40), B.drill((0, 20, 20), (1, 0, 0), 6, 40))


case("hole_cross_drilled",
     _cross,
     [Expect(K.HOLE, S.cyl(5, Z, (20, 20, 0)), "simple", {"diameter": (10, 1e-3), "through": True}),
      Expect(K.HOLE, S.cyl(3, (1, 0, 0), (0, 20, 20)), "simple", {"diameter": (6, 1e-3)})],
     tags=("hole", "interacting"))

case("hole_tiny",
     lambda: B.cut(plate(), B.drill((30, 30, 10), DOWN, 0.5, 10)),
     [Expect(K.HOLE, hole_sel(30, 30, 0.25), "simple", {"diameter": (0.5, 1e-4)})],
     tags=("hole", "fdm"))

case("hole_large_blind",
     lambda: B.cut(B.box(0, 0, 0, 100, 100, 30), B.drill((50, 50, 30), DOWN, 60, 20)),
     [Expect(K.HOLE, S.cyl(30, Z, (50, 50, 0)) | S.plane(Z, (50, 50, 10)) & S.inside(20, 20, 9, 80, 80, 11),
             "simple", {"diameter": (60, 1e-3), "depth": (20, 1e-3), "bottom": "flat"})],
     tags=("hole",))


def _chamfered_entry():
    s = B.cut(plate(), B.drill((30, 30, 10), DOWN, 6, 10))
    return B.chamfer(s, 0.5, B.edges_where(s, lambda m, e: abs(m[2] - 10) < 1e-6 and abs(math.hypot(m[0] - 30, m[1] - 30) - 3) < 1e-3))


case("hole_chamfered_entry",
     _chamfered_entry,
     [Expect(K.HOLE, hole_sel(30, 30, 3), "simple", {"diameter": (6, 1e-3)}),
      Expect(K.CHAMFER, S.cone(Z, (30, 30, 0)), "convex", {"width": (0.7071, 1e-3)})],
     tags=("hole", "blend"),
     notes="A small 45° cone at a hole's entry is a lead-in chamfer, not a countersink.")


def _rounded_entry():
    s = B.cut(plate(), B.drill((30, 30, 10), DOWN, 6, 10))
    return B.fillet(s, 1.0, B.edges_where(s, lambda m, e: abs(m[2] - 10) < 1e-6 and abs(math.hypot(m[0] - 30, m[1] - 30) - 3) < 1e-3))


case("hole_rounded_entry",
     _rounded_entry,
     [Expect(K.HOLE, hole_sel(30, 30, 3), "simple", {"diameter": (6, 1e-3), "through": True}),
      Expect(K.FILLET, S.torus(1.0), "convex", {"radius": (1.0, 1e-3)})],
     tags=("hole", "blend"))


def _hole_in_rib():
    s = B.fuse(plate(), B.box(10, 28, 10, 90, 32, 40))
    return B.cut(s, B.drill((50, 27, 30), (0, 1, 0), 3, 6))


case("hole_through_rib",
     _hole_in_rib,
     [Expect(K.RIB, S.plane((0, 1, 0), (0, 28, 0)) | S.plane((0, 1, 0), (0, 32, 0)), None, {"thickness": (4, 1e-3)}),
      Expect(K.HOLE, S.cyl(1.5, (0, 1, 0), (50, 0, 30)), "simple", {"diameter": (3, 1e-3), "through": True})],
     tags=("hole", "rib", "interacting"))


def _threaded():
    blk = B.box(-15, -15, 0, 15, 15, 20)
    s = B.cut(blk, B.drill((0, 0, 20), DOWN, 8.4, 20))
    th = B.helical_thread((0, 0, 2), 4.2, 1.5, 15.0, 0.75)
    return B.cut(s, th, unify=False)


case("hole_threaded_modeled",
     _threaded,
     [Expect(K.HOLE, S.cyl(4.2, Z, (0, 0, 0)), ("threaded", "simple"), {"threaded": True})],
     tags=("hole", "thread"), variants=("base", "moved"), allow=(K.DEPRESSION,))

# =============================================================================
# Bosses
# =============================================================================

case("boss_plain",
     lambda: B.fuse(plate(), B.cylinder((50, 30, 10), 5, 15)),
     [Expect(K.BOSS, S.cyl(5, Z, (50, 30, 0)), "solid", {"outer_diameter": (10, 1e-3), "height": (15, 1e-3)})],
     tags=("boss",))


def _boss_root_fillet():
    s = B.fuse(plate(), B.cylinder((50, 30, 10), 5, 15))
    return B.fillet(s, 1.0, B.edges_where(s, lambda m, e: abs(m[2] - 10) < 1e-6 and abs(math.hypot(m[0] - 50, m[1] - 30) - 5) < 1e-3))


case("boss_root_fillet",
     _boss_root_fillet,
     [Expect(K.BOSS, S.cyl(5, Z, (50, 30, 0)), "solid", {"outer_diameter": (10, 1e-3), "height": (15, 1e-3),
                                                           "root_fillet_radius": (1.0, 1e-3)}),
      Expect(K.FILLET, S.torus(1.0), "concave", {"radius": (1.0, 1e-3)})],
     tags=("boss", "blend"))


def _boss_top_chamfer():
    s = B.fuse(plate(), B.cylinder((50, 30, 10), 5, 15))
    return B.chamfer(s, 1.0, B.edges_where(s, lambda m, e: abs(m[2] - 25) < 1e-6))


case("boss_top_chamfer",
     _boss_top_chamfer,
     [Expect(K.BOSS, S.cyl(5, Z, (50, 30, 0)), "solid", {"outer_diameter": (10, 1e-3)}),
      Expect(K.CHAMFER, S.cone(Z, (50, 30, 0)), "convex", {"width": (math.sqrt(2), 1e-3)})],
     tags=("boss", "blend"))

case("boss_tapered",
     lambda: B.fuse(plate(), B.cone((50, 30, 10), 5, 5 - 15 * math.tan(math.radians(1.5)), 15)),
     [Expect(K.BOSS, S.cone(Z, (50, 30, 0)), "solid", {"outer_diameter": (10, 1e-3), "taper_deg": (1.5, 0.01),
                                                         "height": (15, 1e-3)})],
     tags=("boss", "draft"))

case("boss_screw",
     lambda: B.cut(B.fuse(plate(), B.cylinder((50, 30, 10), 4, 15)), B.drill((50, 30, 25), DOWN, 2.5, 12)),
     [Expect(K.BOSS, S.cyl(4, Z, (50, 30, 0)), "screw", {"outer_diameter": (8, 1e-3), "inner_diameter": (2.5, 1e-3),
                                                          "wall_thickness": (2.75, 1e-3)}),
      Expect(K.HOLE, S.cyl(1.25, Z, (50, 30, 0)), "simple", {"diameter": (2.5, 1e-3), "depth": (12, 1e-3)})],
     tags=("boss", "hole"))

case("boss_screw_slender",
     lambda: B.cut(B.fuse(plate(), B.cylinder((50, 30, 10), 3, 20)), B.drill((50, 30, 30), DOWN, 3, 20)),
     [Expect(K.BOSS, S.cyl(3, Z, (50, 30, 0)), "screw", {"outer_diameter": (6, 1e-3), "height": (20, 1e-3),
                                                          "wall_thickness": (1.5, 1e-3)}),
      Expect(K.HOLE, S.cyl(1.5, Z, (50, 30, 0)), "simple", {"diameter": (3, 1e-3), "depth": (20, 1e-3)})],
     tags=("boss", "hole", "molding"),
     notes="Over three diameters tall, but cored: a screw boss, not a (solid) pin.")

case("boss_screw_through",
     lambda: B.cut(B.fuse(plate(), B.cylinder((50, 30, 10), 4, 15)), B.drill((50, 30, 25), DOWN, 3, 25)),
     [Expect(K.BOSS, S.cyl(4, Z, (50, 30, 0)), "screw", {"inner_diameter": (3, 1e-3)}),
      Expect(K.HOLE, S.cyl(1.5, Z, (50, 30, 0)), "simple", {"through": True, "depth": (25, 1e-3)})],
     tags=("boss", "hole"))

case("boss_stepped",
     lambda: B.fuse(plate(), B.cylinder((50, 30, 10), 6, 5), B.cylinder((50, 30, 15), 4, 8)),
     [Expect(K.BOSS, S.cyl(6, Z, (50, 30, 0)) | S.cyl(4, Z, (50, 30, 0)), "stepped",
             {"outer_diameter": (12, 1e-3), "height": (13, 1e-3)})],
     tags=("boss",))


def _gussets():
    s = B.fuse(plate(), B.cylinder((50, 30, 10), 5, 20))
    g = []
    for ang in (0, 90, 180, 270):
        tri = B.prism([(53, -1, 10), (65, -1, 10), (53, -1, 26)], (0, 2, 0))
        g.append(B.moved(tri, (0, 0, 0), Z, ang, (50, 0, 0)) if ang else tri)
    gs = [B.moved(x, (0, 30, 0)) for x in g]
    return B.fuse(s, *gs)


case("boss_with_gussets",
     _gussets,
     [Expect(K.BOSS, S.cyl(5, Z, (50, 30, 0)), "solid", {"outer_diameter": (10, 1e-3), "gusset_count": 4})]
     + [Expect(K.RIB, S.inside(*box), "gusset", {"thickness": (2, 1e-3)}) for box in (
         (55, 28, 10, 66, 32, 27), (34, 28, 10, 45, 32, 27), (48, 35, 10, 52, 46, 27), (48, 14, 10, 52, 25, 27))],
     tags=("boss", "rib", "gusset"))


_POCKET_PLANES = S.inside(19, 14, 3.9, 81, 46, 10.1) & S.kind("plane")


def _island_boss():
    s = B.cut(plate(), B.box(20, 15, 4, 80, 45, 11))
    return B.fuse(s, B.cylinder((50, 30, 4), 4, 4))


case("pocket_with_boss_island",
     _island_boss,
     [Expect(K.POCKET, _POCKET_PLANES & S.Sel(lambda f, s: f.centroid[2] < 7.9, "below the boss top"),
             "flat", {"depth": (6, 1e-3)}),
      Expect(K.BOSS, S.cyl(4, Z, (50, 30, 0)), "solid", {"height": (4, 1e-3)})],
     tags=("pocket", "boss", "interacting"))


def _column():
    base = B.box(0, 0, 0, 100, 60, 5)
    top = B.box(0, 0, 25, 100, 60, 30)
    wall = B.box(0, 0, 0, 5, 60, 30)
    return B.fuse(base, top, wall, B.cylinder((60, 30, 5), 4, 20))


case("boss_column",
     _column,
     [Expect(K.BOSS, S.cyl(4, Z, (60, 30, 0)), "column", {"outer_diameter": (8, 1e-3), "height": (20, 1e-3)})],
     allow=(K.SLOT, K.STEP, K.POCKET, K.DEPRESSION),
     tags=("boss",))

case("boss_on_curved_face",
     lambda: B.fuse(B.cylinder((0, 0, 0), 20, 80, (1, 0, 0)), B.cylinder((40, 0, 15), 4, 13)),
     [Expect(K.BOSS, S.cyl(4, Z, (40, 0, 0)), "solid", {"outer_diameter": (8, 1e-3)})],
     tags=("boss", "curved"))

case("boss_small_pin",
     lambda: B.fuse(plate(), B.cylinder((50, 30, 10), 0.5, 3)),
     [Expect(K.BOSS, S.cyl(0.5, Z, (50, 30, 0)), "pin", {"outer_diameter": (1, 1e-3), "height": (3, 1e-3)})],
     tags=("boss", "fdm"),
     directional=DirectionalExpect(print_direction=Z, annotations=[(S.cyl(0.5, Z, (50, 30, 0)), "fragile_pin", True)]))

# =============================================================================
# Ribs
# =============================================================================

_RIB_SIDES = S.plane((0, 1, 0), (0, 28, 0)) | S.plane((0, 1, 0), (0, 30, 0))

case("rib_single",
     lambda: B.fuse(plate(), B.box(20, 28, 10, 80, 30, 30)),
     [Expect(K.RIB, _RIB_SIDES, "single", {"thickness": (2, 1e-3), "height": (20, 1e-3), "length": (60, 1e-3),
                                           "tip": "flat"})],
     tags=("rib",))


def _rib_root_fillets():
    s = B.fuse(plate(), B.box(20, 28, 10, 80, 30, 30))
    return B.fillet(s, 0.5, B.edges_where(s, lambda m, e: abs(m[2] - 10) < 1e-6 and 20 < m[0] < 80 and
                                          (abs(m[1] - 28) < 1e-6 or abs(m[1] - 30) < 1e-6)))


case("rib_root_fillets",
     _rib_root_fillets,
     [Expect(K.RIB, _RIB_SIDES, "single", {"thickness": (2, 1e-3), "height": (20, 1e-3),
                                           "root_fillet_radius": (0.5, 1e-3)}),
      Expect(K.FILLET, S.cyl(0.5, (1, 0, 0), (0, 27.5, 10.5)), "concave", {"radius": (0.5, 1e-3)}),
      Expect(K.FILLET, S.cyl(0.5, (1, 0, 0), (0, 30.5, 10.5)), "concave", {"radius": (0.5, 1e-3)})],
     tags=("rib", "blend"))

case("rib_round_tip",
     lambda: B.fuse(plate(), B.box(20, 28, 10, 80, 30, 29), B.cylinder((20, 29, 29), 1, 60, (1, 0, 0))),
     [Expect(K.RIB, _RIB_SIDES, "single", {"thickness": (2, 1e-3), "tip": "round", "height": (20, 1e-3)})],
     tags=("rib",))


def _rib_drafted():
    sec = [(20, 28, 10), (20, 31, 10), (20, 30.5, 30), (20, 28.5, 30)]
    return B.fuse(plate(), B.prism(sec, (60, 0, 0)))


case("rib_drafted",
     _rib_drafted,
     [Expect(K.RIB, S.inside(19, 27, 9, 81, 32, 31) & S.kind("plane") & S.Sel(lambda f, s: abs(f.normal[2]) < 0.5 and abs(f.normal[0]) < 0.5, "side"),
             "single", {"thickness_root": (3, 0.02), "thickness_tip": (2, 0.02),
                        "draft_deg_side1": (math.degrees(math.atan(0.5 / 20)), 0.05)})],
     tags=("rib", "draft"))

case("rib_to_wall",
     lambda: B.fuse(plate(), B.box(0, 0, 10, 5, 60, 40), B.box(5, 28, 10, 60, 30, 30)),
     [Expect(K.RIB, _RIB_SIDES, "single", {"thickness": (2, 1e-3), "ends": ("wall",)})],
     tags=("rib",))

case("rib_cross",
     lambda: B.fuse(plate(), B.box(10, 29, 10, 90, 31, 25), B.box(49, 5, 10, 51, 55, 25)),
     [Expect(K.RIB, S.plane((0, 1, 0), (0, 29, 0)) | S.plane((0, 1, 0), (0, 31, 0)), "network", {"thickness": (2, 1e-3)}),
      Expect(K.RIB, S.plane((1, 0, 0), (49, 0, 0)) | S.plane((1, 0, 0), (51, 0, 0)), "network", {"thickness": (2, 1e-3)})],
     tags=("rib", "interacting"))


def _rib_grid():
    s = plate(120, 120, 5)
    ribs = [B.box(10 + 30 * i, 5, 5, 11.5 + 30 * i, 115, 17) for i in range(4)]
    ribs += [B.box(5, 10 + 30 * j, 5, 115, 11.5 + 30 * j, 17) for j in range(4)]
    return B.fuse(s, *ribs)


case("rib_grid_4x4",
     _rib_grid,
     [Expect(K.RIB, S.plane((1, 0, 0), (10 + 30 * i, 0, 0)) | S.plane((1, 0, 0), (11.5 + 30 * i, 0, 0)), "network",
             {"thickness": (1.5, 1e-3), "height": (12, 1e-3)}) for i in range(4)]
     + [Expect(K.RIB, S.plane((0, 1, 0), (0, 10 + 30 * j, 0)) | S.plane((0, 1, 0), (0, 11.5 + 30 * j, 0)), "network",
               {"thickness": (1.5, 1e-3)}) for j in range(4)],
     tags=("rib", "interacting"), variants=("base", "moved"))

case("rib_on_curved_base",
     lambda: B.fuse(B.cylinder((0, 0, 0), 20, 80, (1, 0, 0)), B.box(10, -1, 15, 70, 1, 32)),
     [Expect(K.RIB, S.plane((0, 1, 0), (0, -1, 0)) | S.plane((0, 1, 0), (0, 1, 0)), "single", {"thickness": (2, 1e-3)})],
     tags=("rib", "curved"))


def _gusset_wall():
    s = B.fuse(plate(), B.box(0, 0, 10, 5, 60, 50))
    tri = B.prism([(5, 29, 10), (35, 29, 10), (5, 29, 40)], (0, 2, 0))
    return B.fuse(s, tri)


case("rib_gusset_on_wall",
     _gusset_wall,
     [Expect(K.RIB, S.plane((0, 1, 0), (0, 29, 0)) | S.plane((0, 1, 0), (0, 31, 0)), "sloped",
             {"thickness": (2, 1e-3), "ends": ("wall",)})],
     tags=("rib", "gusset"))

# =============================================================================
# Slots and steps
# =============================================================================

case("slot_open_both_ends",
     lambda: B.cut(plate(), B.box(-1, 25, 4, 101, 35, 11)),
     [Expect(K.SLOT, S.plane((0, 1, 0), (0, 25, 0)) | S.plane((0, 1, 0), (0, 35, 0)) | S.plane(Z, (0, 0, 4)),
             "open", {"width": (10, 1e-3), "depth": (6, 1e-3), "length": (100, 1e-3)})],
     tags=("slot",))

case("slot_open_one_end",
     lambda: B.cut(plate(), B.box(-1, 25, 4, 60, 35, 11)),
     [Expect(K.SLOT, S.plane((0, 1, 0), (0, 25, 0)) | S.plane((0, 1, 0), (0, 35, 0)) | S.plane(Z, (0, 0, 4)),
             "open_end", {"width": (10, 1e-3), "depth": (6, 1e-3), "length": (60, 1e-3)})],
     tags=("slot",))


def _obround(z0=4):
    tool = B.fuse(B.box(30, 25, z0, 70, 35, 11), B.cylinder((30, 30, z0), 5, 11 - z0), B.cylinder((70, 30, z0), 5, 11 - z0))
    return B.cut(plate(), tool)


case("slot_obround_blind",
     _obround,
     [Expect(K.SLOT, S.plane((0, 1, 0), (0, 25, 0)) | S.plane((0, 1, 0), (0, 35, 0)) | S.cyl(5, Z), "obround",
             {"width": (10, 1e-3), "length": (40, 1e-3), "overall_length": (50, 1e-3), "depth": (6, 1e-3)})],
     tags=("slot",))

case("slot_obround_through",
     lambda: _obround(-1),
     [Expect(K.SLOT, S.plane((0, 1, 0), (0, 25, 0)) | S.plane((0, 1, 0), (0, 35, 0)) | S.cyl(5, Z), "obround",
             {"width": (10, 1e-3), "through": True})],
     tags=("slot",))

case("slot_through_from_edge",
     lambda: B.cut(plate(), B.box(-1, 25, -1, 60, 35, 11)),
     [Expect(K.SLOT, S.plane((0, 1, 0), (0, 25, 0)) | S.plane((0, 1, 0), (0, 35, 0)), "open_end",
             {"width": (10, 1e-3), "through": True})],
     tags=("slot",))

case("slot_rounded_end_from_edge",
     lambda: B.cut(plate(), B.fuse(B.box(-1, 25, 4, 60, 35, 11), B.cylinder((60, 30, 4), 5, 7), unify=False)),
     [Expect(K.SLOT, S.plane((0, 1, 0), (0, 25, 0)) | S.plane((0, 1, 0), (0, 35, 0)) | S.cyl(5, Z), "open_end",
             {"width": (10, 1e-3), "end_radius": (5, 1e-3)})],
     tags=("slot",))

case("step_single",
     lambda: B.cut(plate(), B.box(-1, 40, 5, 101, 61, 11)),
     [Expect(K.STEP, S.plane(Z, (0, 0, 5)) | S.plane((0, 1, 0), (0, 40, 0)), None,
             {"depth": (5, 1e-3), "width": (20, 1e-3), "length": (100, 1e-3)})],
     tags=("step",))

case("step_double",
     lambda: B.cut(plate(), B.box(-1, 40, 5, 101, 61, 11), B.box(-1, 50, 2, 101, 61, 11)),
     [Expect(K.STEP, S.plane(Z, (0, 0, 5)) | S.plane((0, 1, 0), (0, 40, 0)), None, {"depth": (5, 1e-3)}),
      Expect(K.STEP, S.plane(Z, (0, 0, 2)) | S.plane((0, 1, 0), (0, 50, 0)), None, {"depth": (3, 1e-3)})],
     tags=("step",))


def _step_fillet():
    s = B.cut(plate(), B.box(-1, 40, 5, 101, 61, 11))
    return B.fillet(s, 1.0, B.edges_where(s, lambda m, e: abs(m[2] - 5) < 1e-6 and abs(m[1] - 40) < 1e-6))


case("step_with_fillet",
     _step_fillet,
     [Expect(K.STEP, S.plane(Z, (0, 0, 5)) | S.plane((0, 1, 0), (0, 40, 0)), None, {"depth": (5, 1e-3)}),
      Expect(K.FILLET, S.cyl(1.0), "concave", {"radius": (1, 1e-3)})],
     tags=("step", "blend"))

# =============================================================================
# Pockets and cutouts
# =============================================================================

_POCKET = S.inside(19, 14, 3.9, 81, 46, 10.1)

case("pocket_rect",
     lambda: B.cut(plate(), B.box(20, 15, 4, 80, 45, 11)),
     [Expect(K.POCKET, _POCKET, "flat", {"depth": (6, 1e-3), "floor_area": (1800, 1e-2), "shape": "rectangular"})],
     tags=("pocket",))


def _pocket_tool(r_corner=0.0, r_floor=0.0, z0=4, z1=11):
    t = B.box(20, 15, z0, 80, 45, z1)
    if r_corner:
        t = B.fillet(t, r_corner, B.edges_where(t, lambda m, e: abs(m[2] - (z0 + z1) / 2) < 1e-6))
    if r_floor:
        t = B.fillet(t, r_floor, B.edges_where(t, lambda m, e: abs(m[2] - z0) < 1e-6))
    return t


case("pocket_corner_fillets",
     lambda: B.cut(plate(), _pocket_tool(3)),
     [Expect(K.POCKET, _POCKET & S.kind("plane"), "flat", {"depth": (6, 1e-3), "min_corner_radius": (3, 1e-3)})],
     allow=(K.FILLET,), tags=("pocket", "blend"))

case("pocket_floor_fillets",
     lambda: B.cut(plate(), _pocket_tool(3, 1)),
     [Expect(K.POCKET, _POCKET & S.kind("plane"), "flat", {"depth": (6, 1e-3)})],
     allow=(K.FILLET,), tags=("pocket", "blend"))

case("pocket_with_hole_in_floor",
     lambda: B.cut(plate(), B.box(20, 15, 4, 80, 45, 11), B.drill((50, 30, 4), DOWN, 5, 4)),
     [Expect(K.POCKET, _POCKET & S.kind("plane"), "flat", {"depth": (6, 1e-3)}),
      Expect(K.HOLE, S.cyl(2.5, Z, (50, 30, 0)), "simple", {"through": True, "depth": (4, 1e-3)})],
     tags=("pocket", "hole", "interacting"))

case("pocket_with_block_island",
     lambda: B.fuse(B.cut(plate(), B.box(20, 15, 4, 80, 45, 11)), B.box(45, 25, 4, 55, 35, 8)),
     [Expect(K.POCKET, S.inside(19, 14, 3.9, 81, 46, 10.1) & S.kind("plane") & S.Sel(
         lambda f, s: not (44 < f.centroid[0] < 56 and 24 < f.centroid[1] < 36), "not island"), "flat", {"depth": (6, 1e-3)}),
      Expect(K.PROTRUSION, S.inside(44.9, 24.9, 4, 55.1, 35.1, 8.1) & S.Sel(lambda f, s: f.centroid[2] > 4.01, "above floor"),
             "block", {"height": (4, 1e-3)}, min_confidence=0.3)],
     tags=("pocket", "interacting", "residual"))


def _drafted_pocket():
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt
    t = math.tan(math.radians(3)) * 7

    def rect(z, d):
        p = BRepBuilderAPI_MakePolygon()
        for x, y in ((20 + d, 15 + d), (80 - d, 15 + d), (80 - d, 45 - d), (20 + d, 45 - d)):
            p.Add(gp_Pnt(x, y, z))
        p.Close()
        return p.Wire()

    loft = BRepOffsetAPI_ThruSections(True, True)
    loft.AddWire(rect(11, -t / 7))
    loft.AddWire(rect(4, t * 6 / 7))
    loft.Build()
    return B.cut(plate(), loft.Shape())


case("pocket_drafted_walls",
     _drafted_pocket,
     [Expect(K.POCKET, _POCKET & S.kind("plane"), "flat", {"depth": (6, 1e-3), "wall_taper_deg": (3, 0.01)})],
     tags=("pocket", "draft"),
     directional=DirectionalExpect(pull=Z, features=[Expect(K.DRAFT, S.inside(19, 14, 3.9, 81, 46, 10.1) & S.Sel(
         lambda f, s: f.kind == "plane" and abs(f.normal[2]) < 0.5, "walls"), "positive", {"draft_min_deg": (3, 0.01)})],
         allow=(K.DRAFT,)))

case("cutout_rect",
     lambda: B.cut(plate(), B.box(20, 15, -1, 80, 45, 11)),
     [Expect(K.CUTOUT, S.inside(19, 14, -0.1, 81, 46, 10.1) & S.Sel(lambda f, s: 0.1 < f.centroid[2] < 9.9, "walls"),
             "through", {"thickness": (10, 1e-3), "shape": "rectangular"})],
     tags=("cutout",))

case("cutout_rounded_corners",
     lambda: B.cut(plate(), _pocket_tool(4, 0, -1, 11)),
     [Expect(K.CUTOUT, S.inside(19, 14, -0.1, 81, 46, 10.1) & S.kind("plane") & S.Sel(lambda f, s: 0.1 < f.centroid[2] < 9.9, "walls"),
             "through", {"thickness": (10, 1e-3), "min_corner_radius": (4, 1e-3)})],
     allow=(K.FILLET,), tags=("cutout", "blend"))

case("pocket_keyway_on_shaft",
     lambda: B.cut(B.cylinder((0, 0, 0), 10, 60, (1, 0, 0)), B.box(10, -2, 7, 40, 2, 11)),
     [Expect(K.POCKET, S.inside(9.9, -2.1, 6.9, 40.1, 2.1, 10.1) & S.kind("plane"), None, {"depth": (3, 0.05)})],
     tags=("pocket", "curved"))

# =============================================================================
# Fillets and chamfers
# =============================================================================


def _all_rounds():
    s = B.box(0, 0, 0, 40, 30, 20)
    return B.fillet(s, 2.0, B.edges_of(s))


case("box_all_edges_rounded",
     _all_rounds,
     [Expect(K.FILLET, S.cyl(2.0) | S.sphere(2.0), "convex", {"radius": (2, 1e-3), "face_count": 20})],
     tags=("blend",))

def _one_round():
    s = B.box(0, 0, 0, 40, 30, 20)
    return B.fillet(s, 3.0, B.edges_near(s, [(20, 0, 20)]))


case("box_one_edge_rounded",
     _one_round,
     [Expect(K.FILLET, S.cyl(3.0), "convex", {"radius": (3, 1e-3)})],
     tags=("blend",))


def _l_bracket(r=0.0, ch=0.0):
    s = B.fuse(B.box(0, 0, 0, 60, 40, 5), B.box(0, 0, 0, 5, 40, 50))
    edges = B.edges_where(s, lambda m, e: abs(m[0] - 5) < 1e-6 and abs(m[2] - 5) < 1e-6)
    if r:
        s = B.fillet(s, r, edges)
    if ch:
        s = B.chamfer(s, ch, edges)
    return s


case("l_bracket_inner_fillet",
     lambda: _l_bracket(r=2.0),
     [Expect(K.FILLET, S.cyl(2.0), "concave", {"radius": (2, 1e-3)}),
      Expect(K.STEP, S.plane(Z, (0, 0, 5)) | S.plane((1, 0, 0), (5, 0, 0)), None, {})],
     tags=("blend", "step"))

case("l_bracket_inner_chamfer",
     lambda: _l_bracket(ch=2.0),
     [Expect(K.CHAMFER, S.inside(4.9, -1, 4.9, 7.1, 41, 7.1) & S.kind("plane"), "concave", {"width": (2 * math.sqrt(2), 1e-3)}),
      Expect(K.STEP, S.plane(Z, (0, 0, 5)) | S.plane((1, 0, 0), (5, 0, 0)), None, {})],
     tags=("blend", "step"))

def _top_chamfers():
    s = B.box(0, 0, 0, 40, 30, 20)
    return B.chamfer(s, 1.5, B.edges_where(s, lambda m, e: abs(m[2] - 20) < 1e-6))


case("box_top_edges_chamfered",
     _top_chamfers,
     [Expect(K.CHAMFER, S.inside(-0.1, -0.1, 18.4, 40.1, 30.1, 20.1) & S.Sel(lambda f, s: f.kind == "plane" and 0.1 < abs(f.normal[2]) < 0.9, "bevel"),
             "convex", {"width": (1.5 * math.sqrt(2), 1e-3), "face_count": 4})],
     tags=("blend",))


def _all_chamfers():
    s = B.box(0, 0, 0, 40, 30, 20)
    return B.chamfer(s, 1.0, B.edges_of(s))


case("box_all_edges_chamfered",
     _all_chamfers,
     [Expect(K.CHAMFER, S.Sel(lambda f, s: f.kind == "plane" and sum(1 for c in f.normal if abs(c) > 1e-6) >= 2, "bevels"),
             "convex", {"width": (math.sqrt(2), 1e-3)})],
     tags=("blend",))

# =============================================================================
# Things that are not features
# =============================================================================

for _name, _build in (
        ("plain_box", lambda: B.box(0, 0, 0, 40, 30, 20)),
        ("plain_cylinder", lambda: B.cylinder((0, 0, 0), 10, 30)),
        ("plain_sphere", lambda: B.sphere((0, 0, 0), 10)),
        ("plain_cone_frustum", lambda: B.cone((0, 0, 0), 10, 5, 20)),
        ("plain_wedge", lambda: B.prism([(0, 0, 0), (40, 0, 0), (0, 0, 20)], (0, 30, 0))),
        ("plain_hex_prism", lambda: B.prism([(10 * math.cos(math.radians(a)), 10 * math.sin(math.radians(a)), 0)
                                             for a in range(0, 360, 60)], (0, 0, 15))),
        ("plain_torus_ring", lambda: B.revolve([(20, 0, -3), (26, 0, -3), (26, 0, 3), (20, 0, 3)])),
):
    case(_name, _build, [], tags=("negative",),
         notes="A plain solid: nothing to recognise (the ring's bore is a hole).")
# The rectangular-section ring's bore is an internal cylinder: a through hole.
CASES[-1].expect = [Expect(K.HOLE, S.cyl(20, Z, (0, 0, 0)), "simple", {"diameter": (40, 1e-3), "through": True})]

case("washer",
     lambda: B.cut(B.cylinder((0, 0, 0), 20, 3), B.drill((0, 0, 3), DOWN, 16, 3)),
     [Expect(K.HOLE, S.cyl(8, Z, (0, 0, 0)), "simple", {"diameter": (16, 1e-3), "through": True})],
     tags=("hole", "negative"))

# =============================================================================
# Whole parts
# =============================================================================


def _housing():
    outer = B.box(0, 0, 0, 80, 50, 30)
    s = B.shell(outer, 2.0, lambda c: abs(c[2] - 30) < 1e-6)
    bosses = [B.cylinder((x, y, 2), 4, 20) for x, y in ((12, 12), (68, 12), (12, 38), (68, 38))]
    s = B.fuse(s, *bosses, B.box(39.25, 2, 2, 40.75, 48, 14))
    holes = B.fuse(*[B.drill((x, y, 22), DOWN, 2.5, 15) for x, y in ((12, 12), (68, 12), (12, 38), (68, 38))], unify=False)
    s = B.cut(s, holes, B.drill((-1, 25, 15), (1, 0, 0), 6, 4))
    return s


case("housing_shelled",
     _housing,
     [Expect(K.BOSS, S.cyl(4, Z, (x, y, 0)), "screw", {"outer_diameter": (8, 1e-3), "height": (20, 1e-3),
                                                         "inner_diameter": (2.5, 1e-3)})
      for x, y in ((12, 12), (68, 12), (12, 38), (68, 38))]
     + [Expect(K.HOLE, S.cyl(1.25, Z, (x, y, 0)), "simple", {"depth": (15, 1e-3)})
        for x, y in ((12, 12), (68, 12), (12, 38), (68, 38))]
     + [Expect(K.RIB, S.plane((1, 0, 0), (39.25, 0, 0)) | S.plane((1, 0, 0), (40.75, 0, 0)), None,
               {"thickness": (1.5, 1e-3), "height": (12, 1e-3)}),
        Expect(K.HOLE, S.cyl(3, (1, 0, 0), (0, 25, 15)), "simple", {"diameter": (6, 1e-3), "through": True}),
        Expect(K.POCKET, S.inside(2, 2, 2, 78, 48, 30) & S.kind("plane") & S.Sel(
            lambda f, s: not (39 < f.centroid[0] < 41) and f.area > 100 and f.centroid[2] < 21,
            "inner walls and floor"), None, {"depth": (28, 1e-3)})],
     tags=("part", "molding"),
     directional=DirectionalExpect(pull=Z, features=[
         Expect(K.UNDERCUT, S.cyl(3, (1, 0, 0), (0, 25, 15)), "feature", {})], allow=(K.DRAFT,)))


def _bracket():
    s = B.fuse(B.box(0, 0, 0, 80, 40, 6), B.box(0, 0, 0, 6, 40, 60))
    s = B.fillet(s, 3.0, B.edges_where(s, lambda m, e: abs(m[0] - 6) < 1e-6 and abs(m[2] - 6) < 1e-6))
    g1 = B.prism([(5, 4, 5), (50, 4, 5), (5, 4, 50)], (0, 3, 0))
    g2 = B.prism([(5, 33, 5), (50, 33, 5), (5, 33, 50)], (0, 3, 0))
    s = B.fuse(s, g1, g2)
    s = B.cut(s, B.drill((60, 12, 6), DOWN, 8, 6), B.drill((60, 28, 6), DOWN, 8, 6),
              B.drill((-1, 20, 40), (1, 0, 0), 10, 8))
    return s


case("bracket_with_gussets",
     _bracket,
     [Expect(K.HOLE, S.cyl(4, Z, (60, 12, 0)), "simple", {"through": True}),
      Expect(K.HOLE, S.cyl(4, Z, (60, 28, 0)), "simple", {"through": True}),
      Expect(K.HOLE, S.cyl(5, (1, 0, 0), (0, 20, 40)), "simple", {"through": True}),
      Expect(K.RIB, S.plane((0, 1, 0), (0, 4, 0)) | S.plane((0, 1, 0), (0, 7, 0)), "sloped", {"thickness": (3, 1e-3)}),
      Expect(K.RIB, S.plane((0, 1, 0), (0, 33, 0)) | S.plane((0, 1, 0), (0, 36, 0)), "sloped", {"thickness": (3, 1e-3)}),
      Expect(K.FILLET, S.cyl(3.0), "concave", {"radius": (3, 1e-3)})],
     tags=("part",))


def _fdm_part():
    s = B.box(0, 0, 0, 60, 40, 30)
    s = B.cut(s, B.drill((-1, 20, 15), (1, 0, 0), 8, 62), B.drill((45, 30, 30), DOWN, 0.6, 30))
    return B.fuse(s, B.box(10, 5, 30, 50, 5.6, 40))


case("fdm_horizontal_hole_and_thin_rib",
     _fdm_part,
     [Expect(K.HOLE, S.cyl(4, (1, 0, 0), (0, 20, 15)), "simple", {"diameter": (8, 1e-3)}),
      Expect(K.HOLE, S.cyl(0.3, Z, (45, 30, 0)), "simple", {"diameter": (0.6, 1e-4)}),
      Expect(K.RIB, S.plane((0, 1, 0), (0, 5, 0)) | S.plane((0, 1, 0), (0, 5.6, 0)), None, {"thickness": (0.6, 1e-3)})],
     tags=("part", "fdm"),
     directional=DirectionalExpect(print_direction=Z, annotations=[
         (S.cyl(4, (1, 0, 0), (0, 20, 15)), "horizontal", True),
         (S.cyl(4, (1, 0, 0), (0, 20, 15)), "teardrop_recommended", True),
         (S.cyl(0.3, Z, (45, 30, 0)), "below_min_printable", True),
         (S.cyl(0.3, Z, (45, 30, 0)), "horizontal", False),
         (S.plane((0, 1, 0), (0, 5, 0)), "thin_for_nozzle", True)]))


def _snap_lid():
    lid = B.box(0, 0, 0, 60, 40, 3)
    arm = B.box(10, 0, 3, 20, 2, 20)
    lip = B.box(10, 1.5, 15, 20, 3.5, 18)      # hooks inwards, over the lid
    return B.fuse(lid, arm, lip)


case("snap_hook_lid",
     _snap_lid,
     [],
     allow=(K.RIB, K.STEP, K.PROTRUSION, K.SLOT, K.POCKET),
     tags=("part", "molding", "undercut"),
     directional=DirectionalExpect(pull=Z, features=[
         Expect(K.UNDERCUT, S.plane(Z, (0, 0, 15)) & S.inside(9.9, 1.9, 14.9, 20.1, 3.6, 15.1), None, {})],
         allow=(K.DRAFT, K.UNDERCUT)))


def _perf_plate(n=15):
    s = B.box(0, 0, 0, 20 * n + 10, 20 * n + 10, 8)
    tools = [B.drill((15 + 20 * i, 15 + 20 * j, 8), DOWN, 6, 8) for i in range(n) for j in range(n)]
    return B.cut(s, B.fuse(*tools, unify=False))


case("perf_plate_225_holes",
     _perf_plate,
     [Expect(K.HOLE, S.cyl(3, Z, (15 + 20 * i, 15 + 20 * j, 0)), "simple", {}) for i in range(15) for j in range(15)],
     tags=("hole", "performance"), variants=("base",), max_seconds=30.0)


# =============================================================================
# Round two: harder interactions and realistic details
# =============================================================================

case("hole_through_boss_wall",
     lambda: B.cut(B.fuse(plate(), B.cylinder((50, 30, 10), 6, 20)), B.drill((50, 30, 30), DOWN, 6, 15),
                   B.drill((43, 30, 22), (1, 0, 0), 2, 14)),
     [Expect(K.BOSS, S.cyl(6, Z, (50, 30, 0)), "screw", {"outer_diameter": (12, 1e-3), "inner_diameter": (6, 1e-3)}),
      Expect(K.HOLE, S.cyl(3, Z, (50, 30, 0)), "simple", {"diameter": (6, 1e-3)}),
      Expect(K.HOLE, S.cyl(1, (1, 0, 0), (0, 30, 22)), "simple", {"diameter": (2, 1e-3)})],
     tags=("hole", "boss", "interacting"))

case("hole_into_pocket_wall",
     lambda: B.cut(plate(), B.box(20, 15, 4, 80, 45, 11), B.drill((-1, 30, 7), (1, 0, 0), 3, 22)),
     [Expect(K.POCKET, _POCKET_PLANES, "flat", {"depth": (6, 1e-3)}),
      Expect(K.HOLE, S.cyl(1.5, (1, 0, 0), (0, 30, 7)), "simple", {"diameter": (3, 1e-3), "through": True})],
     tags=("hole", "pocket", "interacting"))

case("hole_from_below_blind",
     lambda: B.cut(plate(), B.drill((30, 30, 0), Z, 5, 6, point_angle=118)),
     [Expect(K.HOLE, S.cyl(2.5, Z, (30, 30, 0)) | S.cone(Z, (30, 30, 0)), "simple",
             {"diameter": (5, 1e-3), "depth": (6, 1e-3), "bottom": "drill_point"})],
     tags=("hole",))

case("holes_y_junction",
     lambda: B.cut(B.box(0, 0, 0, 40, 40, 40), B.drill((20, 20, 40), DOWN, 8, 25),
                   B.drill((20, 0, 30), (0, math.cos(math.radians(45)), -math.sin(math.radians(45))), 6, 30, extra=8)),
     [Expect(K.HOLE, S.cyl(4, Z, (20, 20, 0)), "simple", {"diameter": (8, 1e-3)}),
      Expect(K.HOLE, S.cyl(3, (0, 1, -1)), "simple", {"diameter": (6, 1e-3)})],
     tags=("hole", "interacting"))


def _bottom_fillet_bore():
    s = B.box(0, 0, 0, 80, 80, 30)
    s = B.cut(s, B.drill((40, 40, 30), DOWN, 40, 20))
    return B.fillet(s, 3.0, B.edges_where(s, lambda m, e: abs(m[2] - 10) < 1e-6))


case("hole_large_with_bottom_fillet",
     _bottom_fillet_bore,
     [Expect(K.HOLE, S.cyl(20, Z, (40, 40, 0)), "simple", {"diameter": (40, 1e-3), "bottom": "flat"}),
      Expect(K.FILLET, S.torus(3.0), "concave", {"radius": (3, 1e-3)})],
     tags=("hole", "blend"))


def _real_screw_boss():
    s = B.fuse(plate(), B.cone((50, 30, 10), 4, 4 - 14 * math.tan(math.radians(1)), 14))
    s = B.fillet(s, 0.5, B.edges_where(s, lambda m, e: abs(m[2] - 10) < 1e-6 and abs(math.hypot(m[0] - 50, m[1] - 30) - 4) < 1e-3))
    s = B.cut(s, B.drill((50, 30, 24), DOWN, 2.2, 10))
    return B.chamfer(s, 0.3, B.edges_where(s, lambda m, e: abs(m[2] - 24) < 1e-6 and abs(math.hypot(m[0] - 50, m[1] - 30) - 1.1) < 1e-3))


case("boss_screw_realistic",
     _real_screw_boss,
     [Expect(K.BOSS, S.cone(Z, (50, 30, 0)) & S.Sel(lambda f, s: f.radius > 3, "outer"), "screw",
             {"outer_diameter": (8, 1e-3), "taper_deg": (1, 0.01), "root_fillet_radius": (0.5, 1e-3),
              "inner_diameter": (2.2, 1e-3)}),
      Expect(K.HOLE, S.cyl(1.1, Z, (50, 30, 0)) | S.plane(Z, (0, 0, 14)) & S.inside(48, 28, 13, 52, 32, 15) |
             S.cone(Z, (50, 30, 0)) & S.inside(48, 28, 23, 52, 32, 25), ("countersunk", "simple"),
             {"diameter": (2.2, 1e-3)}),
      Expect(K.FILLET, S.torus(0.5), "concave", {"radius": (0.5, 1e-3)})],
     tags=("boss", "hole", "blend", "molding"))

case("protrusion_rect_block",
     lambda: B.fuse(plate(), B.box(40, 20, 10, 60, 40, 20)),
     [Expect(K.PROTRUSION, S.inside(39.9, 19.9, 10.01, 60.1, 40.1, 20.1), "block", {"height": (10, 1e-3)},
             min_confidence=0.3)],
     tags=("residual",))


def _real_rib():
    sec = [(20, 28, 10), (20, 31, 10), (20, 30.5, 30), (20, 28.5, 30)]
    s = B.fuse(plate(), B.prism(sec, (60, 0, 0)))
    return B.fillet(s, 0.6, B.edges_where(s, lambda m, e: abs(m[2] - 10) < 1e-6 and 20 < m[0] < 80 and 27 < m[1] < 32))


case("rib_realistic_drafted_filleted",
     _real_rib,
     [Expect(K.RIB, S.inside(19, 27, 9, 81, 32, 31) & S.kind("plane") & S.Sel(
         lambda f, s: abs(f.normal[2]) < 0.5 and abs(f.normal[0]) < 0.5, "side"), "single",
             {"thickness_tip": (2, 0.02), "root_fillet_radius": (0.6, 1e-3)})],
     allow=(K.FILLET,), tags=("rib", "draft", "blend", "molding"))

case("rib_t_junction",
     lambda: B.fuse(plate(), B.box(10, 29, 10, 90, 31, 25), B.box(49, 31, 10, 51, 55, 25)),
     [Expect(K.RIB, S.plane((0, 1, 0), (0, 29, 0)) | S.plane((0, 1, 0), (0, 31, 0)), None, {"thickness": (2, 1e-3)}),
      Expect(K.RIB, S.plane((1, 0, 0), (49, 0, 0)) | S.plane((1, 0, 0), (51, 0, 0)), "network",
             {"thickness": (2, 1e-3), "ends": ("rib",)})],
     tags=("rib", "interacting"))

case("pocket_nested",
     lambda: B.cut(plate(100, 60, 20), B.box(10, 10, 10, 90, 50, 21), B.box(30, 20, 4, 70, 40, 11)),
     [Expect(K.POCKET, S.inside(9.9, 9.9, 9.9, 90.1, 50.1, 20.1) & S.kind("plane") & S.Sel(
         lambda f, s: f.centroid[2] > 10.01 or not (29 < f.centroid[0] < 71 and 19 < f.centroid[1] < 41), "outer"),
             "flat", {"depth": (10, 1e-3)}),
      Expect(K.POCKET, S.inside(29.9, 19.9, 3.9, 70.1, 40.1, 10.1) & S.kind("plane") & S.Sel(
          lambda f, s: f.centroid[2] < 9.99, "inner"), "flat", {"depth": (6, 1e-3)})],
     tags=("pocket", "interacting"))

case("slot_wide_from_edge",
     lambda: B.cut(plate(), B.box(-1, 15, 4, 40, 45, 11)),
     [Expect(K.SLOT, S.plane((0, 1, 0), (0, 15, 0)) | S.plane((0, 1, 0), (0, 45, 0)), "open_end",
             {"width": (30, 1e-3), "depth": (6, 1e-3)})],
     tags=("slot",))

case("step_with_hole_in_floor",
     lambda: B.cut(plate(), B.box(-1, 40, 5, 101, 61, 11), B.drill((50, 50, 5), DOWN, 4, 5)),
     [Expect(K.STEP, S.plane(Z, (0, 0, 5)) | S.plane((0, 1, 0), (0, 40, 0)), None, {"depth": (5, 1e-3)}),
      Expect(K.HOLE, S.cyl(2, Z, (50, 50, 0)), "simple", {"through": True})],
     tags=("step", "hole", "interacting"))

case("shaft_with_groove",
     lambda: B.revolve([(0, 0, 0), (10, 0, 0), (10, 0, 20), (8, 0, 20), (8, 0, 23), (10, 0, 23), (10, 0, 50), (0, 0, 50)]),
     [],
     allow=(K.SLOT, K.DEPRESSION),
     tags=("negative", "curved"),
     notes="A groove round a shaft: its floor must not read as a column boss.")


def _flange():
    s = B.revolve([(0, 0, 0), (40, 0, 0), (40, 0, 8), (12, 0, 8), (12, 0, 30), (0, 0, 30)])
    s = B.cut(s, B.drill((0, 0, 30), DOWN, 12, 30))
    holes = [B.drill((30 * math.cos(math.radians(a)), 30 * math.sin(math.radians(a)), 8), DOWN, 5, 8)
             for a in range(0, 360, 60)]
    return B.cut(s, B.fuse(*holes, unify=False))


case("flange_hub_bolt_circle",
     _flange,
     [Expect(K.BOSS, S.cyl(12, Z, (0, 0, 0)), "screw", {"outer_diameter": (24, 1e-3), "height": (22, 1e-3),
                                                         "inner_diameter": (12, 1e-3)}),
      Expect(K.HOLE, S.cyl(6, Z, (0, 0, 0)), "simple", {"diameter": (12, 1e-3), "through": True})]
     + [Expect(K.HOLE, S.cyl(2.5, Z, (30 * math.cos(math.radians(a)), 30 * math.sin(math.radians(a)), 0)), "simple",
               {"diameter": (5, 1e-3), "through": True}) for a in range(0, 360, 60)],
     tags=("part", "hole", "boss", "curved"))


def _housing_boss_ribs():
    outer = B.box(0, 0, 0, 80, 50, 30)
    s = B.shell(outer, 2.0, lambda c: abs(c[2] - 30) < 1e-6)
    s = B.fuse(s, B.cylinder((40, 25, 2), 4, 18),
               B.box(2, 24.25, 2, 36.5, 25.75, 12), B.box(43.5, 24.25, 2, 78, 25.75, 12))
    return B.cut(s, B.drill((40, 25, 20), DOWN, 2.5, 14))


case("housing_boss_tied_to_walls",
     _housing_boss_ribs,
     [Expect(K.BOSS, S.cyl(4, Z, (40, 25, 0)), "screw", {"outer_diameter": (8, 1e-3), "gusset_count": 2}),
      Expect(K.RIB, S.plane((0, 1, 0), (0, 24.25, 0)) & S.inside(0, 0, 0, 39, 50, 30) |
             S.plane((0, 1, 0), (0, 25.75, 0)) & S.inside(0, 0, 0, 39, 50, 30), "gusset", {"thickness": (1.5, 1e-3)}),
      Expect(K.RIB, S.plane((0, 1, 0), (0, 24.25, 0)) & S.inside(41, 0, 0, 80, 50, 30) |
             S.plane((0, 1, 0), (0, 25.75, 0)) & S.inside(41, 0, 0, 80, 50, 30), "gusset", {"thickness": (1.5, 1e-3)}),
      Expect(K.HOLE, S.cyl(1.25, Z, (40, 25, 0)), "simple", {})],
     allow=(K.POCKET,), tags=("part", "molding", "rib", "boss"))


def _cup():
    s = B.revolve([(0, 0, 0), (30, 0, 0), (30, 0, 40), (28, 0, 40), (28, 0, 2), (0, 0, 2)])
    return s


case("cup_revolved",
     _cup,
     [Expect(K.HOLE, S.cyl(28, Z, (0, 0, 0)), "simple", {"diameter": (56, 1e-3), "depth": (38, 1e-3), "bottom": "flat"})],
     tags=("hole", "curved"),
     notes="A cup's bore is a large blind hole.")


# =============================================================================
# Round three: blended molding details
# =============================================================================


def _boss_top_round():
    s = B.fuse(plate(), B.cylinder((50, 30, 10), 5, 15))
    return B.fillet(s, 1.0, B.edges_where(s, lambda m, e: abs(m[2] - 25) < 1e-6))


case("boss_top_round",
     _boss_top_round,
     [Expect(K.BOSS, S.cyl(5, Z, (50, 30, 0)), "solid", {"outer_diameter": (10, 1e-3), "height": (15, 1e-3)}),
      Expect(K.FILLET, S.torus(1.0), "convex", {"radius": (1, 1e-3)})],
     tags=("boss", "blend"))


def _rib_tip_rounds():
    s = B.fuse(plate(), B.box(20, 28, 10, 80, 31, 30))
    return B.fillet(s, 0.75, B.edges_where(s, lambda m, e: abs(m[2] - 30) < 1e-6 and 20 < m[0] < 80))


case("rib_tip_rounds",
     _rib_tip_rounds,
     [Expect(K.RIB, S.plane((0, 1, 0), (0, 28, 0)) | S.plane((0, 1, 0), (0, 31, 0)), "single",
             {"thickness": (3, 1e-3), "height": (20, 1e-3)})],
     allow=(K.FILLET,), tags=("rib", "blend"))


def _slot_floor_fillets():
    s = B.cut(plate(), B.box(-1, 25, 4, 101, 35, 11))
    return B.fillet(s, 1.0, B.edges_where(s, lambda m, e: abs(m[2] - 4) < 1e-6 and 0 < m[0] < 100))


case("slot_floor_fillets",
     _slot_floor_fillets,
     [Expect(K.SLOT, S.plane((0, 1, 0), (0, 25, 0)) | S.plane((0, 1, 0), (0, 35, 0)) | S.plane(Z, (0, 0, 4)),
             "open", {"width": (10, 1e-3), "depth": (6, 1e-3)}),
      Expect(K.FILLET, S.cyl(1.0, (1, 0, 0), (0, 26, 5)), "concave", {}),
      Expect(K.FILLET, S.cyl(1.0, (1, 0, 0), (0, 34, 5)), "concave", {})],
     tags=("slot", "blend"))


def _counterbore_chamfered():
    s = B.cut(plate(), B.counterbore_tool((30, 30, 10), DOWN, 6, 10, 11, 4))
    return B.chamfer(s, 0.5, B.edges_where(s, lambda m, e: abs(m[2] - 10) < 1e-6 and
                                           abs(math.hypot(m[0] - 30, m[1] - 30) - 5.5) < 1e-3))


case("hole_counterbore_chamfered",
     _counterbore_chamfered,
     [Expect(K.HOLE, hole_sel(30, 30, 3) | hole_sel(30, 30, 5.5), "counterbored",
             {"diameter": (6, 1e-3), "through": True}),
      Expect(K.CHAMFER, S.cone(Z, (30, 30, 0)), "convex", {})],
     tags=("hole", "blend"))


def _lid_with_lip():
    lid = B.box(0, 0, 0, 80, 50, 3)
    ring = B.cut(B.box(2, 2, 3, 78, 48, 8), B.box(3.5, 3.5, 2, 76.5, 46.5, 9))
    return B.fuse(lid, ring)


case("lid_with_perimeter_lip",
     _lid_with_lip,
     [Expect(K.RIB, S.plane((0, 1, 0), (0, 2, 0)) | S.plane((0, 1, 0), (0, 3.5, 0)), None, {"thickness": (1.5, 1e-3), "height": (5, 1e-3)}),
      Expect(K.RIB, S.plane((0, 1, 0), (0, 48, 0)) | S.plane((0, 1, 0), (0, 46.5, 0)), None, {"thickness": (1.5, 1e-3)}),
      Expect(K.RIB, S.plane((1, 0, 0), (2, 0, 0)) | S.plane((1, 0, 0), (3.5, 0, 0)), None, {"thickness": (1.5, 1e-3)}),
      Expect(K.RIB, S.plane((1, 0, 0), (78, 0, 0)) | S.plane((1, 0, 0), (76.5, 0, 0)), None, {"thickness": (1.5, 1e-3)})],
     tags=("rib", "molding"),
     notes="A lid's perimeter lip is four thin walls: ribs, joined at the corners.")


def _molded_tray():
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt
    d = 25 * math.tan(math.radians(2))

    def rect(z, k):
        p = BRepBuilderAPI_MakePolygon()
        for x, y in ((k, k), (80 - k, k), (80 - k, 50 - k), (k, 50 - k)):
            p.Add(gp_Pnt(x, y, z))
        p.Close()
        return p.Wire()

    loft = BRepOffsetAPI_ThruSections(True, True)
    loft.AddWire(rect(0, d))
    loft.AddWire(rect(25, 0))
    loft.Build()
    tray = B.shell(loft.Shape(), 2.0, lambda c: abs(c[2] - 25) < 1e-6)
    tray = B.fuse(tray, B.cylinder((40, 25, 2), 4, 15))
    return B.cut(tray, B.drill((40, 25, 17), DOWN, 2.5, 12))


case("molded_tray_drafted",
     _molded_tray,
     [Expect(K.BOSS, S.cyl(4, Z, (40, 25, 0)), "screw", {"outer_diameter": (8, 1e-3), "height": (15, 1e-3)}),
      Expect(K.HOLE, S.cyl(1.25, Z, (40, 25, 0)), "simple", {"depth": (12, 1e-3)}),
      Expect(K.POCKET, S.Sel(lambda f, s: f.kind == "plane" and f.area > 100 and (
          (abs(f.normal[2]) < 0.2 and 2 < f.centroid[0] < 78 and 2 < f.centroid[1] < 48)
          or abs(f.centroid[2] - 2) < 1e-3), "inner walls and floor"), "flat",
             {"depth": (23, 1e-3), "wall_taper_deg": (2, 0.01)})],
     allow=(K.DRAFT,), tags=("part", "molding", "draft"),
     directional=DirectionalExpect(pull=(0, 0, -1), features=[], allow=(K.DRAFT, K.UNDERCUT),
                                   annotations=[(S.cyl(4, Z, (40, 25, 0)), "needs_draft", True)]))


# =============================================================================
# Depressions classified by their profile (the MFCAD shapes, and the
# printed-part details found failing on real models)
# =============================================================================

case("step_blind_corner",
     lambda: B.cut(plate(), B.box(80, 40, 5, 101, 61, 11)),
     [Expect(K.STEP, S.plane(Z, (0, 0, 5)) | S.plane((0, 1, 0), (90, 40, 0)) | S.plane((1, 0, 0), (80, 50, 0)),
             "blind", {"open_ends": 1})],
     tags=("step", "mfcad"))

case("slot_v_groove",
     lambda: B.cut(plate(), B.prism([(45, -1, 10.5), (55, -1, 10.5), (50, -1, 5)], (0, 62, 0))),
     [Expect(K.SLOT, S.plane() & S.inside(44, 0, 4.9, 56, 60, 10.1) & S.Sel(lambda f, s: 0.1 < abs(f.normal[2]) < 0.99, "inclined"),
             "open", {"profile": "v", "depth": (5, 1e-3)})],
     tags=("slot", "mfcad"))

case("step_slanted",
     lambda: B.cut(plate(), B.prism([(-1, 45, 11), (-1, 61, 11), (-1, 61, 4), (-1, 52, 4)], (102, 0, 0))),
     [Expect(K.STEP, S.plane(Z, (0, 0, 4)) | (S.plane() & S.Sel(lambda f, s: 0.1 < abs(f.normal[1]) < 0.99, "slanted")),
             None)],
     tags=("step", "mfcad"))


def _crossed_passages():
    b = B.box(0, 0, 0, 40, 40, 40)
    return B.cut(b, B.box(15, 15, -1, 25, 25, 41), B.box(-1, 17, 10, 41, 23, 30))


case("cutout_crossed_passages",
     _crossed_passages,
     [Expect(K.CUTOUT, S.inside(14.9, 14.9, -0.1, 25.1, 25.1, 40.1) & S.Sel(
          lambda f, s: f.kind == "plane" and abs(f.normal[2]) < 1e-6, "vertical walls"), None, {"thickness": (40, 1e-3)}),
      Expect(K.CUTOUT, S.inside(-0.1, 16.9, 9.9, 40.1, 23.1, 30.1) & S.Sel(
          lambda f, s: f.kind == "plane" and abs(f.normal[0]) < 1e-6, "walls along x"), None, {"thickness": (40, 1e-3)})],
     tags=("cutout", "mfcad", "intersecting"))


def _hex(cx, cy, flats, z0, z1):
    r = flats / math.sqrt(3.0)
    pts = [(cx + r * math.cos(math.radians(60 * i)), cy + r * math.sin(math.radians(60 * i)), z0) for i in range(6)]
    return B.prism(pts, (0, 0, z1 - z0))


case("cutout_hex_crossed",
     lambda: B.cut(B.box(0, 0, 0, 40, 40, 40), _hex(20, 20, 12, -1, 41),
                   B.box(-1, 17, 15, 41, 23, 25)),
     [Expect(K.CUTOUT, S.plane() & S.Sel(lambda f, s: abs(f.normal[2]) < 1e-6 and math.hypot(
         f.centroid[0] - 20, f.centroid[1] - 20) < 7.1 and f.area > 1, "hex walls"), None, {"walls": 6})],
     allow=(K.CUTOUT,), tags=("cutout", "mfcad", "intersecting"))


def _nut_trap():
    """An M3 nut trap printed upside down: hex from below, two bridging
    layers (a rectangle, then a square), then the bolt hole."""
    p = B.box(0, 0, 0, 30, 30, 10)
    return B.cut(p, _hex(15, 15, 5.7, -1, 3), B.box(15 - 1.6, 15 - 2.85, 2.9, 15 + 1.6, 15 + 2.85, 3.2),
                 B.box(13.4, 13.4, 3.1, 16.6, 16.6, 3.4), B.cylinder((15, 15, 3.3), 1.6, 8))


case("pocket_nut_trap_bridged",
     _nut_trap,
     [Expect(K.POCKET, S.plane() & S.inside(10, 10, 0.01, 20, 20, 3.41), "stepped", {"pierced": True}),
      Expect(K.HOLE, S.cyl(1.6, Z, (15, 15, 0)), None)],
     tags=("pocket", "printing"))

case("void_internal",
     lambda: B.cut(plate(), B.box(40, 20, 3, 60, 40, 7)),
     [Expect(K.VOID, S.inside(39.9, 19.9, 2.9, 60.1, 40.1, 7.1), "internal", {"min_size": (4, 1e-3)})],
     tags=("void", "printing"))

case("slot_clamp_slit",
     lambda: B.cut(B.box(0, 0, 0, 40, 30, 10), B.cylinder((25, 15, -1), 6, 12), B.box(-1, 14.5, -1, 25, 15.5, 11)),
     [Expect(K.SLOT, S.plane((0, 1, 0), (0, 14.5, 0)) | S.plane((0, 1, 0), (0, 15.5, 0)), "open",
             {"width": (1, 1e-3), "through": True, "profile": "slit"})],
     allow=(K.HOLE,), tags=("slot", "printing"))



# =============================================================================
# Moulding and printing details. Moulded parts carry draft, so most of these
# come in a drafted variant too: walls lean, bores are cones, beams taper.
# =============================================================================

def _vertical(f, s):
    return abs(f.normal[2]) < 0.2


def _not_big(area=100.0):
    return S.Sel(lambda f, s: f.area < area, f"area < {area}")


def _snap(draft=0.0, window=False, mirror=False):
    """A cantilever snap on a 3 mm base: a 1.6 mm beam 12 mm tall with a
    1 mm catch 9.5 mm up and a lead-in to the tip."""
    k = math.tan(math.radians(draft))
    prof = [(20.0, 3), (21.6, 3), (21.6 - 9.5 * k, 12.5), (22.6, 12.5), (22.6, 13.2),
            (21.6 - 12 * k, 15), (20.0 + 12 * k, 15)]
    s = B.fuse(B.box(0, 0, 0, 60, 40, 3), B.prism([(x, 10, z) for x, z in prof], (0, 8, 0)))
    if mirror:
        s = B.fuse(s, B.prism([(40 - x, 22, z) for x, z in prof], (0, 8, 0)))
    if window:
        s = B.cut(s, B.box(21.7, 10, -1, 22.6, 18, 4))
    return s


_SNAP = S.inside(19.9, 9.9, 3.1, 22.7, 18.1, 15.1)
_SNAP2 = S.inside(17.3, 21.9, 3.1, 20.1, 30.1, 15.1)

case("snap_fit_cantilever",
     _snap,
     [Expect(K.SNAP_FIT, _SNAP, "cantilever",
             {"thickness": (1.6, 1e-3), "length": (9.5, 1e-3), "width": (8, 1e-3), "overhang": (1, 1e-3),
              "draft_deg": (0, 0.05), "mould_window": False})],
     tags=("snap_fit", "molding", "printing"),
     notes="A straight cantilever hook: beam, catch, lead-in.")

case("snap_fit_drafted",
     lambda: _snap(1.5),
     [Expect(K.SNAP_FIT, _SNAP, "cantilever",
             {"thickness": (1.6, 0.01), "length": (9.5, 1e-3), "draft_deg": (1.5, 0.05),
              "thickness_tip": (1.104, 0.01)})],
     tags=("snap_fit", "molding", "draft"),
     notes="Both beam faces drafted 1.5 deg: the beam thins toward the hook.")

case("snap_fit_with_mould_window",
     lambda: _snap(1.5, window=True),
     [Expect(K.SNAP_FIT, _SNAP, "cantilever", {"mould_window": True, "draft_deg": (1.5, 0.05)}),
      Expect(K.CUTOUT, S.inside(21.6, 9.9, -0.1, 22.7, 18.1, 3.0), "through")],
     tags=("snap_fit", "molding", "draft"),
     notes="The hole under the catch lets a straight-pull core form it: no side action needed.")

case("snap_fit_pair",
     lambda: _snap(1.0, mirror=True),
     [Expect(K.SNAP_FIT, _SNAP, "cantilever", {"draft_deg": (1.0, 0.05)}),
      Expect(K.SNAP_FIT, _SNAP2, "cantilever", {"draft_deg": (1.0, 0.05)})],
     tags=("snap_fit", "molding", "draft"))


def _hinge(kind="v"):
    p = B.box(0, 0, 0, 64, 20, 2)
    if kind == "v":
        return B.cut(p, B.prism([(30, -1, 2.01), (34, -1, 2.01), (32, -1, 0.4)], (0, 22, 0)))
    if kind == "flat":
        return B.cut(p, B.box(30, -1, 0.4, 34, 21, 3))
    return B.cut(p, B.box(31, -1, 1.2, 33, 21, 3), B.box(31, -1, -1, 33, 21, 0.85))


_HINGE = S.inside(29.9, -0.1, -0.1, 34.1, 20.1, 2.1) & S.Sel(lambda f, s: abs(f.normal[1]) < 0.5, "not an end") \
    & _not_big(200)

case("living_hinge_v",
     lambda: _hinge("v"),
     [Expect(K.LIVING_HINGE, _HINGE, "single", {"web_thickness": (0.4, 1e-3), "length": (20, 1e-3), "profile": "v"})],
     tags=("living_hinge", "molding"),
     notes="A V-groove leaving a 0.4 mm web across a 2 mm lid.")

case("living_hinge_flat",
     lambda: _hinge("flat"),
     [Expect(K.LIVING_HINGE, _HINGE, "single", {"web_thickness": (0.4, 1e-3), "groove_width": (4, 1e-3)})],
     tags=("living_hinge", "molding", "printing"))

case("living_hinge_double",
     lambda: _hinge("double"),
     [Expect(K.LIVING_HINGE, _HINGE, "double", {"web_thickness": (0.35, 1e-3), "sides": 2})],
     tags=("living_hinge", "molding"),
     notes="Grooved from both sides: the web is in the middle of the wall.")


def _crush(draft=True):
    """A bore for a press-fit pin with three crush ribs; the moulded one is a
    cone (draft on the core pin)."""
    blk = B.box(0, 0, 0, 30, 30, 15)
    tool = B.cone((15, 15, 3), 3.9, 4.0, 12.01) if draft else B.cylinder((15, 15, 3), 4.0, 12.01)
    s = B.cut(blk, tool)
    ribs = []
    for a in (0, 120, 240):
        c, sn = math.cos(math.radians(a)), math.sin(math.radians(a))
        ribs.append(B.prism([(15 + r * c - t * sn, 15 + r * sn + t * c, 3)
                             for r, t in ((4.4, -0.4), (4.4, 0.4), (3.6, 0.0))], (0, 0, 12)))
    return B.fuse(s, *ribs)


def _crush_rib(a):
    cx, cy = 15 + 4 * math.cos(math.radians(a)), 15 + 4 * math.sin(math.radians(a))
    return S.plane() & S.Sel(lambda f, s: abs(f.normal[2]) < 0.2 and math.hypot(
        f.centroid[0] - cx, f.centroid[1] - cy) < 0.6, f"crush rib at {a}")


_CRUSH_BORE = (S.cone(Z, (15, 15, 0)) | S.cyl(4, Z, (15, 15, 0)) | S.plane(Z, (0, 0, 3))) \
    & S.inside(10, 10, 2.9, 20, 20, 15.1)

for _draft in (True, False):
    case("crush_ribs_" + ("drafted" if _draft else "straight"),
         lambda d=_draft: _crush(d),
         [Expect(K.HOLE, _CRUSH_BORE, "simple", {"crush_ribs": 3, "diameter": ((7.9 if _draft else 8.0), 0.01),
                                                 "partial": False})]
         + [Expect(K.RIB, _crush_rib(a), "crush", {"interference": ((0.35 if _draft else 0.4), 0.01),
                                                   "length": (12, 1e-3)}) for a in (0, 120, 240)],
         tags=("rib", "hole", "molding") + (("draft",) if _draft else ()),
         notes="Three small ribs inside a bore, crushed by the pressed-in part.")


_GLYPHS = [  # boxes (x0, y0, x1, y1) for I, L, T, O, H
    [(5, 10, 6, 18)],
    [(9, 10, 10, 18), (10, 10, 13, 11)],
    [(15, 17, 19, 18), (16.5, 10, 17.5, 17)],
    [(21, 10, 25, 11), (21, 17, 25, 18), (21, 11, 22, 17), (24, 11, 25, 17)],
    [(27, 10, 28, 18), (30, 10, 31, 18), (28, 13.5, 30, 14.5)],
]


def _text(emboss=False, draft=0.0):
    p = B.box(0, 0, 0, 40, 30, 3)
    z0, z1 = (3, 3.6) if emboss else (2.5, 3.01)
    tools = []
    for g in _GLYPHS:
        parts = [B.drafted_box(x0, y0, z0, x1, y1, z1, draft, grow=not emboss) for x0, y0, x1, y1 in g]
        tools.append(B.fuse(*parts) if len(parts) > 1 else parts[0])
    return B.fuse(p, *tools) if emboss else B.cut(p, *tools)


for _emboss in (False, True):
    for _draft in (0.0, 2.0):
        _name = "text_" + ("embossed" if _emboss else "engraved") + ("_drafted" if _draft else "")
        _sel = (S.inside(4.5, 9.5, 2.95, 31.5, 18.5, 3.65) if _emboss else S.inside(4.5, 9.5, 2.45, 31.5, 18.5, 3.05)) \
            & _not_big()
        case(_name,
             lambda e=_emboss, d=_draft: _text(e, d),
             [Expect(K.TEXT, _sel, "embossed" if _emboss else "engraved",
                     {"glyphs": 5, "depth": ((0.6 if _emboss else 0.5), 1e-3), "glyph_size": (8, 0.1)})],
             tags=("text", "molding", "printing") + (("draft",) if _draft else ()),
             notes="Five 8 mm letters (I L T O H) in 1 mm strokes; not slots, steps or bosses.")


def _vent(draft=0.0, holes=False, cones=False):
    p = B.box(0, 0, 0, 60, 40, 2)
    if cones:
        tools = [B.cone((12 + 4 * i, 10 + 4 * j, -0.01), 1.0, 1.05, 2.02) for i in range(6) for j in range(6)]
    elif holes:
        tools = [B.cylinder((12 + 4 * i, 10 + 4 * j, -1), 1.0, 4) for i in range(6) for j in range(6)]
    else:
        tools = [B.drafted_box(10 + 5 * i, 10, -0.01, 12 + 5 * i, 30, 2.01, draft, grow=True) for i in range(6)]
    return B.cut(p, *tools)


_VENT = S.inside(9.5, 7.5, -0.1, 37.5, 32.5, 2.1) & _not_big(200)

case("vent_slots",
     _vent,
     [Expect(K.VENT, _VENT, "slots", {"count": 6, "pitch": (5, 1e-3), "opening_width": (2, 1e-3),
                                      "opening_length": (20, 1e-3), "open_area": (240, 0.1)})],
     tags=("vent", "molding", "printing"))

case("vent_slots_drafted",
     lambda: _vent(1.0),
     [Expect(K.VENT, _VENT, "slots", {"count": 6, "pitch": (5, 1e-3), "opening_width": (2.07, 0.01)})],
     tags=("vent", "molding", "draft"),
     notes="Each slot opens up 1 deg toward the top.")

case("vent_hole_grille",
     lambda: _vent(holes=True),
     [Expect(K.VENT, _VENT, "holes", {"count": 36, "pitch": (4, 1e-3), "opening_width": (2, 1e-3)})],
     tags=("vent", "printing"))

case("vent_hole_grille_drafted",
     lambda: _vent(cones=True),
     [Expect(K.VENT, _VENT, "holes", {"count": 36, "pitch": (4, 1e-3)})],
     tags=("vent", "molding", "draft"),
     notes="Each hole is a cone, as the core pins are drafted.")


def _open_box():
    return B.shell(B.box(0, 0, 0, 80, 50, 30), 2.0, lambda c: abs(c[2] - 30) < 1e-6)


def _lip(draft=0.0):
    ring = B.cut(B.drafted_box(1, 1, 30, 79, 49, 32, draft), B.box(2, 2, 29, 78, 48, 33))
    return B.fuse(_open_box(), ring)


_INNER = S.Sel(lambda f, s: f.kind == "plane" and f.area > 100 and (
    (abs(f.normal[2]) < 0.2 and 1.9 < f.centroid[0] < 78.1 and 1.9 < f.centroid[1] < 48.1
     and f.centroid[2] < 30) or abs(f.centroid[2] - 2) < 1e-3), "inner walls and floor")

for _draft in (0.0, 1.0):
    case("lip_outer" + ("_drafted" if _draft else ""),
         lambda d=_draft: _lip(d),
         [Expect(K.LIP, S.inside(-0.1, -0.1, 30.5, 80.1, 50.1, 32.1), "outer",
                 {"height": (2, 1e-3), "thickness": ((0.965 if _draft else 1.0), 0.01)}),
          Expect(K.POCKET, _INNER, "flat", {"depth": (30, 1e-3)})],
         tags=("lip", "molding") + (("draft",) if _draft else ()),
         notes="A 1 mm locating lip on the outer half of a housing rim.")


def _groove(draft=0.0):
    if draft:
        ring = B.cut(B.drafted_box(0.5, 0.5, 28.5, 79.5, 49.5, 30.01, draft, grow=True),
                     B.drafted_box(1.5, 1.5, 28.5, 78.5, 48.5, 30.01, draft))
    else:
        ring = B.cut(B.box(0.5, 0.5, 28.5, 79.5, 49.5, 31), B.box(1.5, 1.5, 28, 78.5, 48.5, 32))
    return B.cut(_open_box(), ring)


for _draft in (0.0, 2.0):
    case("groove_housing_rim" + ("_drafted" if _draft else ""),
         lambda d=_draft: _groove(d),
         [Expect(K.SLOT, S.inside(-0.1, -0.1, 28.4, 80.1, 50.1, 29.9), "ring",
                 {"width": (1, 1e-3), "depth": (1.5, 1e-3), "open_ends": 0}),
          Expect(K.POCKET, _INNER, "flat")],
         tags=("slot", "molding") + (("draft",) if _draft else ()),
         notes="A tongue-and-groove or gasket groove around a housing rim.")


def _oring():
    disc = B.cylinder((0, 0, 0), 30, 5)
    groove = B.cut(B.cylinder((0, 0, 3), 18, 3), B.cylinder((0, 0, 2.9), 15, 4))
    return B.cut(disc, groove, B.drill((0, 0, 5), DOWN, 10, 5))


case("groove_oring_face",
     _oring,
     [Expect(K.SLOT, S.cyl(18, Z, (0, 0, 0)) | S.cyl(15, Z, (0, 0, 0)) | S.plane(Z, (0, 0, 3)), "ring",
             {"width": (3, 1e-3), "depth": (2, 1e-3), "inner_diameter": (30, 1e-3), "outer_diameter": (36, 1e-3)}),
      Expect(K.HOLE, S.cyl(5, Z, (0, 0, 0)), "simple", {"diameter": (10, 1e-3)})],
     tags=("slot", "molding", "machining"),
     notes="A face groove for an O-ring around a bore.")


def _teardrop():
    blk = B.box(0, 0, 0, 30, 20, 20)
    r = 3.0
    q = r / math.sqrt(2)
    tri = B.prism([(15 - q, -1, 10 + q), (15 + q, -1, 10 + q), (15, -1, 10 + r * math.sqrt(2))], (0, 22, 0))
    return B.cut(blk, B.fuse(B.cylinder((15, -1, 10), r, 22, (0, 1, 0)), tri))


case("hole_teardrop",
     _teardrop,
     [Expect(K.HOLE, S.inside(11, -0.1, 6, 19, 20.1, 14.5) & S.Sel(lambda f, s: abs(f.normal[1]) < 0.5 if f.kind == "plane"
                                                                  else True, "bore and point"), "simple",
             {"diameter": (6, 1e-3), "profile": "teardrop", "point_angle": (90, 0.1), "through": True})],
     tags=("hole", "printing"),
     notes="A horizontal hole with a 90 deg point on top, so it prints without support.")

for _draft in (False, True):
    case("boss_pin" + ("_drafted" if _draft else ""),
         lambda d=_draft: B.fuse(plate(), B.cone((50, 30, 10), 1.0, 0.9, 8) if d else B.cylinder((50, 30, 10), 1.0, 8)),
         [Expect(K.BOSS, S.inside(48.9, 28.9, 10.1, 51.1, 31.1, 18.1), "pin",
                 {"outer_diameter": (2, 1e-3), "height": (8, 1e-3), "slenderness": (4, 0.01),
                  "taper_deg": ((0.716, 0.01) if _draft else (0, 0.01))})],
         tags=("boss", "molding", "printing") + (("draft",) if _draft else ()),
         notes="A solid locating pin, four diameters tall.")


def _nut_trap_hex():
    blk = B.box(0, 0, 0, 20, 20, 10)
    return B.cut(blk, _hex(10, 10, 5.5, -1, 2.5), B.cylinder((10, 10, -1), 1.6, 12))


case("pocket_nut_trap",
     _nut_trap_hex,
     [Expect(K.POCKET, S.plane() & S.inside(6.5, 6.5, 0.01, 13.5, 13.5, 2.6), "nut_trap",
             {"across_flats": (5.5, 1e-3), "nut_size": "M3"}),
      Expect(K.HOLE, S.cyl(1.6, Z, (10, 10, 0)), "simple", {"diameter": (3.2, 1e-3)})],
     tags=("pocket", "printing"))


def _nut_slot():
    blk = B.box(0, 0, 0, 20, 20, 20)
    return B.cut(blk, B.cylinder((10, 10, -1), 1.6, 22), B.box(7.2, -1, 8.7, 12.8, 12.8, 11.3))


case("slot_nut_slot",
     _nut_slot,
     [Expect(K.SLOT, S.plane() & S.inside(7.1, -0.1, 8.6, 12.9, 12.9, 11.4), "nut_slot",
             {"nut_thickness": (2.6, 1e-3), "nut_width": (5.6, 1e-3), "nut_size": "M3"}),
      Expect(K.HOLE, S.cyl(1.6, Z, (10, 10, 0)), "simple")],
     tags=("slot", "printing"),
     notes="A nut pushed in from the side, across an M3 bolt hole.")


def _vented_void():
    return B.cut(B.box(0, 0, 0, 60, 40, 10), B.box(15, 10, 3, 45, 30, 7),
                 B.drill((20, 15, 10), DOWN, 3, 3.5), B.drill((40, 25, 10), DOWN, 3, 3.5))


case("void_vented",
     _vented_void,
     [Expect(K.VOID, S.inside(14.9, 9.9, 2.9, 45.1, 30.1, 7.1), "vented",
             {"escape_holes": 2, "min_escape_diameter": (3, 1e-3)}),
      Expect(K.HOLE, S.cyl(1.5, Z, (20, 15, 0)), "simple"),
      Expect(K.HOLE, S.cyl(1.5, Z, (40, 25, 0)), "simple")],
     tags=("void", "printing"),
     notes="A hollow with two drain holes: resin or powder can escape.")


def _standoff(draft=False):
    s = B.fuse(_open_box(), B.cone((5, 25, 2), 4.3, 4, 20) if draft else B.cylinder((5, 25, 2), 4, 20))
    return B.cut(s, B.drill((5, 25, 22), DOWN, 2.5, 15))


for _draft in (False, True):
    case("boss_tied_to_wall" + ("_drafted" if _draft else ""),
         lambda d=_draft: _standoff(d),
         [Expect(K.BOSS, (S.cyl(4, Z, (5, 25, 0)) | S.cone(Z, (5, 25, 0)) | S.plane(Z, (0, 0, 22)))
                 & S.inside(0, 20, 2.1, 10, 30, 22.1), "screw",
                 {"outer_diameter": ((8.6 if _draft else 8.0), 1e-3), "height": (20, 1e-3)}),
          Expect(K.HOLE, S.cyl(1.25, Z, (5, 25, 0)), "simple", {"depth": (15, 1e-3)}),
          Expect(K.POCKET, _INNER, "flat")],
         tags=("boss", "molding") + (("draft",) if _draft else ()),
         notes="A screw boss merged into a side wall, as in most housings.")


def _bridge():
    return B.fuse(B.box(0, 0, 0, 40, 20, 5), B.box(0, 0, 5, 5, 20, 15), B.box(35, 0, 5, 40, 20, 15),
                  B.box(0, 0, 15, 40, 20, 18), B.box(40, 0, 12, 50, 20, 18))


case("printing_bridge_and_overhang",
     _bridge,
     [Expect(K.CUTOUT, S.inside(4.9, -0.1, 4.9, 35.1, 20.1, 15.1) & S.plane()
             & S.Sel(lambda f, s: abs(f.normal[1]) < 0.5, "tunnel walls"), "through")],
     allow=(K.STEP,), tags=("printing", "directional"),
     notes="A 30 mm bridge over a tunnel and a flat 10 mm overhang beside it.",
     directional=DirectionalExpect(print_direction=Z, allow=(K.DRAFT,), features=[
         Expect(K.BRIDGE, S.plane(Z, (0, 0, 15)) & S.inside(5, 0, 15, 35, 20, 15), "flat",
                {"span": (30, 1e-3), "too_long": True}),
         Expect(K.OVERHANG, S.plane(Z, (0, 0, 12)), "flat", {"height_above_bed": (12, 1e-3)})]))

def by_name(name: str) -> Case:
    for c in CASES:
        if c.name == name:
            return c
    raise KeyError(name)


def select_cases(names=None, tags=None, pattern: str = "") -> list[Case]:
    out = []
    for c in CASES:
        if names and c.name not in names:
            continue
        if tags and not set(tags) & set(c.tags):
            continue
        if pattern and pattern not in c.name:
            continue
        out.append(c)
    return out
