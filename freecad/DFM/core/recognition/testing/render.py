# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Pictures of recognition results, without FreeCAD or a GPU.

Each face is coloured by the kind of feature that owns it (grey: no
feature) and drawn from several directions with a small z-buffer
rasteriser (numpy), with B-rep edges on top. Feature numbers are written
where the feature is visible, and a legend lists every feature with its
main parameters. Used to judge results by eye, in the bench and from the
command line::

    python -m freecad.DFM.core.recognition.testing.render part.step out.png

Needs numpy and matplotlib (both ship with FreeCAD).
"""

from __future__ import annotations

import math
import sys
from typing import Optional

import numpy as np

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location

from ...utils.geometry import EdgeIndex, FaceIndex
from ..model import FeatureKind

KIND_COLORS = {
    FeatureKind.HOLE: "#1F77B4",
    FeatureKind.BOSS: "#FF7F0E",
    FeatureKind.RIB: "#2CA02C",
    FeatureKind.SLOT: "#9467BD",
    FeatureKind.STEP: "#8C564B",
    FeatureKind.POCKET: "#E377C2",
    FeatureKind.CUTOUT: "#BCBD22",
    FeatureKind.FILLET: "#17BECF",
    FeatureKind.CHAMFER: "#F5C400",
    FeatureKind.DRAFT: "#AEC7E8",
    FeatureKind.UNDERCUT: "#D62728",
    FeatureKind.VOID: "#393B79",
    FeatureKind.SNAP_FIT: "#E6550D",
    FeatureKind.LIVING_HINGE: "#31A354",
    FeatureKind.VENT: "#6BAED6",
    FeatureKind.LIP: "#756BB1",
    FeatureKind.TEXT: "#636363",
    FeatureKind.OVERHANG: "#FD8D3C",
    FeatureKind.BRIDGE: "#9E9AC8",
    FeatureKind.DEPRESSION: "#C49C94",
    FeatureKind.PROTRUSION: "#C5B0D5",
}
REST = "#D9D9D9"

VIEWS = {
    "iso": (1.0, -1.0, 0.8),
    "iso_back": (-1.0, 1.0, 0.8),
    "top": (0.0, 0.0, 1.0),
    "bottom": (0.0, 0.0, -1.0),
    "front": (0.0, -1.0, 0.0),
    "right": (1.0, 0.0, 0.0),
}


def _rgb(hex_color: str) -> np.ndarray:
    h = hex_color.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)])


class Mesh:
    """Triangles (with their face numbers) and edge polylines of a shape."""

    def __init__(self, shape, deflection: Optional[float] = None):
        faces = FaceIndex(shape)
        edges = EdgeIndex(shape)
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        box = Bnd_Box()
        BRepBndLib.Add_s(shape, box)
        x0, y0, z0, x1, y1, z1 = box.Get()
        self.bounds = np.array([[x0, y0, z0], [x1, y1, z1]])
        diag = float(np.linalg.norm(self.bounds[1] - self.bounds[0])) or 1.0
        BRepMesh_IncrementalMesh(shape, deflection or diag / 600.0, False, 0.25, True)
        tris, ids = [], []
        for i in range(1, len(faces) + 1):
            face = faces.face_at(i)
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(face, loc)
            if tri is None:
                continue
            trsf = loc.Transformation()
            nodes = []
            for k in range(1, tri.NbNodes() + 1):
                p = tri.Node(k).Transformed(trsf)
                nodes.append((p.X(), p.Y(), p.Z()))
            nodes = np.array(nodes)
            rev = face.Orientation() == TopAbs_REVERSED
            for k in range(1, tri.NbTriangles() + 1):
                a, b, c = tri.Triangle(k).Get()
                if rev:
                    b, c = c, b
                tris.append((nodes[a - 1], nodes[b - 1], nodes[c - 1]))
                ids.append(i)
        self.tris = np.array(tris) if tris else np.zeros((0, 3, 3))
        self.face_ids = np.array(ids, dtype=int)
        self.face_count = len(faces)
        self.edges = []
        for i in range(1, len(edges) + 1):
            e = edges.edge_at(i)
            if BRep_Tool.Degenerated_s(e):
                continue
            c = BRepAdaptor_Curve(e)
            a, b = c.FirstParameter(), c.LastParameter()
            if not (math.isfinite(a) and math.isfinite(b)):
                continue
            n = 24 if c.GetType() != 0 else 2  # lines need two points
            pts = [c.Value(a + (b - a) * k / (n - 1)) for k in range(n)]
            self.edges.append(np.array([(p.X(), p.Y(), p.Z()) for p in pts]))


def _view_matrix(direction):
    d = np.array(direction, float)
    d /= np.linalg.norm(d)
    up = np.array([0.0, 0.0, 1.0])
    if abs(d @ up) > 0.99:
        up = np.array([0.0, 1.0, 0.0])
    right = np.cross(up, d)
    right /= np.linalg.norm(right)
    true_up = np.cross(d, right)
    return np.stack([right, true_up, d])   # rows: screen x, screen y, depth (towards viewer)


def render_view(mesh: Mesh, face_rgb: np.ndarray, direction, size: int = 520, edges: bool = True,
                focus: Optional[np.ndarray] = None):
    """(image HxWx3 float, projector, zbuffer, face buffer). *focus*: 2x3
    bounds to frame instead of the whole part (a zoom)."""
    R = _view_matrix(direction)
    bounds = focus if focus is not None else mesh.bounds
    centre = bounds.mean(axis=0)
    radius = 0.5 * float(np.linalg.norm(bounds[1] - bounds[0])) or 1.0
    scale = (size * 0.46) / radius

    def project(p):
        q = (np.asarray(p) - centre) @ R.T
        x = size / 2 + q[..., 0] * scale
        y = size / 2 - q[..., 1] * scale
        return x, y, q[..., 2]

    img = np.ones((size, size, 3))
    zbuf = np.full((size, size), -np.inf)
    fbuf = np.zeros((size, size), dtype=int)
    light = np.array([0.35, 0.25, 0.9]) @ np.eye(3)
    light = light / np.linalg.norm(light)
    tris = mesh.tris
    if len(tris):
        X, Y, Z = project(tris)               # each (n, 3)
        n_world = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        n_len = np.linalg.norm(n_world, axis=1)
        n_len[n_len == 0] = 1
        n_view = (n_world / n_len[:, None]) @ R.T
        shade = 0.35 + 0.65 * np.abs(n_view @ light)
        for t in range(len(tris)):
            xs, ys, zs = X[t], Y[t], Z[t]
            x0, x1 = int(max(0, np.floor(xs.min()))), int(min(size - 1, np.ceil(xs.max())))
            y0, y1 = int(max(0, np.floor(ys.min()))), int(min(size - 1, np.ceil(ys.max())))
            if x1 < x0 or y1 < y0:
                continue
            px, py = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            d = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
            if abs(d) < 1e-12:
                continue
            w0 = ((ys[1] - ys[2]) * (px - xs[2]) + (xs[2] - xs[1]) * (py - ys[2])) / d
            w1 = ((ys[2] - ys[0]) * (px - xs[2]) + (xs[0] - xs[2]) * (py - ys[2])) / d
            w2 = 1 - w0 - w1
            inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not inside.any():
                continue
            z = w0 * zs[0] + w1 * zs[1] + w2 * zs[2]
            sub = zbuf[y0:y1 + 1, x0:x1 + 1]
            win = inside & (z > sub)
            if not win.any():
                continue
            sub[win] = z[win]
            img[y0:y1 + 1, x0:x1 + 1][win] = face_rgb[mesh.face_ids[t]] * shade[t]
            fbuf[y0:y1 + 1, x0:x1 + 1][win] = mesh.face_ids[t]
    if edges:
        tol = 2.0 * (1.0 / scale) * 1.5 + 1e-3 * radius
        for poly in mesh.edges:
            # densify, then draw the points that are in front
            seg = []
            for a, b in zip(poly[:-1], poly[1:]):
                L = max(2, int(np.linalg.norm(b - a) * scale))
                tt = np.linspace(0, 1, L)[:, None]
                seg.append(a + (b - a) * tt)
            pts = np.concatenate(seg)
            xs, ys, zs = project(pts)
            xi, yi = xs.astype(int), ys.astype(int)
            ok = (xi >= 0) & (xi < size) & (yi >= 0) & (yi < size)
            xi, yi, zs = xi[ok], yi[ok], zs[ok]
            vis = zs >= zbuf[yi, xi] - tol
            img[yi[vis], xi[vis]] = (0.12, 0.12, 0.14)
    return img, project, zbuf, fbuf


def face_colors(face_count: int, features, kind_colors=KIND_COLORS) -> np.ndarray:
    rgb = np.tile(_rgb(REST), (face_count + 1, 1))
    for f in features:
        c = _rgb(kind_colors.get(f.kind, "#999999"))
        for face in f.faces:
            if 1 <= face <= face_count:
                rgb[face] = c
    return rgb


def _label(f) -> str:
    p = f.params
    bits = [f"{f.id} {f.kind.value}"]
    if f.subtype:
        bits[0] += f" ({f.subtype})"
    for key, fmt in (("diameter", "Ø{:.3g}"), ("outer_diameter", "Ø{:.3g}"), ("thickness", "t{:.3g}"),
                     ("width", "w{:.3g}"), ("depth", "d{:.3g}"), ("height", "h{:.3g}"), ("radius", "R{:.3g}")):
        if key in p and isinstance(p[key], (int, float)) and not isinstance(p[key], bool):
            bits.append(fmt.format(p[key]))
    if f.confidence < 0.6:
        bits.append(f"c{f.confidence:.2f}")
    return " ".join(bits)


def render(shape, result, path: str, views=("iso", "iso_back", "top", "bottom"), title: str = "",
           size: int = 640, mesh: Optional[Mesh] = None, truth_rgb: Optional[np.ndarray] = None,
           truth_legend: Optional[dict] = None, focus_faces=None) -> str:
    """Write a PNG of *result* on *shape*: the views in a 2-column grid and a
    legend. With *truth_rgb* (face colours of a ground truth) each view is
    shown twice, recognised beside truth."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    mesh = mesh or Mesh(shape)
    rgb = face_colors(mesh.face_count, result.features)
    focus = None
    if focus_faces:
        sel = mesh.tris[np.isin(mesh.face_ids, list(focus_faces))].reshape(-1, 3)
        if len(sel):
            lo, hi = sel.min(axis=0), sel.max(axis=0)
            pad = 0.6 * (hi - lo).max() + 1e-6
            focus = np.array([lo - pad, hi + pad])
    panels = []
    for v in views:
        panels.append((v, rgb, True))
        if truth_rgb is not None:
            panels.append(("truth " + v, truth_rgb, False))
    cols = 2
    rows = (len(panels) + cols - 1) // cols
    fig = plt.figure(figsize=(cols * 5.2 + 4.6, rows * 5.2), dpi=100)
    gs = fig.add_gridspec(rows, cols + 1, width_ratios=[1] * cols + [0.9])
    for i, (name, colours, labels) in enumerate(panels):
        ax = fig.add_subplot(gs[i // cols, i % cols])
        v = name.split()[-1]
        img, project, zbuf, fbuf = render_view(mesh, colours, VIEWS[v], size, focus=focus)
        ax.imshow(img, interpolation="bilinear")
        ax.set_axis_off()
        ax.set_title(name, fontsize=10)
        if not labels:
            continue
        for f in result.features:
            mask = np.isin(fbuf, list(f.faces))
            if mask.sum() < 15:
                continue
            ys, xs = np.nonzero(mask)
            # a pixel well inside the visible part
            cx, cy = xs.mean(), ys.mean()
            k = int(np.argmin((xs - cx) ** 2 + (ys - cy) ** 2))
            ax.text(xs[k], ys[k], str(f.id), fontsize=7, ha="center", va="center",
                    bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="black", lw=0.4, alpha=0.8))
    ax = fig.add_subplot(gs[:, -1])
    ax.set_axis_off()
    kinds = []
    for f in result.features:
        if f.kind not in kinds:
            kinds.append(f.kind)
    handles = [Patch(color=KIND_COLORS.get(k, "#999"), label=k.value) for k in kinds] + [Patch(color=REST, label="no feature")]
    if truth_legend:
        handles += [Patch(color=c, label="truth: " + n) for n, c in truth_legend.items()]
    ax.legend(handles=handles, loc="upper left", fontsize=8, frameon=False, ncol=2)
    lines = [_label(f) for f in result.features]
    if len(lines) > 90:
        lines = lines[:90] + [f"... {len(result.features) - 90} more"]
    ax.text(0.0, 0.84, "\n".join(lines), fontsize=6.5, va="top", family="monospace", transform=ax.transAxes)
    fig.suptitle(title or f"{len(result.features)} features, {result.face_count} faces", fontsize=11)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def main(argv=None) -> int:  # pragma: no cover - a tool
    from ..api import recognize
    from . import shapes as B

    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2:
        print("usage: render.py PART.step|PART.brep OUT.png")
        return 2
    src, out = argv[0], argv[1]
    shape = B.read_step(src) if src.lower().endswith((".step", ".stp")) else B.read_brep(src)
    result = recognize(shape)
    render(shape, result, out, title=src.split("/")[-1])
    print(out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
