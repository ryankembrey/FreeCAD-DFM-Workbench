# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""recognize(): the pipeline.

1. Topology: faces and edges numbered like FaceIndex / EdgeIndex, each
   described (surface, axis, inside/outside, loops; edge convexity).
2. AAG: the attributed adjacency graph.
3. Fillets and chamfers, then hidden in the graph (virtual arcs).
4. Hint-based recognizers in priority order, each claiming its faces:
   holes, bosses, threads, ribs, slots, steps, pockets/cutouts, residuals.
5. History hints, then arbitration (parents, gussets, interactions).
"""

from __future__ import annotations

import time
import traceback
from typing import Callable, Iterable, Optional

from OCP.TopoDS import TopoDS_Shape

from ._graph import AAG
from ._reasoning import link, to_features
from ._recognizers import blends, bosses, depressions, holes, molding, pockets, ribs, slots, threads
from ._recognizers.common import Context
from ._topology import build_topology
from ._version import ENGINE_VERSION
from .cache import fingerprint
from .hints import Hint, apply_hints
from .model import FaceSignature, RecognitionResult
from .options import RecognitionOptions

ProgressCallback = Optional[Callable[[str, float], None]]

#: (name, function) in priority order. Names are what options.disabled takes.
RECOGNIZERS = (
    # Closed grooves first: their walls would read as holes, bosses or lips.
    ("ring_grooves", molding.find_ring_grooves),
    ("holes", holes.find_holes),
    ("crush_ribs", molding.find_crush_ribs),
    ("bosses", bosses.find_bosses),
    ("threads", threads.find_threads),
    # Moulded details that would otherwise read as ribs or pockets.
    ("snap_fits", molding.find_snap_fits),
    ("lips", molding.find_lips),
    ("ribs", ribs.find_ribs),
    # Closed depressions first: a pocket's opposite walls would otherwise
    # read as a slot.
    ("pockets", pockets.find_pockets),
    ("slots", slots.find_slots),
    ("slits", slots.find_slits),
    ("steps", slots.find_steps),
    # Whatever prismatic depression the hints above missed, by its profile.
    ("depressions", depressions.find_depressions),
    # Round cradles cut across walls (what is left of part-cylinders).
    ("saddles", depressions.find_saddles),
)


def build_graph(shape: TopoDS_Shape, options: Optional[RecognitionOptions] = None) -> AAG:
    """The AAG alone (for the developer bench and tests)."""
    options = options or RecognitionOptions()
    return AAG(build_topology(shape, options), options)


#: Passes over what the recognizers found: details made of other features.
POST_PASSES = (
    ("living_hinges", molding.living_hinges),
    ("snap_windows", molding.snap_windows),
    ("nut_traps", molding.nut_traps),
    ("vents", molding.vents),
    ("text", molding.text),
)


def recognize(
    shape: TopoDS_Shape,
    options: Optional[RecognitionOptions] = None,
    *,
    hints: Iterable[Hint] = (),
    progress: ProgressCallback = None,
    check_abort: Optional[Callable[[], bool]] = None,
    strict: bool = False,
) -> RecognitionResult:
    """Recognise the features of *shape* (a solid, or a compound of solids).

    Args:
        options: thresholds and tolerances (defaults if None).
        hints: what the model's history says (see hints.py).
        progress: called with (stage name, fraction done).
        check_abort: return True to stop; recognize() then raises
            InterruptedError.
        strict: raise on a recognizer error instead of skipping that
            recognizer with a warning (for tests).
    """
    if shape is None or shape.IsNull():
        raise ValueError("Cannot recognise features of an empty shape.")
    options = options or RecognitionOptions()
    timings: dict[str, float] = {}
    t_all = time.perf_counter()

    def stage(name: str, fraction: float):
        if check_abort and check_abort():
            raise InterruptedError
        if progress:
            progress(name, fraction)

    stage("topology", 0.0)
    t = time.perf_counter()
    topo = build_topology(shape, options, check_abort)
    timings["topology"] = time.perf_counter() - t

    stage("graph", 0.3)
    t = time.perf_counter()
    aag = AAG(topo, options)
    ctx = Context(topo, aag, options, strict)
    timings["graph"] = time.perf_counter() - t

    def run(name: str, fn, fraction: float):
        if not options.enabled(name):
            return []
        stage(name, fraction)
        t0 = time.perf_counter()
        try:
            found = fn(ctx)
        except InterruptedError:
            raise
        except Exception as exc:  # one recognizer failing must not lose the rest
            if strict:
                raise
            ctx.warn(f"{name}: {type(exc).__name__}: {exc}")
            ctx.warn(traceback.format_exc(limit=3))
            found = []
        accepted = [c for c in found if ctx.accept(c)]
        timings[name] = time.perf_counter() - t0
        return accepted

    accepted_blends = run("fillets", blends.find_blends, 0.35)
    accepted_blends += run("chamfers", blends.find_chamfers, 0.4)
    blends.suppress(ctx, accepted_blends)

    n = len(RECOGNIZERS)
    for i, (name, fn) in enumerate(RECOGNIZERS):
        run(name, fn, 0.45 + 0.45 * i / n)
    depressions.merge_split_passages(ctx)
    pockets.claim_rib_walls(ctx)
    if options.report_residuals:
        run("residuals", lambda c: pockets.find_pockets(c, residual=True), 0.92)
    for name, fn in POST_PASSES:
        if not options.enabled(name):
            continue
        t0 = time.perf_counter()
        try:
            fn(ctx)
        except Exception as exc:
            if strict:
                raise
            ctx.warn(f"{name}: {type(exc).__name__}: {exc}")
        timings[name] = time.perf_counter() - t0

    hints = tuple(hints)
    if hints and options.use_history_hints:
        stage("hints", 0.95)
        try:
            apply_hints(ctx, hints)
        except Exception as exc:
            if strict:
                raise
            ctx.warn(f"hints: {type(exc).__name__}: {exc}")

    stage("arbitration", 0.97)
    link(ctx)
    features = to_features(ctx)
    timings["total"] = time.perf_counter() - t_all
    stage("done", 1.0)

    edge_convexity = {}
    for e in topo.iter_edges():
        edge_convexity[e.index] = e.convexity
    sigs = {f.index: FaceSignature(f.index, f.kind, f.area, f.centroid) for f in topo.iter_faces()}
    try:
        from .form import part_form
        form = part_form(topo, features, options)
    except Exception as exc:
        if strict:
            raise
        form = {}
        ctx.warn(f"form: {type(exc).__name__}: {exc}")
    return RecognitionResult(
        features=features,
        face_count=topo.face_count,
        edge_count=topo.edge_count,
        fingerprint=fingerprint(shape),
        engine_version=ENGINE_VERSION,
        options=options.to_dict(),
        edge_convexity=edge_convexity,
        face_signatures=sigs,
        timings={k: round(v, 6) for k, v in timings.items()},
        warnings=tuple(ctx.warnings),
        form=form,
    )
