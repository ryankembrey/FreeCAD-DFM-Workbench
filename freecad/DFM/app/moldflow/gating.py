# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Where to put the gate: a quick version of Moldflow's gate location analysis.

The best single gate is taken to be the one from which the hardest place to
reach is easiest to reach: every flow path is weighted by how hard the melt
finds it (length / thickness^2, as Hele-Shaw resistance goes), and the gate
minimises the largest such distance (it balances the flow lengths).
"""

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import dijkstra


def flow_graph(mesh):
    e = mesh.edges()
    length = np.linalg.norm(mesh.nodes[e[:, 0]] - mesh.nodes[e[:, 1]], axis=1)
    h = 0.5 * (mesh.thickness[e[:, 0]] + mesh.thickness[e[:, 1]])
    w = length / np.maximum(h, 1e-3) ** 2
    rows, cols, data = [e[:, 0]], [e[:, 1]], [w]
    pairs = getattr(mesh, "pairs", None)
    if pairs is not None and len(pairs):
        tiny = np.full(len(pairs), 1e-9)
        rows.append(pairs[:, 0]); cols.append(pairs[:, 1]); data.append(tiny)
    n = len(mesh.nodes)
    g = sp.coo_matrix((np.concatenate(data), (np.concatenate(rows), np.concatenate(cols))),
                      shape=(n, n)).tocsr()
    return g


def suggest_gate(mesh, candidates=160, allowed=None, seed=0):
    """Node index of a good single gate (and its worst flow distance)."""
    g = flow_graph(mesh)
    ok = ~mesh.rim_nodes()  # not on the rim of a wall
    if allowed is not None:
        ok &= allowed
    pool = np.nonzero(ok)[0]
    rng = np.random.default_rng(seed)
    first = rng.choice(pool, size=min(candidates, len(pool)), replace=False)
    best, best_cost = None, np.inf

    def worst(nodes):
        d = dijkstra(g, directed=False, indices=nodes)
        d = np.where(np.isfinite(d), d, np.nan)
        return np.nanmax(d, axis=1)

    costs = worst(first)
    k = int(np.nanargmin(costs))
    best, best_cost = first[k], costs[k]
    # refine around the best: its neighbourhood, a few rounds
    for _ in range(3):
        d = dijkstra(g, directed=False, indices=[best])[0]
        near = pool[np.argsort(d[pool])[:60]]
        c = worst(near)
        j = int(np.nanargmin(c))
        if c[j] < best_cost:
            best, best_cost = near[j], c[j]
        else:
            break
    return int(best), float(best_cost)
