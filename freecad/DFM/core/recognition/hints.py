# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Hints: what the model's history (or a user) says a feature is.

Vandenbrande & Requicha note hints need not come from geometry alone:
design attributes are hints too. The workbench turns PartDesign features
(Hole, Fillet, Chamfer, Pocket, Pad, ...) into ``Hint`` objects: plain
data, with points on the faces the feature made, so this package never
needs FreeCAD. The points are matched to faces here.

A hint that agrees with a recognised feature raises its confidence and
adds design parameters (a cosmetic thread, the designed diameter). One
that disagrees lowers it. One with no feature on its faces becomes a
feature of its own, with modest confidence, if it names a single kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .model import FeatureKind


@dataclass(frozen=True)
class Hint:
    #: Kinds the hint is compatible with, most likely first. A PartDesign
    #: Pocket may be a pocket, slot, step, cutout or hole.
    kinds: tuple[FeatureKind, ...]
    #: Points (model coordinates) on faces the feature made.
    points: tuple[tuple[float, float, float], ...]
    params: dict = field(default_factory=dict)
    subtype: str = ""
    source: str = ""            # e.g. "PartDesign::Hole 'Hole001'"
    weight: float = 1.0         # 0..1: how much to trust it

    def to_dict(self) -> dict:
        return {"kinds": [k.value for k in self.kinds], "points": [list(p) for p in self.points],
                "params": dict(self.params), "subtype": self.subtype, "source": self.source,
                "weight": self.weight}

    @classmethod
    def from_dict(cls, d: dict) -> "Hint":
        return cls(kinds=tuple(FeatureKind(k) for k in d["kinds"]),
                   points=tuple(tuple(p) for p in d.get("points", ())),
                   params=dict(d.get("params", {})), subtype=d.get("subtype", ""),
                   source=d.get("source", ""), weight=float(d.get("weight", 1.0)))


def resolve_points(topo, points: Iterable, tolerance: float) -> set[int]:
    """Face numbers the points lie on (within tolerance)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt

    found: set[int] = set()
    for p in points:
        x, y, z = p
        best, best_d = None, None
        for info in topo.iter_faces():
            x0, y0, z0, x1, y1, z1 = info.bbox
            if not (x0 - tolerance <= x <= x1 + tolerance and y0 - tolerance <= y <= y1 + tolerance
                    and z0 - tolerance <= z <= z1 + tolerance):
                continue
            v = BRepBuilderAPI_MakeVertex(gp_Pnt(x, y, z)).Vertex()
            dist = BRepExtrema_DistShapeShape(v, info.face)
            if not dist.IsDone() or dist.NbSolution() == 0:
                continue
            d = dist.Value()
            if d <= tolerance and (best_d is None or d < best_d):
                best, best_d = info.index, d
        if best is not None:
            found.add(best)
    return found


def apply_hints(ctx, hints: Iterable[Hint]) -> None:
    """Fold hints into the context's accepted candidates (before linking)."""
    from ._recognizers.common import Candidate, Score

    tol = max(1e-3 * ctx.topo.scale, 0.01, ctx.options.linear_tolerance * 10)
    for hint in hints:
        faces = resolve_points(ctx.topo, hint.points, tol)
        if not faces:
            ctx.warn(f"hint {hint.source or hint.kinds[0].value}: none of its points is on the shape")
            continue
        owners = {}
        for f in faces:
            cid = ctx.owner.get(f)
            if cid is not None:
                owners[cid] = owners.get(cid, 0) + 1
        matched = False
        for cid in sorted(owners, key=lambda c: -owners[c]):
            cand = ctx.candidate(cid)
            if cand is None:
                continue
            if cand.kind in hint.kinds:
                matched = True
                w = max(0.0, min(1.0, hint.weight))
                cand.score.value = 1.0 - (1.0 - cand.score.value) * (1.0 - 0.7 * w)
                cand.score.ok(f"confirmed by {hint.source or 'a hint'}")
                for k, v in hint.params.items():
                    if k not in cand.params:
                        cand.params[k] = v
                    elif cand.params[k] != v:
                        cand.params[f"design_{k}"] = v
                if hint.params.get("threaded") and cand.kind == FeatureKind.HOLE:
                    cand.params["threaded"] = True
                    if cand.subtype == "simple":
                        cand.subtype = "threaded"
                break
        if matched:
            continue
        if owners:
            for cid in owners:
                cand = ctx.candidate(cid)
                if cand is not None and cand.kind not in (FeatureKind.FILLET, FeatureKind.CHAMFER):
                    cand.score.penalize(0.85, f"{hint.source or 'a hint'} says "
                                        + "/".join(k.value for k in hint.kinds))
        free = sorted(f for f in faces if f not in ctx.owner)
        # Only a hint that says exactly what it is (a Hole, a Fillet) makes a
        # feature of its own; a Pad or Pocket could be several things. And
        # only where the geometry can be that: history says what was asked
        # for, not what is left after later features.
        if free and len(hint.kinds) == 1:
            kind = hint.kinds[0]
            fits, subtype = _geometry_fits(ctx, kind, hint.subtype, free)
            if not fits:
                ctx.warn(f"hint {hint.source or kind.value}: its faces don't look like a {kind.value}; ignored")
                continue
            score = Score(0.5 * max(0.1, min(1.0, hint.weight)))
            score.ok(f"from {hint.source or 'a hint'} only: not recognised from the geometry")
            params = dict(hint.params)
            params["from_history"] = True
            if hint.subtype and subtype != hint.subtype:
                params["design_subtype"] = hint.subtype
                score.penalize(0.9, f"history says {hint.subtype}, the geometry doesn't show it")
            cand = Candidate(kind=kind, subtype=subtype, faces=free, params=params, score=score)
            ctx.accept(cand)


def _geometry_fits(ctx, kind: FeatureKind, subtype: str, faces) -> tuple[bool, str]:
    """(whether *faces* can be a *kind*, the subtype they show). A hole needs
    an inside cylinder or cone; it is countersunk only with a cone, and
    counterbored only with two coaxial cylinders."""
    infos = [ctx.info(f) for f in faces]
    if kind == FeatureKind.HOLE:
        cyl = [i for i in infos if i.kind == "cylinder" and getattr(i, "internal", False)]
        cones = [i for i in infos if i.kind == "cone"]
        if not cyl and not cones:
            return False, ""
        if subtype == "countersunk" and not cones:
            return True, "from_history"
        if subtype in ("counterbored", "counterdrilled") and len({round(i.radius, 6) for i in cyl}) < 2:
            return True, "from_history"
        return True, subtype or "from_history"
    if kind == FeatureKind.FILLET:
        return any(i.kind in ("cylinder", "torus", "bspline", "sphere") for i in infos), "from_history"
    if kind == FeatureKind.CHAMFER:
        return any(i.kind in ("plane", "cone") for i in infos), "from_history"
    if kind == FeatureKind.BOSS:
        return any(i.kind in ("cylinder", "cone") and not getattr(i, "internal", True) for i in infos), \
            subtype or "from_history"
    return True, subtype or "from_history"
