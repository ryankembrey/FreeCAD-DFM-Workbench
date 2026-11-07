# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""What the fill means for the part: weld and meld lines, air traps,
confidence of fill.

All of it is read from the fill time (when the melt reached each node):

* weld lines: mesh edges where the melt arrived from both sides (fronts
  meeting); a weld is kept if the fronts met at a real angle somewhere along
  it and it is at least a wall thickness long. The fill time is first
  smoothed over about half a wall, as the solver's steps leave a small
  element-to-element jitter that would otherwise show as short false seams;
* meld lines: from the end of a weld, the flow followed on downstream: the
  seam between the two streams (behind a hole, say);
* air traps: places the melt reached last locally, closing in from opposing
  sides, and clearly later than everything a couple of walls round them;
* confidence of fill: the pressure the gate needed when the melt got there,
  against the machine's limit.
"""

import numpy as np
from scipy.sparse.csgraph import connected_components
import scipy.sparse as sp


def smoothed(mesh, fill_time, passes=3, weight=0.5):
    """Fill time with the step-to-step jitter of the solver smoothed out
    (averaged with the neighbours a few times), for finding seams. Real
    seams span many elements and survive; one-element ridges don't."""
    e = mesh.edges()
    pairs = getattr(mesh, "pairs", np.zeros((0, 2), np.int64))
    links = np.vstack([e, pairs]) if len(pairs) else e
    n = len(mesh.nodes)
    ok = np.isfinite(fill_time)
    f = np.where(ok, fill_time, 0.0)
    # not across a wall's rim (it fills late, from both skins): averaging it
    # in would make false ridges along every edge
    cap = getattr(mesh, "tri_cap", np.zeros(len(mesh.tris), bool))
    rim = np.zeros(n, bool)
    rim[mesh.tris[cap].ravel()] = True
    both = ok[links[:, 0]] & ok[links[:, 1]] & ~rim[links[:, 0]] & ~rim[links[:, 1]]
    a, b = links[both, 0], links[both, 1]
    A = sp.coo_matrix((np.ones(2 * len(a)), (np.concatenate([a, b]), np.concatenate([b, a]))),
                      shape=(n, n)).tocsr()
    deg = np.asarray(A.sum(axis=1)).ravel()
    for _ in range(passes):
        avg = np.where(deg > 0, (A @ f) / np.maximum(deg, 1), f)
        f = np.where(ok, (1 - weight) * f + weight * avg, f)
    return np.where(ok, f, np.inf)


def _group_edges(mesh, a, b, gap=1.6):
    """Label edges into lines: edges whose midpoints are within *gap*
    element sizes are on the same line (a seam crossing triangles zigzags,
    so its edges needn't share nodes)."""
    from scipy.spatial import cKDTree

    mid = 0.5 * (mesh.nodes[a] + mesh.nodes[b])
    size = np.sqrt(2.3 * np.median(mesh.tri_area))
    close = cKDTree(mid).query_pairs(gap * size, output_type="ndarray")
    k = len(a)
    if len(close):
        g = sp.coo_matrix((np.ones(len(close)), (close[:, 0], close[:, 1])), shape=(k, k))
    else:
        g = sp.coo_matrix((k, k))
    return connected_components(g, directed=False)[1]


def flow_directions(mesh, fill_time):
    """Direction the melt moved through each triangle (unit, in its plane:
    the way the fill time grows), and how fast it grows there (s/mm; 0 where
    unknown)."""
    x = mesh.nodes
    t = mesh.tris
    n = mesh.tri_normal
    area2 = 2.0 * mesh.tri_area
    grads = [np.cross(n, x[t[:, 2]] - x[t[:, 1]]), np.cross(n, x[t[:, 0]] - x[t[:, 2]]),
             np.cross(n, x[t[:, 1]] - x[t[:, 0]])]
    ft = np.where(np.isfinite(fill_time), fill_time, np.nan)[t]
    g = sum(ft[:, k, None] * grads[k] for k in range(3)) / area2[:, None]
    mag = np.linalg.norm(g, axis=1)
    return g / np.maximum(mag, 1e-300)[:, None], mag


