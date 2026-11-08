# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The part's overall form: how it could have been modelled.

Feature-recognition tools that rebuild a parametric model (SolidWorks'
FeatureWorks, for one) start with the *base feature*: the extrusion or
revolution the part is made from, with everything else added to it or cut
from it. This finds that, for the whole part, from the faces that are not
fillets or chamfers:

* **extrusion**: every face is a wall along one direction (a plane or
  cylinder parallel to it, drafted by up to ``max_draft``) or a cap across
  it, at two levels only. It is one profile pushed straight through, such
  as a bracket cut from plate or an extruded profile.
* **2.5d**: the same along one direction, but with caps at more than two
  levels. It is an extrusion plus extruded cuts and bosses (pockets,
  steps, counterbores) along the same direction, which covers most
  machined and many moulded parts.
* **revolve**: every face is a surface of revolution about one axis, or a
  plane across it. It is a turned part.
* **mixed**: none of these covers everything. The direction covering the
  most area is still reported, with the faces it doesn't explain (for
  example, a cradle cut across the walls).

No faces are owned: this describes the part and is not one of its
features. It is reported as ``RecognitionResult.form``.
"""

from __future__ import annotations

import math
from typing import Optional

from ._topology import vec as V
from .model import FeatureKind

_BLENDS = (FeatureKind.FILLET, FeatureKind.CHAMFER)


def _extrusion_direction(info) -> Optional[V.Vec]:
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        d = BRepAdaptor_Surface(info.face).Direction()
        return (d.X(), d.Y(), d.Z())
    except Exception:
        return None


def _fits(info, d: V.Vec, max_draft: float) -> tuple[str, float]:
    """("wall" | "cap" | "", draft in degrees) for one face along *d*."""
    sin_d = math.sin(math.radians(max_draft))
    if info.kind == "plane":
        c = abs(V.dot(info.normal, d))
        if c > math.cos(math.radians(0.5)):
            return "cap", 0.0
        if c <= sin_d:
            return "wall", math.degrees(math.asin(min(1.0, c)))
        return "", 0.0
    if info.kind in ("cylinder", "cone") and info.axis is not None:
        if abs(abs(V.dot(info.axis, d)) - 1.0) < 1e-6:
            draft = info.semi_angle if info.kind == "cone" else 0.0
            return ("wall", draft) if draft <= max_draft else ("", 0.0)
        return "", 0.0
    if info.kind == "extrusion":
        e = _extrusion_direction(info)
        if e is not None and abs(abs(V.dot(V.unit(e), d)) - 1.0) < 1e-6:
            return "wall", 0.0
    return "", 0.0


def _revolve_fits(info, origin: V.Vec, axis: V.Vec, tol: float) -> bool:
    if info.kind == "plane":
        return abs(abs(V.dot(info.normal, axis)) - 1.0) < 1e-6
    if info.kind in ("cylinder", "cone", "torus", "sphere", "revolution"):
        if info.kind == "sphere":
            return info.origin is not None and V.point_line_distance(info.origin, origin, axis) <= tol
        if info.axis is None or info.origin is None:
            return False
        return abs(abs(V.dot(info.axis, axis)) - 1.0) < 1e-6 and \
            V.point_line_distance(info.origin, origin, axis) <= tol
    return False


def part_form(topo, features, options) -> dict:
    """The part's form (see the module docstring) as plain data."""
    blend_faces = {face for f in features if f.kind in _BLENDS for face in f.faces}
    infos = [topo.faces[i] for i in range(1, topo.face_count + 1) if i not in blend_faces]
    total = sum(i.area for i in infos) or 1.0
    tol = max(options.linear_tolerance * 10, 1e-6, 1e-6 * topo.scale)

    # candidate directions: plane normals and axes, largest faces first
    dirs: list[V.Vec] = []
    for info in sorted(infos, key=lambda i: -i.area):
        for d in ((info.normal,) if info.kind == "plane" else
                  (info.axis,) if info.kind in ("cylinder", "cone") and info.axis is not None else ()):
            if d is None:
                continue
            d = V.unit(d)
            if not any(abs(abs(V.dot(d, e)) - 1.0) < 1e-6 for e in dirs):
                dirs.append(d)
        if len(dirs) >= 12:
            break

    best = None
    for d in dirs:
        covered, other, draft = 0.0, [], 0.0
        levels: list[float] = []
        for info in infos:
            role, dr = _fits(info, d, options.max_draft)
            if role:
                covered += info.area
                draft = max(draft, dr)
                if role == "cap":
                    h = V.dot(info.centroid, d)
                    if not any(abs(h - x) <= tol * 10 for x in levels):
                        levels.append(h)
            else:
                other.append(info.index)
        key = (covered, -len(levels))
        if best is None or key > best[0]:
            best = (key, d, covered, other, draft, sorted(levels))

    revolve = None
    for info in sorted(infos, key=lambda i: -i.area):
        if info.kind in ("cylinder", "cone", "torus") and info.axis is not None and info.origin is not None:
            if all(_revolve_fits(i, info.origin, info.axis, tol) for i in infos):
                revolve = info
            break

    if revolve is not None:
        return {"form": "revolve", "direction": tuple(round(c, 6) for c in V.unit(revolve.axis)),
                "origin": tuple(round(c, 6) for c in revolve.origin), "coverage": 1.0, "other_faces": []}
    if best is None:
        return {"form": "mixed", "coverage": 0.0, "other_faces": [i.index for i in infos]}
    _key, d, covered, other, draft, levels = best
    # report the direction with its largest component positive (+Z, not -Z)
    if max(d, key=abs) < 0:
        d = V.mul(d, -1.0)
        levels = sorted(-x for x in levels)
    coverage = covered / total
    if not other:
        form = "extrusion" if len(levels) <= 2 else "2.5d"
    else:
        form = "mixed"
    out = {
        "form": form,
        "direction": tuple(round(c, 6) for c in d),
        "coverage": round(coverage, 4),
        "levels": len(levels),
        "length": round(levels[-1] - levels[0], 6) if len(levels) >= 2 else 0.0,
        "draft_deg": round(draft, 3),
        "other_faces": other,
    }
    return out
