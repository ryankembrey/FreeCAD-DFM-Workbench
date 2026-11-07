# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""One mold flow analysis, start to finish, and its results as plain data.

The task panel meshes the part (the contour tools' gmsh mesher), then calls
run() with the settings. The result holds a value per mesh node for each
result the panel shows, the seams and air traps, and a summary; to_field()
and from_field() turn it into lists for the saved analysis object.
"""

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from . import results as R
from .gating import suggest_gate
from .material import Material
from .mesh import FlowMesh
from .solver import FillSolver, nearest_nodes

# What the panel can show, in its order: id -> (label, unit).
RESULTS = {
    "fill_time": ("Fill time", "s"),
    "pressure": ("Pressure at V/P switchover", "MPa"),
    "confidence": ("Confidence of fill", ""),
    "frozen": ("Frozen layer at end of fill", "%"),
    "thickness": ("Wall thickness", "mm"),
    "shear": ("Shear rate at end of fill", "1/s"),
}

# Confidence of fill categories: code -> (label, rgb, tooltip)
CONFIDENCE = {
    3: ("High", (0.18, 0.62, 0.33), "Fills with under 60% of the machine's pressure"),
    2: ("Medium", (0.95, 0.77, 0.12), "Needs 60 to 80% of the machine's pressure"),
    1: ("Low", (0.89, 0.42, 0.12), "Needs 80 to 100% of the machine's pressure"),
    0: ("Won't fill", (0.82, 0.18, 0.18), "Needs more than the machine's pressure, or "
        "the melt never gets there (short shot)"),
}


@dataclass
class Settings:
    material: Material
    gates: list = field(default_factory=list)  # [(x, y, z) mm]; empty: suggest one
    injection_time: Optional[float] = None  # s; None: automatic
    max_pressure: float = 100e6  # Pa, the machine's limit
    vp_switch: float = 0.99  # share of the volume filled at the V/P switchover
    cooling: bool = True
    front_fraction: float = 0.1  # share of the melt front filled per solver step
    weld_angle: float = 45.0  # degrees; fronts meeting at a shallower angle leave no weld line


@dataclass
class MoldFlowResult:
    nodes: np.ndarray
    tris: np.ndarray
    fields: dict  # result id -> per-node values (nan: none)
    welds: list  # [(ax, ay, az, bx, by, bz, angle)]
    melds: list  # [[(x, y, z), ...]]
    traps: list  # [(x, y, z, time)]
    gates: list  # [((x, y, z), (nx, ny, nz))]: where, and the surface normal there
    last: tuple  # (x, y, z): the last place to fill
    gate_history: list  # [(t, MPa)]
    summary: dict

    @property
    def fill_end(self):
        ft = self.fields["fill_time"]
        ok = np.isfinite(ft)
        return float(ft[ok].max()) if ok.any() else 0.0


def auto_injection_time(volume_cm3):
    """A typical fill time for the size of the shot (s)."""
    return float(np.clip(0.4 + 0.12 * volume_cm3 ** 0.6, 0.4, 5.0))


Progress = Optional[Callable[[str, Optional[float]], None]]


def run(uniform_mesh, settings: Settings, progress: Progress = None,
        check_abort: Optional[Callable[[], bool]] = None) -> MoldFlowResult:
    """Simulate the fill of a part meshed by app.contour.meshing."""

    def say(text, share=None):
        if progress is not None:
            progress(text, share)

    say("Measuring wall thickness")
    mesh = FlowMesh.from_uniform_mesh(uniform_mesh).prepare()
    if check_abort is not None and check_abort():
        from .solver import Aborted

        raise Aborted()

    mat = settings.material
    solver = FillSolver(mesh, mat)
    volume_cm3 = float(solver.volume.sum() * 1e6)
    if settings.gates:
        gate_nodes = np.concatenate([nearest_nodes(mesh, g) for g in settings.gates])
        suggested = False
    else:
        say("Choosing a gate")
        gate_nodes = np.array([suggest_gate(mesh)[0]])
        suggested = True
    t_inj = settings.injection_time or auto_injection_time(volume_cm3)

    say("Filling", 0.0)
    res = solver.run(
        gate_nodes, injection_time=t_inj, vp_switch=settings.vp_switch,
        cooling=settings.cooling, front_fraction=settings.front_fraction,
        progress=lambda share, _t: say("Filling", share), check_abort=check_abort)

    say("Finding weld lines and air traps")
    welds, melds = R.seams(mesh, res.fill_time, meeting_angle=settings.weld_angle)
    passes = R.smoothing_passes(mesh)
    traps = R.air_traps(mesh, R.smoothed(mesh, res.fill_time, passes) if passes else res.fill_time)
    last = R.last_to_fill(res.fill_time)
    conf = R.confidence(res, settings.max_pressure)

    n = len(mesh.nodes)

    def per_node(tri_values):
        out = np.zeros(n)
        cnt = np.zeros(n)
        for k in range(3):
            np.add.at(out, mesh.tris[:, k], tri_values)
            np.add.at(cnt, mesh.tris[:, k], 1.0)
        return out / np.maximum(cnt, 1.0)

    ft = res.fill_time.copy()
    fields = {
        "fill_time": np.where(np.isfinite(ft), ft, np.nan),
        "pressure": res.pressure_end / 1e6,
        "confidence": conf.astype(float),
        "frozen": 100.0 * per_node(res.frozen_fraction),
        "thickness": mesh.thickness.copy(),
        "shear": per_node(res.shear_rate),
    }
    gate_info = []
    for g in solver._live_gates(gate_nodes):
        # one marker per gate place (not one for each skin's node)
        p = mesh.nodes[g]
        near = max(2.0 * mesh.element_size, 1.5 * float(mesh.thickness[g]))
        if any(np.linalg.norm(p - np.asarray(q)) < near for q, _ in gate_info):
            continue
        gate_info.append((tuple(map(float, p)), tuple(map(float, mesh.node_normal[g]))))
    hist = np.array(res.gate_history) if res.gate_history else np.zeros((0, 2))
    if len(hist) > 400:
        hist = hist[np.linspace(0, len(hist) - 1, 400).astype(int)]
    summary = {
        "material": mat.name,
        "melt_temp": mat.melt_temp,
        "mold_temp": mat.mold_temp,
        "volume_cm3": volume_cm3,
        "injection_time": t_inj,
        "fill_time": float(np.nanmax(fields["fill_time"])) if np.isfinite(ft).any() else 0.0,
        "injection_pressure": res.injection_pressure / 1e6,
        "max_pressure": settings.max_pressure / 1e6,
        "filled": res.filled_fraction,
        "weld_lines": _count_lines(mesh, welds),
        "meld_lines": len(melds),
        "air_traps": len(traps),
        "gate_suggested": suggested,
        "nodes": n,
        "element_size": mesh.element_size,
        "steps": res.steps,
        "seconds": res.seconds,
        "cooling": settings.cooling,
    }
    return MoldFlowResult(
        nodes=mesh.nodes.copy(),
        tris=mesh.tris.copy(),
        fields=fields,
        welds=[(*map(float, mesh.nodes[a]), *map(float, mesh.nodes[b]), float(ang))
               for a, b, ang in welds],
        melds=[[tuple(map(float, p)) for p in line] for line in melds],
        traps=[(*map(float, mesh.nodes[node]), float(t)) for node, t in traps],
        gates=gate_info,
        last=tuple(map(float, mesh.nodes[last])),
        gate_history=[(float(t), float(p) / 1e6) for t, p in hist],
        summary=summary,
    )


def _count_lines(mesh, welds):
    """Weld lines as the user sees them (both skins of a wall show the same
    line: count lines a wall apart as one)."""
    if not welds:
        return 0
    from scipy.spatial import cKDTree
    from scipy.sparse.csgraph import connected_components
    import scipy.sparse as sp

    a = np.array([w[0] for w in welds])
    b = np.array([w[1] for w in welds])
    mid = 0.5 * (mesh.nodes[a] + mesh.nodes[b])
    reach = max(1.6 * mesh.element_size, 1.2 * float(np.median(mesh.tri_thickness)))
    close = cKDTree(mid).query_pairs(reach, output_type="ndarray")
    k = len(a)
    g = (sp.coo_matrix((np.ones(len(close)), (close[:, 0], close[:, 1])), shape=(k, k))
         if len(close) else sp.coo_matrix((k, k)))
    return int(connected_components(g, directed=False)[0])


# -- Saving -----------------------------------------------------------------------------


def _round(values, digits):
    return [None if not math.isfinite(v) else round(float(v), digits) for v in values]


def to_field(result: MoldFlowResult) -> dict:
    """Plain lists for the analysis object (no numpy, no inf/nan)."""
    return {
        "version": 1,
        "nodes": [round(float(v), 4) for v in result.nodes.ravel()],
        "tris": [int(v) for v in result.tris.ravel()],
        "fields": {k: _round(v, 6) for k, v in result.fields.items()},
        "welds": [[round(v, 4) for v in w] for w in result.welds],
        "melds": [[round(c, 4) for p in line for c in p] for line in result.melds],
        "traps": [[round(v, 5) for v in t] for t in result.traps],
        "gates": [[*g, *nrm] for g, nrm in result.gates],
        "last": list(result.last),
        "gate_history": [[round(t, 5), round(p, 4)] for t, p in result.gate_history],
        "summary": dict(result.summary),
    }


def from_field(data: dict) -> MoldFlowResult:
    nodes = np.array(data["nodes"], float).reshape(-1, 3)
    tris = np.array(data["tris"], np.int64).reshape(-1, 3)
    fields = {k: np.array([np.nan if v is None else v for v in vals], float)
              for k, vals in data["fields"].items()}
    return MoldFlowResult(
        nodes=nodes,
        tris=tris,
        fields=fields,
        welds=[tuple(w) for w in data.get("welds", [])],
        melds=[[tuple(line[i:i + 3]) for i in range(0, len(line), 3)]
               for line in data.get("melds", [])],
        traps=[tuple(t) for t in data.get("traps", [])],
        gates=[(tuple(g[:3]), tuple(g[3:6])) for g in data.get("gates", [])],
        last=tuple(data.get("last", (0.0, 0.0, 0.0))),
        gate_history=[tuple(h) for h in data.get("gate_history", [])],
        summary=dict(data.get("summary", {})),
    )