def weld_lines(mesh, fill_time, min_angle=20.0, max_fold=50.0, min_edges=3, arrive=0.08):
    """Mesh edges where two melt fronts met.

    Returns a list of (node a, node b, meeting angle in degrees): the angle
    between the two flow directions (180 = head on, the weakest kind).
    """
    v, mag = flow_directions(mesh, fill_time)
    t = mesh.tris
    # interior edges and the two triangles on them
    e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    opp = np.concatenate([t[:, 2], t[:, 0], t[:, 1]])
    tri = np.tile(np.arange(len(t)), 3)
    key = np.sort(e, axis=1)
    order = np.lexsort((key[:, 1], key[:, 0]))
    key, opp, tri = key[order], opp[order], tri[order]
    same = np.all(key[1:] == key[:-1], axis=1)
    i1 = np.nonzero(same)[0]
    i2 = i1 + 1
    a, b = key[i1, 0], key[i1, 1]
    t1, t2 = tri[i1], tri[i2]
    o1, o2 = opp[i1], opp[i2]
    x = mesh.nodes
    edge = x[b] - x[a]
    edge /= np.linalg.norm(edge, axis=1)[:, None]

    def into_other(tri_id, opp_node):
        # in this triangle's plane, perpendicular to the edge, away from its own corner
        m = np.cross(mesh.tri_normal[tri_id], edge)
        side = np.einsum("ij,ij->i", m, x[opp_node] - x[a])
        return m * -np.sign(side)[:, None]

    m1 = into_other(t1, o1)
    m2 = into_other(t2, o2)
    flat = np.einsum("ij,ij->i", mesh.tri_normal[t1], mesh.tri_normal[t2]) > np.cos(np.radians(max_fold))
    arrive1 = np.einsum("ij,ij->i", v[t1], m1) > arrive
    arrive2 = np.einsum("ij,ij->i", v[t2], m2) > arrive
    moving = (mag[t1] > 0) & (mag[t2] > 0)
    cosang = np.clip(np.einsum("ij,ij->i", v[t1], v[t2]), -1, 1)
    angle = np.degrees(np.arccos(cosang))
    keep = flat & arrive1 & arrive2 & moving & (angle >= min_angle)
    keep &= np.isfinite(fill_time[a]) & np.isfinite(fill_time[b])
    cap = getattr(mesh, "tri_cap", None)
    if cap is not None:
        # not on a wall's rim, nor running along its edge (fronts don't meet
        # along an edge; the skins there just end)
        on_rim = np.zeros(len(mesh.nodes), bool)
        on_rim[mesh.tris[cap].ravel()] = True
        keep &= ~cap[t1] & ~cap[t2] & ~(on_rim[a] & on_rim[b])
    a, b, angle = a[keep], b[keep], angle[keep]
    if len(a) == 0:
        return []
    # drop lone fragments: a weld line runs over several edges
    label = _group_edges(mesh, a, b)
    size = np.bincount(label)
    long_enough = size[label] >= min_edges
    return [(int(p), int(q), float(ang))
            for p, q, ang in zip(a[long_enough], b[long_enough], angle[long_enough])]


def smoothing_passes(mesh):
    """Smoothing to apply before looking for seams: about half a wall
    thickness (jitter is a few elements across; seams are walls long)."""
    size = np.sqrt(2.3 * np.median(mesh.tri_area))
    wall = np.median(mesh.tri_thickness)
    return int(np.clip(round((0.6 * wall / size) ** 2), 0, 12))


def seams(mesh, fill_time, min_edges=4, min_meld_mm=None, meeting_angle=45.0,
          min_length_walls=1.0, smooth=True):
    """Weld lines (where fronts meet) and the meld lines they carry on as.

    Returns (welds, melds): welds as weld_lines(); melds as polylines. A weld
    too short to count on its own is kept when it starts a meld line. With
    *smooth*, the fill time's step jitter is smoothed out first, over about
    half a wall thickness, and a weld must be *min_length_walls* walls long.
    """
    if smooth:
        passes = smoothing_passes(mesh)
        if passes:
            fill_time = smoothed(mesh, fill_time, passes)
    wall = float(np.median(mesh.tri_thickness))
    candidates = weld_lines(mesh, fill_time, min_edges=2)
    if not candidates:
        return [], []
    size = np.sqrt(2.3 * np.median(mesh.tri_area))
    min_meld = min_meld_mm or 5.0 * size
    a = np.array([w[0] for w in candidates]); b = np.array([w[1] for w in candidates])
    comp = _group_edges(mesh, a, b)
    welds, melds = [], []
    for c in np.unique(comp):
        group = [candidates[i] for i in np.nonzero(comp == c)[0]]
        if max(w[2] for w in group) < meeting_angle:
            continue  # flows running alongside each other, not fronts meeting
        length = sum(np.linalg.norm(mesh.nodes[p] - mesh.nodes[q]) for p, q, _ in group)
        if length < min_length_walls * wall:
            continue  # too short to be a seam (a ripple in the fill)
        lines = meld_lines(mesh, fill_time, group)
        lines = [ln for ln in lines if np.linalg.norm(np.diff(ln, axis=0), axis=1).sum() >= min_meld]
        if len(group) >= min_edges or lines:
            welds.extend(group)
            melds.extend(lines)
    return welds, melds


