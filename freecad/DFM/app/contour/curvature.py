# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Per-triangle surface curvature, worked out while gmsh still has the CAD.

The radius does not come from the mesh's shape, which is too coarse for a
small fillet. Each mesh node knows its place (u, v) on its CAD surface, so:

* The SIZE of the curvature is gmsh's exact principal curvature at the
  triangle's corners (getPrincipalCurvatures). Exact on any surface type.
* The SIGN (convex or concave) comes from a fit to the exact CAD normals at
  the three corners (Rusinkiewicz's per-face second fundamental form).
  gmsh's own signs follow each surface's parametrisation, so two identical
  convex fillets can come back with opposite signs; the fit can't.

The fit is exact on cylinders and spheres (the normal there changes linearly
with position), and it stands in for the size too when gmsh can't evaluate
a surface. Results are signed against the triangle's own winding normal,
positive when the surface bends away from it (convex on an outward normal).
"""

import math

try:
    import numpy as np
except Exception:  # pragma: no cover - numpy ships with FreeCAD
    np = None


def _fit_curvatures(P, N):
    """Principal curvatures (k1, k2) of each triangle from its corner normals.

    P, N: arrays (n, 3, 3) of corner points and unit normals. Returns two (n,)
    arrays, k1 >= k2, signed so that positive bends away from N. Degenerate
    triangles get NaN.
    """
    e = np.stack([P[:, 1] - P[:, 0], P[:, 2] - P[:, 1], P[:, 0] - P[:, 2]], axis=1)
    dn = np.stack([N[:, 1] - N[:, 0], N[:, 2] - N[:, 1], N[:, 0] - N[:, 2]], axis=1)

    fn = np.cross(e[:, 0], P[:, 2] - P[:, 0])
    fl = np.linalg.norm(fn, axis=1)
    ul = np.linalg.norm(e[:, 0], axis=1)
    ok = (fl > 1e-18) & (ul > 1e-12)
    fl[~ok] = 1.0
    ul[~ok] = 1.0
    fn = fn / fl[:, None]
    u = e[:, 0] / ul[:, None]
    v = np.cross(fn, u)

    eu = np.einsum("nij,nj->ni", e, u)
    ev = np.einsum("nij,nj->ni", e, v)
    du = np.einsum("nij,nj->ni", dn, u)
    dv = np.einsum("nij,nj->ni", dn, v)

    # Least squares for II = [[L, M], [M, N]]:  II (eu, ev) = (du, dv) per edge.
    a00 = (eu * eu).sum(1)
    a01 = (eu * ev).sum(1)
    a11 = (ev * ev + eu * eu).sum(1)
    a22 = (ev * ev).sum(1)
    b0 = (eu * du).sum(1)
    b1 = (ev * du + eu * dv).sum(1)
    b2 = (ev * dv).sum(1)

    A = np.zeros((len(P), 3, 3))
    A[:, 0, 0] = a00
    A[:, 0, 1] = A[:, 1, 0] = a01
    A[:, 1, 1] = a11
    A[:, 1, 2] = A[:, 2, 1] = a01
    A[:, 2, 2] = a22
    b = np.stack([b0, b1, b2], axis=1)

    det = np.linalg.det(A)
    scale = np.maximum(a00 + a22, 1e-30) ** 3
    ok &= np.abs(det) > 1e-12 * scale
    x = np.full((len(P), 3), np.nan)
    if ok.any():
        x[ok] = np.linalg.solve(A[ok], b[ok][..., None])[..., 0]
    L, M, Nn = x[:, 0], x[:, 1], x[:, 2]
    half_tr = 0.5 * (L + Nn)
    disc = np.sqrt(np.maximum(half_tr * half_tr - (L * Nn - M * M), 0.0))
    return half_tr + disc, half_tr - disc


def _facet_vertex_normals(P_all, tris):
    """Fallback node normals for a surface gmsh gave no normals for: the area-
    weighted mean of the surrounding facet normals (on this surface only)."""
    acc = {}
    for a, b, c in tris:
        pa, pb, pc = P_all[a], P_all[b], P_all[c]
        n = np.cross(np.subtract(pb, pa), np.subtract(pc, pa))
        for k in (a, b, c):
            acc[k] = acc.get(k, 0.0) + n
    out = {}
    for k, n in acc.items():
        length = float(np.linalg.norm(n))
        if length > 1e-18:
            out[k] = tuple(n / length)
    return out


def surface_curvatures(gmsh, surf_tag, s_tags, s_params, node_normal, tri_tags, coords_of):
    """Signed tightest curvature for each triangle of one CAD surface.

    gmsh:        the live gmsh module (still initialised).
    s_tags:      node tags of the surface, in the order of s_params.
    s_params:    flat (u, v) list from getNodes(..., returnParametricCoord=True).
    node_normal: {node tag: CAD unit normal} for this surface (may be empty).
    tri_tags:    [(t0, t1, t2)] node tags of the surface's triangles.
    coords_of:   node tag -> (x, y, z).

    Returns a list (one per triangle) of the signed curvature with the
    largest magnitude (1/mm), positive = convex against the triangle's
    winding normal, or None where it couldn't be worked out.
    """
    if np is None or not tri_tags:
        return [None] * len(tri_tags)

    # Exact magnitudes at the nodes.
    exact = {}
    try:
        kmax, kmin, _dmax, _dmin = gmsh.model.getPrincipalCurvatures(surf_tag, s_params)
        if len(kmax) == len(s_tags):
            for i, tag in enumerate(s_tags):
                k = max(abs(float(kmax[i])), abs(float(kmin[i])))
                if math.isfinite(k):
                    exact[int(tag)] = k
    except Exception:
        exact = {}

    normals = node_normal
    if not normals or any(t not in normals for tri in tri_tags for t in tri):
        local = _facet_vertex_normals({t: coords_of(t) for tri in tri_tags for t in tri}, tri_tags)
        normals = {**local, **(node_normal or {})}

    P = np.array([[coords_of(t) for t in tri] for tri in tri_tags], dtype=float)
    N = np.array([[normals.get(t, (0.0, 0.0, 0.0)) for t in tri] for tri in tri_tags], dtype=float)

    # The fit is signed against N; make N agree with each triangle's winding
    # so the result is signed against the winding normal instead.
    wind = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    flip = np.einsum("ni,ni->n", N.mean(axis=1), wind) < 0.0
    N[flip] *= -1.0

    k1, k2 = _fit_curvatures(P, N)
    dominant = np.where(np.abs(k1) >= np.abs(k2), k1, k2)

    out = []
    for i, tri in enumerate(tri_tags):
        fit = float(dominant[i])
        mags = [exact[t] for t in tri if t in exact]
        if len(mags) == 3:
            mag = sorted(mags)[1]  # median: one bad corner (a pole) can't skew it
        elif not math.isnan(fit):
            mag = abs(fit)
        else:
            out.append(None)
            continue
        if math.isnan(fit):
            sign = 1.0
        else:
            sign = -1.0 if fit < 0.0 else 1.0
        out.append(sign * mag)
    return out
