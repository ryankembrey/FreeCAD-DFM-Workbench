# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Filling: generalized Hele-Shaw flow on the dual domain surface mesh.

Each wall of the part is a narrow gap of thickness h. In it the melt flows
from high to low pressure at a rate set by its fluidity S = h^3 / (12 eta):

    flow per unit width  q = -S grad p,      div q = 0 in the filled region

Each skin carries half the wall (half its volume and fluidity), and the two
skins are tied by connectors where they face each other, so a wall fills
through its whole thickness at once. A wall's rim takes no part.

Filling uses control volumes around the nodes (CV/FEM): every step solves the
pressure over the filled nodes (zero at the melt front), works out the flow
into each node on the front, and moves time on until the next nodes are
full. Near full (the V/P switchover) the machine holds the pressure reached
instead of the speed, as a real one does.

The melt is shear thinning (Cross-WLF). Cooling is first order: where melt
has arrived, a frozen layer grows on each mold wall by conduction into the
cold steel (delta = 2 erfinv(theta) sqrt(alpha t)), narrowing the gap the
melt flows in; the flowing melt itself stays at the melt temperature.

Checked against closed-form answers (a centre-gated disc, an end-gated
strip): pressures and fill times within 1%.
"""

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import spsolve

from .material import Material

MM = 1e-3


class Aborted(Exception):
    """The run was stopped (check_abort returned True)."""


@dataclass
class FillResult:
    fill_time: np.ndarray  # s per node (inf: not reached)
    pressure_end: np.ndarray  # Pa per node, at the V/P switchover
    pressure_at_fill: np.ndarray  # Pa at the gate when each node filled
    gate_history: list = field(default_factory=list)  # [(t, p_gate)]
    steps: int = 0
    seconds: float = 0.0
    volume: float = 0.0  # m^3
    flow_rate: float = 0.0  # m^3/s
    injection_pressure: float = 0.0  # Pa, needed at the gate (at the switchover)
    filled_fraction: float = 0.0
    shear_rate: Optional[np.ndarray] = None  # 1/s per triangle at the end of fill
    frozen_fraction: Optional[np.ndarray] = None  # per triangle at the end of fill


class FillSolver:
    def __init__(self, mesh, material: Material, connector_factor=8.0):
        self.mesh = mesh
        self.material = material
        x = mesh.nodes * MM
        t = mesh.tris
        self.h = mesh.tri_thickness * MM  # m per triangle
        self.h_flow = self.h.copy()  # the gap still open (less the frozen layers)
        n = mesh.tri_normal
        area = mesh.tri_area * MM * MM
        self.area = area
        # gradients of the linear shape functions: n x (opposite edge) / 2A
        e0 = x[t[:, 2]] - x[t[:, 1]]
        e1 = x[t[:, 0]] - x[t[:, 2]]
        e2 = x[t[:, 1]] - x[t[:, 0]]
        self.grad = np.stack([np.cross(n, e0), np.cross(n, e1), np.cross(n, e2)], 1) \
            / (2.0 * area)[:, None, None]
        self.dots = np.einsum("tid,tjd->tij", self.grad, self.grad)
        self._rows = np.repeat(t, 3, axis=1).ravel()
        self._cols = np.tile(t, (1, 3)).ravel()
        self.n = len(x)
        # The rim of a wall takes no part in the flow: it has no volume of its
        # own, and nodes with none would fill the moment melt touched them,
        # racing along every rim (the two skins are tied by connectors).
        cap = mesh.tri_cap
        vol = np.zeros(self.n)
        for k in range(3):
            np.add.at(vol, t[:, k], np.where(cap, 0.0, area * self.h / 2.0 / 3.0))
        self.volume = vol
        self.flow_factor = np.where(cap, 0.0, 1.0)
        self.pairs = mesh.pairs
        self.connector_factor = connector_factor

    # -- Assembly --------------------------------------------------------------------

    def stiffness(self, eta):
        """Conductance matrix: per skin, fluidity S/2 = h^3 / (24 eta)."""
        g = self.h_flow ** 3 / (24.0 * eta) * self.area * self.flow_factor
        data = (g[:, None, None] * self.dots).ravel()
        K = sp.coo_matrix((data, (self._rows, self._cols)), shape=(self.n, self.n)).tocsr()
        K.eliminate_zeros()
        if len(self.pairs):
            diag = K.diagonal()
            i, j = self.pairs[:, 0], self.pairs[:, 1]
            k = self.connector_factor * 0.5 * (diag[i] + diag[j])
            C = sp.coo_matrix((np.concatenate([k, k, -k, -k]),
                               (np.concatenate([i, j, i, j]), np.concatenate([i, j, j, i]))),
                              shape=(self.n, self.n)).tocsr()
            K = K + C
        return K

    def shear_rate(self, p, eta):
        """Wall shear rate per triangle: tau_w = h |grad p| / 2, rate = tau_w / eta."""
        gp = np.einsum("ti,tid->td", p[self.mesh.tris], self.grad)
        return self.h_flow * np.linalg.norm(gp, axis=1) / 2.0 / eta

    # -- Filling ---------------------------------------------------------------------------

    def run(self, gates, injection_time=None, flow_rate=None, vp_switch=0.99, cooling=True,
            step_fraction=0.5, front_fraction=0.1, max_steps=200000, hold_factor=1.5,
            progress: Optional[Callable[[float, float], None]] = None,
            check_abort: Optional[Callable[[], bool]] = None) -> FillResult:
        """Fill from *gates* (node indices) at a constant flow rate.

        Give the flow rate (m^3/s) or the time to fill (s). *progress(share
        filled, time)* is called every few steps; *check_abort()* returning
        True stops the run (raises Aborted).

        After the V/P switchover the held pressure finishes the fill; whatever
        it hasn't reached by *hold_factor* times the switchover time is left
        unfilled (a short shot there: usually tiny or very thin features).
        """
        started = time.perf_counter()
        mat = self.material
        V = self.volume
        total = V.sum()
        if total <= 0:
            raise ValueError("The part has no volume to fill (is it a closed solid?).")
        Q = flow_rate if flow_rate is not None else total / (injection_time or 1.0)
        gates = self._live_gates(gates)
        tris = self.mesh.tris

        fill = np.zeros(self.n)
        full = np.zeros(self.n, bool)
        full[gates] = True
        fill[gates] = 1.0
        fill_time = np.full(self.n, np.inf)
        fill_time[gates] = 0.0
        p_at_fill = np.full(self.n, np.nan)
        p_at_fill[gates] = 0.0
        source = np.zeros(self.n)
        source[gates] = Q / len(gates)

        eta = np.full(len(tris), float(mat.viscosity(1000.0)))
        freeze_coef = self._freeze_coefficient() if cooling else 0.0
        tri_arrival = np.full(len(tris), np.inf)
        self.h_flow = self.h.copy()
        K = self.stiffness(eta)

        t = V[gates].sum() / Q
        history = []
        p = np.zeros(self.n)
        p_switch_field = None
        p_switch = None
        t_switch = None
        filled_volume = V[full].sum()
        steps = 0
        while steps < max_steps:
            steps += 1
            if check_abort is not None and check_abort():
                raise Aborted()
            D = np.nonzero(full)[0]
            KD = K[D]
            touched = np.unique(KD.indices)
            front = touched[~full[touched]]
            if len(front) == 0:
                break
            if p_switch is None and filled_volume >= vp_switch * total:
                # V/P switchover: from here the machine holds the pressure reached.
                p_switch = float(p[gates].max())
                p_switch_field = p.copy()
                t_switch = t
            if t_switch is not None and t > hold_factor * t_switch:
                break  # the held pressure has stalled: the rest is a short shot
            p[:] = 0.0
            if p_switch is None:
                pD = spsolve(KD[:, D].tocsc(), source[D])
                p[D] = pD
            else:
                inner = D[~np.isin(D, gates)]
                p[gates] = p_switch
                if len(inner):
                    rhs = -(K[inner][:, gates] @ p[gates])
                    p[inner] = spsolve(K[inner][:, inner].tocsc(), rhs)
                pD = p[D]
            # flow into each front node from the filled ones
            q = np.maximum(-(K[front][:, D] @ pD), 0.0)
            moving = q > 0
            if not moving.any():
                break
            front, q = front[moving], q[moving]
            flow_now = q.sum() if p_switch is not None else Q
            need = (1.0 - fill[front]) * V[front]
            with np.errstate(divide="ignore", over="ignore"):
                tau = need / q
            # fill about *front_fraction* of the front each step (at least one node)
            dt = max(tau.min() * (1.0 + step_fraction), np.quantile(tau, front_fraction))
            done = tau <= dt
            # Nodes that fill part way through the step take only what they
            # need; time moves on by what was actually taken in (no melt lost).
            taken = np.where(done, need, q * dt)
            with np.errstate(divide="ignore", invalid="ignore"):
                fill[front] = np.where(V[front] > 0, fill[front] + taken / V[front], 1.0)
            dt_used = taken.sum() / flow_now if flow_now > 0 else dt
            filled_volume += taken.sum()
            newly = front[done]
            fill[newly] = 1.0
            full[newly] = True
            fill_time[newly] = t + np.minimum(tau[done], dt_used)
            p_gate = float(p[gates].max())
            p_at_fill[newly] = p_gate
            t += dt_used
            history.append((t, p_gate))
            refresh = False
            if freeze_coef > 0:
                # cooling: frozen layers grow where melt has been
                arrived = np.isinf(tri_arrival) & (fill[tris].max(axis=1) > 0)
                tri_arrival[arrived] = t
                since = np.where(np.isfinite(tri_arrival), t - tri_arrival, 0.0)
                frozen = freeze_coef * np.sqrt(np.maximum(since, 0.0))
                self.h_flow = np.maximum(self.h - 2.0 * frozen, 0.05 * self.h)
                refresh = True
            if mat.n < 1.0:
                # shear thinning: viscosity from the shear rate just found
                eta = np.sqrt(eta * mat.viscosity(self.shear_rate(p, eta)))  # damped
                refresh = True
            if refresh:
                K = self.stiffness(eta)
            if progress is not None and steps % 5 == 0:
                progress(min(1.0, filled_volume / total), t)

        self._fill_rims(fill_time, p_at_fill)
        reached = np.isfinite(fill_time)
        if p_switch_field is None:
            p_switch_field = p.copy()
        return FillResult(
            fill_time=fill_time,
            pressure_end=p_switch_field,
            pressure_at_fill=p_at_fill,
            gate_history=history,
            steps=steps,
            seconds=time.perf_counter() - started,
            volume=total,
            flow_rate=Q,
            injection_pressure=(p_switch if p_switch is not None
                                else max((h for _t, h in history), default=0.0)),
            filled_fraction=float(V[reached].sum() / total),
            shear_rate=self.shear_rate(p, eta),
            frozen_fraction=1.0 - self.h_flow / self.h,
        )

    def _live_gates(self, gates):
        """Gate nodes that hold melt (a gate on a wall's rim feeds the skin
        node next to it), and their partners across the wall."""
        gates = np.unique(np.asarray(gates, np.int64))
        holds = self.volume > 0
        if len(gates) == 0:
            raise ValueError("No gate.")
        if not holds[gates].all():
            from scipy.spatial import cKDTree

            tree = cKDTree(self.mesh.nodes[holds])
            _d, k = tree.query(self.mesh.nodes[gates[~holds[gates]]])
            gates = np.unique(np.concatenate([gates[holds[gates]], np.nonzero(holds)[0][k]]))
        if len(self.pairs):
            mask = np.isin(self.pairs, gates)
            extra = np.concatenate([self.pairs[mask[:, 0], 1], self.pairs[mask[:, 1], 0]])
            gates = np.unique(np.concatenate([gates, extra]))
        return gates

    def _freeze_coefficient(self):
        mat = self.material
        if mat.no_flow_temp is None or mat.melt_temp <= mat.mold_temp:
            return 0.0
        from scipy.special import erfinv

        theta = (mat.no_flow_temp - mat.mold_temp) / (mat.melt_temp - mat.mold_temp)
        theta = float(np.clip(theta, 0.0, 0.95))
        return 2.0 * float(erfinv(theta)) * np.sqrt(mat.diffusivity)

    def _fill_rims(self, fill_time, p_at_fill, rounds=50):
        """Rim nodes (no volume, no flow) take the times of the skin beside them."""
        e = self.mesh.edges()
        n = self.n
        A = sp.coo_matrix((np.ones(2 * len(e)), (np.concatenate([e[:, 0], e[:, 1]]),
                                                 np.concatenate([e[:, 1], e[:, 0]]))),
                          shape=(n, n)).tocsr()
        for _ in range(rounds):
            empty = ~np.isfinite(fill_time)
            if not empty.any():
                break
            known = np.where(np.isfinite(fill_time), fill_time, 0.0)
            count = A @ (~empty).astype(float)
            take = empty & (count > 0) & (self.volume == 0)
            if not take.any():
                break
            fill_time[take] = (A @ known)[take] / count[take]
            pk = np.where(np.isfinite(p_at_fill), p_at_fill, 0.0)
            p_at_fill[take] = (A @ pk)[take] / count[take]


def nearest_nodes(mesh, point_mm, radius_mm=0.0):
    """Gate nodes: the node nearest *point*, and any within *radius*."""
    d = np.linalg.norm(mesh.nodes - np.asarray(point_mm, float), axis=1)
    nodes = np.nonzero(d <= radius_mm)[0] if radius_mm > 0 else np.array([], np.int64)
    return np.unique(np.concatenate([nodes, [int(d.argmin())]])).astype(np.int64)