def air_traps(mesh, fill_time, merge_mm=None, reach_mm=None, max_resultant=0.55,
              min_prominence=0.4):
    """Places where melt fronts close in from different sides, trapping air:
    [(node, time)], one per place, latest first.

    A node every neighbour of which fills before it is a candidate; it is a
    trap when the melt around it arrives from opposing directions (at the end
    of a single flow, e.g. a corner far from the gate, the flow all comes one
    way and the air escapes along the parting line). See last_to_fill() for
    the last place overall.
    """
    from scipy.spatial import cKDTree

    e = mesh.edges()
    pairs = getattr(mesh, "pairs", np.zeros((0, 2), np.int64))
    links = np.vstack([e, pairs]) if len(pairs) else e
    ft = np.where(np.isfinite(fill_time), fill_time, -np.inf)
    n = len(mesh.nodes)
    later = np.zeros(n, bool)  # some neighbour fills later
    a, b = links[:, 0], links[:, 1]
    np.logical_or.at(later, a, ft[b] > ft[a])
    np.logical_or.at(later, b, ft[a] > ft[b])
    peaks = np.nonzero(~later & np.isfinite(fill_time))[0]
    if len(peaks) == 0:
        return []
    size = np.sqrt(2.3 * np.median(mesh.tri_area))
    reach = reach_mm or 4.0 * size
    v, mag = flow_directions(mesh, fill_time)
    cent = mesh.nodes[mesh.tris].mean(axis=1)
    ctree = cKDTree(cent)
    cap = getattr(mesh, "tri_cap", np.zeros(len(mesh.tris), bool))
    incident = [[] for _ in range(n)]
    for ti, tri in enumerate(mesh.tris):
        for nd in tri:
            incident[nd].append(ti)
    traps = []
    for c in peaks:
        near = np.array(ctree.query_ball_point(mesh.nodes[c], reach), dtype=np.int64)
        if len(near) == 0:
            continue
        near = near[~cap[near] & (mag[near] > 0)]
        # Judge each flat patch at the node on its own: round a corner, the
        # flow on the two sides only looks like it converges.
        refs = [mesh.tri_normal[t] for t in incident[c] if not cap[t]]
        found = False
        for normal in refs:
            group = near[mesh.tri_normal[near] @ normal > 0.85]
            if len(group) < 4:
                continue
            d = mesh.nodes[c] - cent[group]
            flows = v[group] - np.outer(v[group] @ normal, normal)
            towards = np.einsum("ij,ij->i", flows, d) > 0
            flows = flows[towards]
            if len(flows) < 4:
                continue
            flows /= np.maximum(np.linalg.norm(flows, axis=1)[:, None], 1e-300)
            if np.linalg.norm(flows.mean(axis=0)) <= max_resultant:
                found = True
                break
        if found:
            traps.append(c)
    # Prominence: everything in a ring about two walls out filled clearly
    # earlier. Where the melt only flows past (round a corner, along an
    # edge), half that ring is downstream and fills later.
    wall = float(np.median(mesh.tri_thickness))
    ntree = cKDTree(mesh.nodes)
    _v, gmag = flow_directions(mesh, fill_time)
    kept = []
    for c in traps:
        outer = ntree.query_ball_point(mesh.nodes[c], max(2.5 * wall, 3 * size))
        inner = set(ntree.query_ball_point(mesh.nodes[c], max(1.5 * wall, 2 * size)))
        ring = np.array([i for i in outer if i not in inner], dtype=np.int64)
        ring = ring[np.isfinite(fill_time[ring])] if len(ring) else ring
        if len(ring) < 4:
            continue
        near_t = np.array(ctree.query_ball_point(mesh.nodes[c], max(2.5 * wall, 3 * size)), dtype=np.int64)
        slope = np.median(gmag[near_t]) if len(near_t) else 0.0  # s per mm
        dist = np.median(np.linalg.norm(mesh.nodes[ring] - mesh.nodes[c], axis=1))
        if fill_time[c] - np.median(fill_time[ring]) >= min_prominence * slope * dist:
            kept.append(c)
    traps = kept
    if not traps:
        return []
    traps = np.array(traps)
    merge = merge_mm or 3.0 * size
    tree = cKDTree(mesh.nodes[traps])
    close = tree.query_pairs(merge, output_type="ndarray")
    if len(close):
        g = sp.coo_matrix((np.ones(len(close)), (close[:, 0], close[:, 1])),
                          shape=(len(traps), len(traps)))
    else:
        g = sp.coo_matrix((len(traps), len(traps)))
    _count, label = connected_components(g, directed=False)
    out = []
    for k in np.unique(label):
        members = traps[label == k]
        best = members[np.argmax(fill_time[members])]
        out.append((int(best), float(fill_time[best])))
    return sorted(out, key=lambda tr: -tr[1])


