# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""What recognizers share: the working context, candidates, scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..model import FeatureKind, Frame
from .._graph import AAG
from .._topology import Topology
from .._topology import vec as V


class Score:
    """Confidence with its reasons: hard rules are checked before a candidate
    is made; soft ones scale the score and say so."""

    def __init__(self, base: float = 0.95):
        self.value = base
        self.evidence: list[str] = []

    def ok(self, text: str) -> "Score":
        self.evidence.append(f"+ {text}")
        return self

    def penalize(self, factor: float, text: str) -> "Score":
        factor = max(0.0, min(1.0, factor))
        self.value *= factor
        self.evidence.append(f"- {text} (x{factor:.2f})")
        return self

    @property
    def confidence(self) -> float:
        return max(0.0, min(1.0, self.value))


@dataclass
class Candidate:
    kind: FeatureKind
    subtype: str
    faces: list[int]
    support: list[int] = field(default_factory=list)
    edges: list[int] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    frame: Optional[Frame] = None
    score: Score = field(default_factory=Score)
    #: Internal links resolved by arbitration (not exported).
    links: dict[str, Any] = field(default_factory=dict)
    id: int = 0
    parent: Optional[int] = None
    interacts: set[int] = field(default_factory=set)


class Context:
    """Shared state while recognising one shape."""

    def __init__(self, topo: Topology, aag: AAG, options, strict: bool = False):
        self.topo = topo
        self.aag = aag
        self.options = options
        self.strict = strict
        self.owner: dict[int, int] = {}          # face -> candidate id
        self.accepted: list[Candidate] = []
        self.rejected: list[tuple[Candidate, str]] = []
        self.warnings: list[str] = []
        self._next = 1

    # -- Faces ------------------------------------------------------------------

    def info(self, face: int):
        return self.topo.faces[face]

    def free(self, face: int) -> bool:
        return face not in self.owner and not self.aag.is_hidden(face)

    def unowned(self, face: int) -> bool:
        return face not in self.owner

    def free_faces(self, kinds: Optional[Iterable[str]] = None) -> list[int]:
        ks = set(kinds) if kinds else None
        return [f for f in self.aag.faces if f not in self.owner and (ks is None or self.info(f).kind in ks)]

    def candidate(self, cid: int) -> Optional[Candidate]:
        for c in self.accepted:
            if c.id == cid:
                return c
        return None

    def owner_candidate(self, face: int) -> Optional[Candidate]:
        cid = self.owner.get(face)
        return self.candidate(cid) if cid is not None else None

    # -- Accepting candidates ----------------------------------------------------

    def accept(self, cand: Candidate, *, max_overlap: float = 0.5) -> bool:
        """Claim the candidate's faces. Faces already owned are dropped from
        it; if too many were, the candidate is rejected."""
        faces = list(dict.fromkeys(cand.faces))
        taken = [f for f in faces if f in self.owner]
        if not faces:
            self.rejected.append((cand, "no faces"))
            return False
        if taken:
            if len(taken) / len(faces) > max_overlap:
                self.rejected.append((cand, f"faces {taken} already recognised"))
                return False
            for f in taken:
                cand.interacts.add(self.owner[f])
            faces = [f for f in faces if f not in self.owner]
            cand.score.penalize(0.85, f"shares faces {taken} with another feature")
        cand.faces = faces
        cand.id = self._next
        self._next += 1
        for f in faces:
            self.owner[f] = cand.id
        self.accepted.append(cand)
        return True

    def warn(self, text: str) -> None:
        self.warnings.append(text)


# -- Geometry helpers ------------------------------------------------------------------


def face_points(topo: Topology, face: int) -> list[V.Vec]:
    """Points along the boundary of a face (from its edges' samples)."""
    info = topo.faces[face]
    pts: list[V.Vec] = []
    for e in info.edges:
        edge = topo.edges[e]
        if edge is not None:
            pts.extend(edge.points)
    if not pts and info.sample_point:
        pts.append(info.sample_point)
    return pts


def extent(points: Iterable[V.Vec], direction: V.Vec, origin: V.Vec = (0.0, 0.0, 0.0)) -> tuple[float, float]:
    dx, dy, dz = direction[0], direction[1], direction[2]
    off = origin[0] * dx + origin[1] * dy + origin[2] * dz
    vals = [p[0] * dx + p[1] * dy + p[2] * dz for p in points]   # hot: inlined dot products
    if not vals:
        return (0.0, 0.0)
    return (min(vals) - off, max(vals) - off)


def shared_edges(topo: Topology, a: int, b: int) -> list[int]:
    ea = set(topo.faces[a].edges)
    return [e for e in topo.faces[b].edges if e in ea]


def parallel(a: V.Vec, b: V.Vec, tol_deg: float) -> bool:
    return V.line_angle(a, b) <= tol_deg


def perpendicular(a: V.Vec, b: V.Vec, tol_deg: float) -> bool:
    return abs(90.0 - V.angle(a, b)) <= tol_deg


def axial_interval(info, origin: V.Vec, direction: V.Vec) -> tuple[float, float]:
    """A revolved face's [start, end] along an axis through origin/direction."""
    base = V.dot(V.sub(info.origin, origin), direction)
    s = 1.0 if V.dot(info.axis, direction) >= 0 else -1.0
    a = base + s * info.axial_min
    b = base + s * info.axial_max
    return (min(a, b), max(a, b))


def r3(x: float) -> float:
    return round(float(x), 4)


def vec3(v: V.Vec) -> tuple[float, float, float]:
    return (r3(v[0]), r3(v[1]), r3(v[2]))


def deg(x: float) -> float:
    return round(float(x), 3)


def finite(x: float) -> bool:
    return math.isfinite(x)
