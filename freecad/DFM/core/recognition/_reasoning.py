# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Arbitration: who contains whom, who touches whom, then the features.

Face ownership was settled as candidates were accepted, in priority order
(blends, holes, bosses, ribs, slots, steps, pockets, residuals). Here the
accepted candidates are linked:

* a hole on a boss's axis opening on the boss is the boss's (screw boss);
* a fillet or chamfer chain belongs to the feature owning most of its
  support faces (a rib's root fillets, a boss's top chamfer);
* a rib ending on a boss is a gusset; ribs meeting ribs form a network;
* features using each other's faces as supports interact.
"""

from __future__ import annotations

from .model import Feature, FeatureKind, renumber
from ._recognizers.common import Context, r3
from ._topology import vec as V

_BLENDS = (FeatureKind.FILLET, FeatureKind.CHAMFER)


def _coaxial(a, b, tol_dist: float, tol_deg: float = 1.0) -> bool:
    (pa, da), (pb, db) = a, b
    if V.line_angle(da, db) > tol_deg:
        return False
    return V.point_line_distance(pb, pa, da) <= tol_dist


def link(ctx: Context) -> None:
    cands = ctx.accepted
    by_id = {c.id: c for c in cands}
    owner = ctx.owner
    tol = max(ctx.options.linear_tolerance * 10, 1e-6 * ctx.topo.scale, 1e-6)

    # Holes in bosses
    bosses = [c for c in cands if c.kind == FeatureKind.BOSS]
    for h in (c for c in cands if c.kind == FeatureKind.HOLE):
        for b in bosses:
            if "axis" not in h.links or "axis" not in b.links:
                continue
            if not _coaxial(h.links["axis"], b.links["axis"], tol):
                continue
            if not (set(h.support) & set(b.faces)):
                continue
            h.parent = b.id
            b.params["inner_diameter"] = h.params.get("diameter")
            od = b.params.get("outer_diameter", 0.0)
            b.params["wall_thickness"] = r3((od - h.params.get("diameter", 0.0)) / 2.0)
            b.params["inner_hole"] = h.id
            # A pin is solid by definition: a slender boss with a core is a screw boss.
            if b.subtype in ("solid", "stepped", "pin"):
                b.subtype = "screw"
            b.score.ok(f"coaxial hole Ø{h.params.get('diameter')}: wall {b.params['wall_thickness']}")

    # Ribs: what their ends join
    for r in (c for c in cands if c.kind == FeatureKind.RIB):
        joined = {owner.get(f) for f in r.links.get("joins", ()) if f in owner}
        joined.discard(None)
        joined.discard(r.id)
        bosses_joined = [j for j in joined if by_id[j].kind == FeatureKind.BOSS]
        ribs_joined = [j for j in joined if by_id[j].kind == FeatureKind.RIB]
        ends = []
        if bosses_joined:
            r.subtype = "gusset"
            ends.append("boss")
            for j in bosses_joined:
                by_id[j].params.setdefault("gussets", [])
                by_id[j].params["gussets"].append(r.id)
                r.parent = j
        if ribs_joined:
            if r.subtype == "single":
                r.subtype = "network"
            ends.append("rib")
            r.interacts.update(ribs_joined)
        unowned_joins = [f for f in r.links.get("joins", ()) if f not in owner]
        if unowned_joins or r.links.get("corner"):
            ends.append("wall")
        r.params["ends"] = tuple(sorted(set(ends))) or ("free",)
    for b in bosses:
        if "gussets" in b.params:
            b.params["gussets"] = tuple(sorted(b.params["gussets"]))
            b.params["gusset_count"] = len(b.params["gussets"])

    # Blend chains belong to the feature owning most of their supports
    for c in cands:
        if c.kind not in _BLENDS:
            continue
        counts: dict[int, int] = {}
        for s in c.support:
            o = owner.get(s)
            if o is not None and by_id[o].kind not in _BLENDS:
                counts[o] = counts.get(o, 0) + 1
        if counts:
            best = max(sorted(counts), key=lambda k: counts[k])
            c.parent = best

    # Interactions: one feature's faces are another's supports
    for c in cands:
        if c.kind in _BLENDS:
            continue
        for s in c.support:
            o = owner.get(s)
            if o is None or o == c.id:
                continue
            other = by_id[o]
            if other.kind in _BLENDS or c.parent == o or other.parent == c.id:
                continue
            c.interacts.add(o)
            other.interacts.add(c.id)


def to_features(ctx: Context) -> tuple[Feature, ...]:
    feats = []
    for c in ctx.accepted:
        params = {k: (tuple(v) if isinstance(v, list) else v) for k, v in c.params.items()}
        feats.append(Feature(
            id=c.id, kind=c.kind, subtype=c.subtype, faces=tuple(sorted(c.faces)),
            support_faces=tuple(sorted(set(c.support) - set(c.faces))), edges=tuple(sorted(set(c.edges))),
            params=params, frame=c.frame, confidence=round(c.score.confidence, 4),
            evidence=tuple(c.score.evidence), parent=c.parent,
            interacts_with=tuple(sorted(c.interacts - {c.id})),
        ))
    return renumber(feats)