def last_to_fill(fill_time):
    """The node the melt reaches last (where a short shot would show first)."""
    ft = np.where(np.isfinite(fill_time), fill_time, -np.inf)
    return int(np.argmax(ft))


def confidence(result, max_pressure):
    """Confidence of fill per node: 3 high, 2 medium, 1 low, 0 won't fill.

    From the pressure the gate needs by the time the melt gets there,
    against the machine's limit."""
    share = result.pressure_at_fill / max_pressure
    level = np.where(share < 0.6, 3, np.where(share < 0.8, 2, np.where(share < 1.0, 1, 0)))
    level = np.where(np.isfinite(result.fill_time), level, 0)
    return level.astype(np.int8)


# -- Meld lines: the seam carried downstream from where fronts met -------------------


def _tri_neighbours(mesh):
    """neighbour[t, k]: the triangle across edge k (nodes k, k+1) of t, or -1."""
    t = mesh.tris
    e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    key = np.sort(e, axis=1)
    tri = np.tile(np.arange(len(t)), 3)
    local = np.repeat(np.arange(3), len(t))
    order = np.lexsort((key[:, 1], key[:, 0]))
    key, tri, local = key[order], tri[order], local[order]
    nb = -np.ones((len(t), 3), np.int64)
    same = np.nonzero(np.all(key[1:] == key[:-1], axis=1))[0]
    nb[tri[same], local[same]] = tri[same + 1]
    nb[tri[same + 1], local[same + 1]] = tri[same]
    return nb


def meld_lines(mesh, fill_time, welds, max_fold=50.0, max_steps=4000):
    """From the downstream end of each weld line, follow the flow on to where
    the melt stops: the seam between the two streams (a meld line).
    Returns polylines [(N, 3) array of points in mm]."""
    if not welds:
        return []
    v, mag = flow_directions(mesh, fill_time)
    nb = _tri_neighbours(mesh)
    cap = getattr(mesh, "tri_cap", np.zeros(len(mesh.tris), bool))
    x = mesh.nodes
    ft = fill_time
    # weld lines as groups of edges; seed at each group's latest node
    a = np.array([w[0] for w in welds]); b = np.array([w[1] for w in welds])
    edge_label = _group_edges(mesh, a, b)
    node_tris = [[] for _ in range(len(x))]
    for ti, tri in enumerate(mesh.tris):
        for nd in tri:
            node_tris[nd].append(ti)
    cos_fold = np.cos(np.radians(max_fold))
    lines = []
    for comp in np.unique(edge_label):
        members = np.unique(np.concatenate([a[edge_label == comp], b[edge_label == comp]]))
        seed = members[np.argmax(ft[members])]
        # start in the seed's triangle that the flow leaves it through
        cands = [ti for ti in node_tris[seed] if not cap[ti] and mag[ti] > 0]
        if not cands:
            continue
        cent = x[mesh.tris[cands]].mean(axis=1)
        ahead = np.einsum("ij,ij->i", cent - x[seed], v[cands])
        tcur = cands[int(np.argmax(ahead))]
        if ahead.max() <= 0:
            continue
        p = x[seed] + 1e-3 * (x[mesh.tris[tcur]].mean(axis=0) - x[seed])
        pts = [x[seed].copy()]
        last_time = ft[seed]
        for _ in range(max_steps):
            d = v[tcur]
            tri = mesh.tris[tcur]
            best = None
            for kk in range(3):
                A, B = x[tri[kk]], x[tri[(kk + 1) % 3]]
                M = np.stack([d, A - B], axis=1)
                sol, *_ = np.linalg.lstsq(M, A - p, rcond=None)
                s, u = sol
                if s > 1e-9 and -1e-9 <= u <= 1 + 1e-9 and (best is None or s < best[0]):
                    best = (s, u, kk)
            if best is None:
                break
            s, u, kk = best
            q = p + s * d
            A, B = tri[kk], tri[(kk + 1) % 3]
            t_here = (1 - u) * ft[A] + u * ft[B]
            if not np.isfinite(t_here) or t_here < last_time - 1e-12:
                break  # the melt stopped (or the trace turned back)
            pts.append(q)
            last_time = t_here
            nxt = nb[tcur, kk]
            if nxt < 0 or cap[nxt] or mag[nxt] <= 0:
                break
            if mesh.tri_normal[tcur] @ mesh.tri_normal[nxt] < cos_fold:
                break
            # flow in the next triangle must carry on away from this edge
            if v[nxt] @ d < -0.2:
                break
            p, tcur = q + 1e-9 * v[nxt], nxt
        if len(pts) > 2:
            lines.append(np.array(pts))
    return lines
