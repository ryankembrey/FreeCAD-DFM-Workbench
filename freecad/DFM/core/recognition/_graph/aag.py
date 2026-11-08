# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The Attributed Adjacency Graph (Joshi & Chang, 1988).

Nodes are faces (by face number), arcs are the edges two faces share,
labelled with how the faces meet: convex, concave or smooth (tangent).
Two faces can share several edges, so this is a multigraph.

Blend suppression (Zhu & Menq 2002; Slyadnev & Turlapov 2020) works on the
graph only: a recognised fillet or chamfer face is *hidden* and its support
faces get a *virtual* arc with the convexity the blend stood for (a concave
fillet gives a concave arc). The shape itself is never changed, so face
numbers stay valid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from .._topology import Topology
from .._topology import vec as V


@dataclass(frozen=True)
class Arc:
    a: int
    b: int
    edge: int               # edge number, 0 for a virtual arc
    convexity: str          # convex, concave, smooth, variable
    angle: float = 0.0      # signed turn angle (degrees)
    virtual: bool = False
    via: Optional[int] = None   # hidden blend face a virtual arc stands for

    def other(self, face: int) -> int:
        return self.b if self.a == face else self.a


@dataclass
class Blend:
    """A hidden blend face: what it stood for between its supports."""

    face: int
    kind: str               # "fillet", "round", "chamfer"
    convexity: str          # convexity of the virtual arcs it leaves: concave / convex
    supports: tuple[int, ...]
    radius: float = 0.0


