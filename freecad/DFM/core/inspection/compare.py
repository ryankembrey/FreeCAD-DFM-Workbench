# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""What changed between two bodies, A (the reference) and B?

B is moved onto A (or left where it is), then A is subtracted from B and B
from A. B - A is material added in B; A - B is material removed from A.
Each connected piece is a region; regions that touch the same feature are
one change, so a hole made larger is one change, "Hole Ø8 → Ø10".

Results are in B's own place: what was removed is moved back with B, so
it shows where it was relative to B.
"""

from __future__ import annotations

import math
from typing import Callable, Optional, Sequence

from OCP.TopoDS import TopoDS_Shape

from . import geometry as G
from .models import ADDED, CHANGED, REMOVED, BodyProps, CompareResult, Difference
from .naming import FaceFeatures, feature_kind, feature_name, feature_size, fmt_dims, same_feature

PLACED, CENTRES, BEST_FIT = "placed", "centres", "best_fit"

Progress = Optional[Callable[[str, float], None]]


def body_props(shape: TopoDS_Shape) -> BodyProps:
    mp = G.mass_props(shape)
    return BodyProps(mp.volume, mp.area, len(G.faces(shape)), G.bounding_box(shape).size)


def align(a: TopoDS_Shape, b: TopoDS_Shape, mode: str, progress: Progress = None):
    """(3 x 4 matrix moving B onto A, RMS distance of the fit or None)."""
    identity = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
    if mode == CENTRES or mode == BEST_FIT:
        ca, cb = G.centre(a), G.centre(b)
        shift = G.vsub(ca, cb)
        matrix = [[1.0, 0.0, 0.0, shift[0]], [0.0, 1.0, 0.0, shift[1]], [0.0, 0.0, 1.0, shift[2]]]
        if mode == CENTRES:
            return matrix, None
        # From where B is and from the centres: changes pull the centre of
        # mass sideways, so for a revised part, B's own place is the better start.
        fits = [best_fit(a, b, start, progress) for start in (identity, matrix)]
        return min(fits, key=lambda f: f[1])
    return identity, None


def best_fit(a: TopoDS_Shape, b: TopoDS_Shape, start, progress: Progress = None,
             iterations: int = 40, samples: int = 2500, targets: int = 12000):
    """Point-to-point ICP: the rigid move of B that fits A best, from *start*."""
    import numpy as np

    rng = np.random.default_rng(1)
    pa = np.array(G.sample_points(a, grid=9))
    pb = np.array(G.sample_points(b, grid=9))
    if len(pa) > targets:
        pa = pa[rng.choice(len(pa), targets, replace=False)]
    if len(pb) > samples:
        pb = pb[rng.choice(len(pb), samples, replace=False)]
    m = np.array(start, dtype=float)
    rot, trans = m[:, :3], m[:, 3]
    rms = math.inf
    for it in range(iterations):
        if progress:
            progress("Fitting B onto A", it / iterations)
        moved = pb @ rot.T + trans
        nearest = _nearest(moved, pa)
        target = pa[nearest]
        # Leave out the worst points: features that changed shouldn't pull the fit.
        d = np.linalg.norm(moved - target, axis=1)
        if len(d) > 20:
            keep = d <= max(min(np.quantile(d, 0.8), 3.0 * np.median(d)), 1e-9)
        else:
            keep = np.ones(len(d), bool)
        src, dst = moved[keep], target[keep]
        cs, cd = src.mean(axis=0), dst.mean(axis=0)
        h = (src - cs).T @ (dst - cd)
        u, _s, vt = np.linalg.svd(h)
        r = vt.T @ u.T
        if np.linalg.det(r) < 0:
            vt[-1, :] *= -1
            r = vt.T @ u.T
        t = cd - r @ cs
        rot, trans = r @ rot, r @ trans + t
        new_rms = float(np.sqrt(np.mean(d[keep] ** 2))) if keep.any() else 0.0
        if abs(rms - new_rms) < 1e-6:
            rms = new_rms
            break
        rms = new_rms
    matrix = [list(rot[i]) + [float(trans[i])] for i in range(3)]
    return matrix, rms


def _nearest(points, cloud, chunk: int = 512):
    import numpy as np

    out = np.empty(len(points), dtype=int)
    sq = (cloud ** 2).sum(axis=1)
    for i in range(0, len(points), chunk):
        p = points[i:i + chunk]
        d = sq[None, :] - 2.0 * p @ cloud.T
        out[i:i + chunk] = np.argmin(d, axis=1)
    return out


def compare(a: TopoDS_Shape, b: TopoDS_Shape, tolerance: float = 0.01, alignment: str = PLACED,
            recognition_a=None, recognition_b=None, ignored: Sequence[str] = (),
            progress: Progress = None) -> CompareResult:
    """Compare B with A. Recognition results (optional) name the changes."""
    tol = max(float(tolerance), 1e-6)
    matrix, rms = align(a, b, alignment, progress)
    trsf = G.rigid(matrix)
    moved = G.transformed(b, trsf)  # B in A's frame
    back = trsf.Inverted()
    b_of_moved = {j: i for i, j in moved.face_map.items()}

    if progress:
        progress("Finding added material", 0.3)
    added = G.difference(moved.shape, a, tol, min_volume=tol ** 3)
    if progress:
        progress("Finding removed material", 0.55)
    removed = G.difference(a, moved.shape, tol, min_volume=tol ** 3)

    fa, fb = FaceFeatures(a, recognition_a), FaceFeatures(b, recognition_b)
    items = []
    for kind, regions, other in ((ADDED, added, a), (REMOVED, removed, moved.shape)):
        for i, r in enumerate(regions):
            if progress:
                progress("Measuring the changes", 0.6 + 0.3 * i / max(len(regions), 1))
            dev = G.max_distance(G.spread_points(r.shape), other, r.box.grown(10 * tol + 1e-3))
            if dev < tol:
                continue
            if kind == ADDED:
                faces_b = fb.key_faces(_remap(r.area_from(G.OWN), b_of_moved))
                faces_a = fa.key_faces(r.area_from(G.TOOL))
            else:
                faces_a = fa.key_faces(r.area_from(G.OWN))
                faces_b = fb.key_faces(_remap(r.area_from(G.TOOL), b_of_moved))
            items.append((kind, r, dev, faces_a, faces_b))

    groups = _group(items, fa, fb)
    diffs = [_describe(members, fa, fb, back) for members in groups]
    order = {ADDED: 0, REMOVED: 1, CHANGED: 2}
    diffs.sort(key=lambda d: (order[d.kind], -abs(d.volume)))
    wanted = set(ignored)
    for n, d in enumerate(diffs, 1):
        d.number = n
        d.ignored = d.key in wanted
    if progress:
        progress("Done", 1.0)
    return CompareResult(tol, alignment, matrix, rms, body_props(a), body_props(b), diffs)


def _remap(areas: dict[int, float], mapping: dict[int, int]) -> dict[int, float]:
    out: dict[int, float] = {}
    for j, a in areas.items():
        i = mapping.get(j)
        if i:
            out[i] = out.get(i, 0.0) + a
    return out


def _group(items, fa: FaceFeatures, fb: FaceFeatures) -> list[list]:
    parent = list(range(len(items)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    seen: dict = {}
    for i, (_k, _r, _d, faces_a, faces_b) in enumerate(items):
        tags = {("a", ("feature", fa.owner[f].id) if f in fa.owner else ("face", f)) for f in faces_a}
        tags |= {("b", ("feature", fb.owner[f].id) if f in fb.owner else ("face", f)) for f in faces_b}
        for tag in tags:
            if tag in seen:
                parent[find(i)] = find(seen[tag])
            else:
                seen[tag] = i
    groups: dict[int, list] = {}
    for i, item in enumerate(items):
        groups.setdefault(find(i), []).append(item)
    return list(groups.values())


def _describe(members, fa: FaceFeatures, fb: FaceFeatures, back) -> Difference:
    kinds = {m[0] for m in members}
    faces_a = sorted({f for m in members for f in m[3]})
    faces_b = sorted({f for m in members for f in m[4]})
    feat_a = fa.main_feature(faces_a)
    feat_b = fb.main_feature(faces_b)
    vol = sum(m[1].volume if m[0] == ADDED else -m[1].volume for m in members)
    dev = max(m[2] for m in members)
    # Shown in B's own place.
    solids = [G.transformed(m[1].shape, back).shape for m in members if m[0] == ADDED]
    ghosts = [G.transformed(m[1].shape, back).shape for m in members if m[0] == REMOVED]
    box = None
    for s in solids + ghosts:
        bb = G.bounding_box(s)
        box = bb if box is None else G.Box(tuple(min(box.lo[i], bb.lo[i]) for i in range(3)),
                                           tuple(max(box.hi[i], bb.hi[i]) for i in range(3)))

    if len(kinds) == 1 and not (feat_a is not None and feat_b is not None
                                and feature_kind(feat_a) == feature_kind(feat_b)):
        kind = kinds.pop()
        feature = feat_b if kind == ADDED else feat_a
        faces_for = fb if kind == ADDED else fa
        name = feature_name(feature, faces_for) if feature is not None else "Material"
        detail = fmt_dims(G.bounding_box(G.compound(m[1].shape for m in members)))
    else:
        kind = CHANGED
        if feat_a is not None and feat_b is not None and feature_kind(feat_a) == feature_kind(feat_b):
            na, nb = feature_name(feat_a, fa), feature_name(feat_b, fb)
            name = na if na == nb else f"{na} → {nb.split(' ', 1)[-1] if na.split(' ')[0] == nb.split(' ')[0] else nb}"
            sa, sb = feature_size(feat_a), feature_size(feat_b)
            if same_feature(feat_a, feat_b):
                ca, cb = fa.centre_of(feat_a.faces), fb.centre_of(feat_b.faces)
                moved_by = G.vlen(G.vsub(G.apply(back, ca), cb)) if ca and cb else 0.0
                detail = f"moved {moved_by:.2f} mm".replace(".00 ", " ") if moved_by > 1e-6 else "reshaped"
            elif sa is not None and sb is not None:
                detail = "made larger" if sb > sa else "made smaller"
            else:
                detail = "changed"
        else:
            feature = feat_b or feat_a
            name = feature_name(feature, fb if feature is feat_b else fa) if feature is not None else "Material"
            detail = "reshaped"
    # A badge point: on B's faces when there are any, else on the removed material.
    anchor = fb.point_on(faces_b) if faces_b else None
    if anchor is None:
        pool = solids or ghosts
        anchor = G.sample_points(pool[0], grid=3)[0] if pool else (0.0, 0.0, 0.0)
    c = box.centre if box else anchor
    key = f"{kind}:{name}:{c[0]:.1f},{c[1]:.1f},{c[2]:.1f}:{vol:.1f}"
    return Difference(0, kind, name, detail, vol, dev, faces=faces_b, solids=solids, ghosts=ghosts,
                      anchor=anchor, box=box, key=key)


def reference_in_b(a: TopoDS_Shape, result: CompareResult) -> TopoDS_Shape:
    """A, moved into B's place (the inverse of how B was aligned onto A)."""
    if not result.transform:
        return a
    return G.transformed(a, G.rigid(result.transform).Inverted()).shape
