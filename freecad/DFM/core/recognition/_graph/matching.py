# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Subgraph matching on the AAG (Ullmann 1976 / VF2, Cordella et al. 2004).

A pattern is a small graph of node predicates and required arcs between
pattern nodes. ``match(aag, pattern, candidates)`` finds every injective
mapping of pattern nodes to visible faces such that every node predicate
holds and every pattern arc exists with an allowed convexity. Candidate
sets are filtered by node predicates first and extended only along
adjacency (VF2-style), so the search stays small inside the pruned
components it is run on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from .aag import AAG

NodePredicate = Callable[[AAG, int], bool]


@dataclass
class Pattern:
    name: str
    nodes: dict[str, NodePredicate] = field(default_factory=dict)
    #: (node a, node b, allowed convexities)
    arcs: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    #: (node a, node b) that must NOT be adjacent
    not_adjacent: list[tuple[str, str]] = field(default_factory=list)
    #: Extra test on a complete mapping.
    check: Optional[Callable[[AAG, dict[str, int]], bool]] = None

    def node(self, name: str, predicate: NodePredicate = lambda g, f: True) -> "Pattern":
        self.nodes[name] = predicate
        return self

    def arc(self, a: str, b: str, *convexities: str) -> "Pattern":
        self.arcs.append((a, b, tuple(convexities) or ("convex", "concave", "smooth", "variable")))
        return self


def _order(pattern: Pattern) -> list[str]:
    """Visit pattern nodes so each next node is adjacent to one already
    placed (when possible): candidates then come from neighbours."""
    names = list(pattern.nodes)
    if not names:
        return []
    adj: dict[str, set[str]] = {n: set() for n in names}
    for a, b, _ in pattern.arcs:
        adj[a].add(b)
        adj[b].add(a)
    order = [max(names, key=lambda n: len(adj[n]))]
    while len(order) < len(names):
        placed = set(order)
        nxt = [n for n in names if n not in placed and adj[n] & placed]
        if not nxt:
            nxt = [n for n in names if n not in placed]
        order.append(max(nxt, key=lambda n: len(adj[n] & placed)))
    return order


def match(aag: AAG, pattern: Pattern, candidates: Optional[Iterable[int]] = None,
          limit: int = 10000) -> list[dict[str, int]]:
    pool = set(candidates) if candidates is not None else set(aag.faces)
    pool -= set(aag.hidden)
    domains = {n: [f for f in sorted(pool) if pred(aag, f)] for n, pred in pattern.nodes.items()}
    order = _order(pattern)
    arcs_by_node: dict[str, list[tuple[str, str, tuple[str, ...]]]] = {n: [] for n in pattern.nodes}
    for a, b, conv in pattern.arcs:
        arcs_by_node[a].append((a, b, conv))
        arcs_by_node[b].append((a, b, conv))
    results: list[dict[str, int]] = []

    def consistent(name: str, face: int, mapping: dict[str, int]) -> bool:
        for a, b, conv in arcs_by_node[name]:
            other = b if a == name else a
            if other not in mapping:
                continue
            found = aag.convexity_between(face, mapping[other])
            if not found & set(conv):
                return False
        for a, b in pattern.not_adjacent:
            if name in (a, b):
                other = b if a == name else a
                if other in mapping and aag.arcs_between(face, mapping[other]):
                    return False
        return True

    def candidates_for(name: str, mapping: dict[str, int]) -> list[int]:
        for a, b, _ in arcs_by_node[name]:
            other = b if a == name else a
            if other in mapping:
                nbrs = aag.neighbors(mapping[other])
                return [f for f in domains[name] if f in nbrs]
        return domains[name]

    def extend(depth: int, mapping: dict[str, int], used: set[int]) -> None:
        if len(results) >= limit:
            return
        if depth == len(order):
            if pattern.check is None or pattern.check(aag, mapping):
                results.append(dict(mapping))
            return
        name = order[depth]
        for f in candidates_for(name, mapping):
            if f in used or not consistent(name, f, mapping):
                continue
            mapping[name] = f
            used.add(f)
            extend(depth + 1, mapping, used)
            del mapping[name]
            used.discard(f)

    extend(0, {}, set())
    return results