class AAG:
    def __init__(self, topo: Topology, options):
        self.topo = topo
        self.options = options
        self.adj: dict[int, list[Arc]] = {i: [] for i in range(1, topo.face_count + 1)}
        self.hidden: dict[int, Blend] = {}
        for e in topo.iter_edges():
            if not e.is_manifold:
                continue
            arc = Arc(e.faces[0], e.faces[1], e.index, e.convexity, e.angle)
            self.adj[arc.a].append(arc)
            self.adj[arc.b].append(arc)
        self._groups = self._logical_groups()

    # -- Basic queries --------------------------------------------------------------

    @property
    def faces(self) -> list[int]:
        return [f for f in self.adj if f not in self.hidden]

    def info(self, face: int):
        return self.topo.faces[face]

    def arcs(self, face: int, *, visible: bool = True) -> list[Arc]:
        """Arcs of *face*. With visible=True (default), arcs to hidden blends
        are left out and the virtual arcs they left are included."""
        if not visible:
            return [a for a in self.adj[face] if not a.virtual]
        return [a for a in self.adj[face] if a.other(face) not in self.hidden]

    def neighbors(self, face: int, *convexities: str, visible: bool = True) -> set[int]:
        return {a.other(face) for a in self.arcs(face, visible=visible)
                if not convexities or a.convexity in convexities}

    def arcs_between(self, a: int, b: int, *, visible: bool = True) -> list[Arc]:
        return [arc for arc in self.arcs(a, visible=visible) if arc.other(a) == b]

    def convexity_between(self, a: int, b: int) -> set[str]:
        return {arc.convexity for arc in self.arcs_between(a, b)}

    def is_hidden(self, face: int) -> bool:
        return face in self.hidden

    def edge_class(self, face: int, edge: int) -> str:
        """How *face* meets whatever is across *edge*, seeing through hidden
        blends (an edge to a concave fillet counts as concave)."""
        e = self.topo.edges[edge]
        if e is None or not e.is_manifold:
            return e.convexity if e else "none"
        other = e.other(face)
        if other in self.hidden and face in self.hidden[other].supports:
            return self.hidden[other].convexity
        return e.convexity

    def across(self, face: int, edge: int) -> set[int]:
        """The visible face(s) across *edge* from *face*, stepping over hidden
        blends to their other supports."""
        e = self.topo.edges[edge]
        if e is None or not e.is_manifold:
            return set()
        other = e.other(face)
        if other not in self.hidden:
            return {other}
        out: set[int] = set()
        seen = {face}
        stack = [other]
        while stack:
            h = stack.pop()
            if h in seen:
                continue
            seen.add(h)
            if h not in self.hidden:
                out.add(h)
                continue
            for s in self.hidden[h].supports:
                if s not in seen:
                    stack.append(s)
            for arc in self.adj[h]:
                o = arc.other(h)
                if o in self.hidden and o not in seen:
                    stack.append(o)
        out.discard(face)
        return out

    # -- Blend suppression ------------------------------------------------------------

    def hide(self, blend: Blend) -> None:
        """Hide a blend face, joining its supports with virtual arcs."""
        self.hidden[blend.face] = blend
        sup = [s for s in blend.supports if s != blend.face]
        for i, s in enumerate(sup):
            for t in sup[i + 1:]:
                if s == t:
                    continue
                if any(a.other(s) == t for a in self.adj[s]):
                    continue  # already adjacent (really, or through another blend)
                arc = Arc(s, t, 0, blend.convexity, 0.0, True, blend.face)
                self.adj[s].append(arc)
                self.adj[t].append(arc)

    # -- Logical faces --------------------------------------------------------------------

    def _logical_groups(self) -> dict[int, int]:
        """Group faces that are one surface split in pieces: coaxial equal
        cylinders / cones / spheres and coplanar planes, joined by a smooth
        edge. Returns face -> representative."""
        parent = {f: f for f in self.adj}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for f, arcs in self.adj.items():
            for arc in arcs:
                g = arc.other(f)
                if g <= f or arc.convexity != "smooth":
                    continue
                if self.same_surface(f, g):
                    parent[find(g)] = find(f)
        return {f: find(f) for f in self.adj}

    def same_surface(self, f: int, g: int) -> bool:
        a, b = self.info(f), self.info(g)
        tol = self.options.linear_tolerance * 10
        ang = self.options.parallel_angle
        if a.kind != b.kind:
            return False
        if a.kind == "plane":
            if V.angle(a.normal, b.normal) > ang:
                return False
            return abs(V.dot(V.sub(b.origin, a.origin), a.normal)) < tol
        if a.kind in ("cylinder", "cone", "sphere", "torus"):
            if a.internal != b.internal:
                return False
            if abs(a.radius - b.radius) > tol and a.kind != "cone":
                return False
            if a.kind == "sphere":
                return V.dist(a.origin, b.origin) < tol
            return self.coaxial(f, g)
        return False

    def coaxial(self, f: int, g: int) -> bool:
        a, b = self.info(f), self.info(g)
        if a.axis is None or b.axis is None:
            return False
        if V.line_angle(a.axis, b.axis) > self.options.parallel_angle:
            return False
        tol = max(self.options.linear_tolerance * 10, 1e-6 * self.topo.scale)
        return V.point_line_distance(b.origin, a.origin, a.axis) < tol

    def group(self, face: int) -> int:
        return self._groups.get(face, face)

    def group_members(self, face: int) -> list[int]:
        rep = self.group(face)
        return [f for f, r in self._groups.items() if r == rep]

    # -- Components -------------------------------------------------------------------------

    def components(self, faces: Iterable[int], arc_ok: Callable[[Arc], bool] = lambda a: True) -> list[list[int]]:
        pool = set(faces)
        out = []
        while pool:
            start = min(pool)
            pool.discard(start)
            comp = [start]
            stack = [start]
            while stack:
                f = stack.pop()
                for arc in self.arcs(f):
                    g = arc.other(f)
                    if g in pool and arc_ok(arc):
                        pool.discard(g)
                        comp.append(g)
                        stack.append(g)
            out.append(sorted(comp))
        return out

    def all_convex(self, face: int) -> bool:
        """Joshi & Chang: a face meeting all its neighbours convexly is not
        part of a depression feature."""
        arcs = self.arcs(face)
        return bool(arcs) and all(a.convexity in ("convex", "smooth") for a in arcs)

    # -- Export ---------------------------------------------------------------------------------

    def to_dict(self) -> dict:
        nodes = []
        for f in self.adj:
            i = self.info(f)
            nodes.append({"face": f, "kind": i.kind, "internal": i.internal, "area": round(i.area, 6),
                          "hidden": f in self.hidden, "group": self.group(f)})
        arcs = []
        seen = set()
        for f, lst in self.adj.items():
            for a in lst:
                key = (a.a, a.b, a.edge, a.via)
                if key in seen:
                    continue
                seen.add(key)
                arcs.append({"a": a.a, "b": a.b, "edge": a.edge, "convexity": a.convexity,
                             "angle": round(a.angle, 4), "virtual": a.virtual, "via": a.via})
        return {"nodes": nodes, "arcs": arcs}
