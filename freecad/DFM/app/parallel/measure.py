# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The parallel phase of an analysis: measure the model with the workers.

The analyzers worth it (``BaseAnalyzer.parallel``) are run together: their
batches of faces (``BaseAnalyzer.batches()``, the same in every run) go into
one queue, the most expensive analyzers first, and each idle worker takes the
next batch. All of it runs on FreeCAD's main thread, between Qt event
updates, so the window keeps redrawing and Abort works at once.

When something goes wrong, the analysis carries on:

* a batch that raises a Python error in a worker is run again here, in
  FreeCAD's process, exactly as it would have been without workers;
* a worker that crashes (an OpenCASCADE fault) or hangs is replaced and its
  batch tried once more; if that fails too, those faces are skipped and named
  in the report view. A crash in a worker can't take FreeCAD down;
* if workers can't be used at all, nothing is measured here and the analysis
  runs in FreeCAD's process as before.
"""

import collections
import os
import queue
import tempfile
import time
import uuid
from typing import Any, Callable, Optional

from .pool import configured_workers, get_pool
from .shapes import fingerprint, write_shape

_STARTUP_TIMEOUT = 90.0


def _params():
    from ..prefs import dfm_params

    return dfm_params()


def parallel_enabled() -> bool:
    try:
        return bool(_params().GetBool("ParallelEnabled", True))
    except Exception:
        return False


def min_items() -> int:
    """Below this many faces (summed over the analyzers) it isn't worth it."""
    try:
        return max(1, int(_params().GetInt("ParallelMinFaces", 24)))
    except Exception:
        return 24


def batch_timeout() -> float:
    """Seconds one batch may take before its worker is taken to have hung."""
    try:
        return float(max(10, int(_params().GetInt("ParallelBatchTimeout", 600))))
    except Exception:
        return 600.0


# -- Sending the analysis settings ------------------------------------------------


def _encode(value):
    """Plain, picklable form of an analysis setting (see worker._decode)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    name = type(value).__name__
    if name in ("gp_Dir", "gp_Pnt", "gp_Vec"):
        return {"__ocp__": name, "data": (value.X(), value.Y(), value.Z())}
    if name == "gp_Pln":
        loc, axis = value.Location(), value.Axis().Direction()
        return {"__ocp__": "gp_Pln",
                "data": ((loc.X(), loc.Y(), loc.Z()), (axis.X(), axis.Y(), axis.Z()))}
    if isinstance(value, (list, tuple)):
        items = [_encode(v) for v in value]
        return items if isinstance(value, list) else tuple(items)
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    raise TypeError(f"can't send a {name} to a worker")


def encode_kwargs(kwargs: dict) -> tuple:
    """(settings the analyzers read, names of those that can't be sent)."""
    from ...core.models import ProcessRequirement

    wanted = {r.name for r in ProcessRequirement} | {"prefs"}
    encoded, unsupported = {}, set()
    for key, value in kwargs.items():
        if key not in wanted:
            continue
        try:
            encoded[key] = _encode(value)
        except TypeError:
            unsupported.add(key)
    prefs = dict(encoded.get("prefs") or {})
    # Workers already use every core; OCCT threads inside each would fight them.
    prefs["SphereMultiThreaded"] = False
    encoded["prefs"] = prefs
    return encoded, unsupported


# -- The outcome --------------------------------------------------------------------


class Outcome:
    """What the parallel phase measured."""

    def __init__(self):
        self.results: dict[str, dict] = {}  # analyzer id -> {("Face", i): value}
        # Values measured for one face from another (BaseAnalyzer.contribute()).
        self.contributions: dict[str, dict] = collections.defaultdict(dict)
        self.skipped: dict[str, list[int]] = collections.defaultdict(list)
        self.merged: set[str] = set()  # analyzers whose results are final
        self.worker_seconds: dict[str, float] = collections.defaultdict(float)
        self.workers = 0
        self.aborted = False
        self.wall_seconds = 0.0


Progress = Optional[Callable[[int, str], None]]
Abort = Optional[Callable[[], bool]]
Log = Callable[[str], None]
# status(workers, {analyzer id: {"done", "total", "busy", "started", "finished",
# "cpu"}}): the state of every analyzer in the parallel phase, as it changes.
# "busy" is how many workers are on it now; the times are time.monotonic() or
# None; "cpu" is the seconds its finished batches took, summed over workers.
Status = Optional[Callable[[int, dict], None]]
# finished(analyzer_id, results): an analyzer's last batch is in; *results* are
# final (merged). Called at once, while the other analyzers are still measured,
# so its rules can be checked straight away.
Finished = Optional[Callable[[str, Any], None]]


def measure_in_parallel(
    analyzers: list,
    shape,
    face_index,
    edge_index,
    kwargs: dict,
    progress: Progress = None,
    check_abort: Abort = None,
    log: Log = print,
    warn: Log = print,
    status: Status = None,
    finished: Finished = None,
) -> Optional[Outcome]:
    """Measure the model for *analyzers* ([(id, class), ...]) with the workers.

    *progress(items_done, analyzer_id)* is called as batches finish,
    *status* with every analyzer's state (see Status), and *finished* as each
    analyzer is done (see Finished). Returns None when
    workers weren't used (the caller measures as usual), else an Outcome
    holding every analyzer it took on.
    """
    if not parallel_enabled() or not analyzers:
        return None
    total = sum(cls().item_count(face_index, edge_index) for _aid, cls in analyzers)
    if total < min_items():
        return None
    count = configured_workers()
    if count < 2:
        return None

    pool = get_pool()
    started = time.perf_counter()
    if not pool.ensure(count):
        _flush(pool, log)
        log(f"Parallel processing unavailable, measuring in FreeCAD's process ({pool.reason}).")
        return None

    encoded, unsupported = encode_kwargs(kwargs)
    eligible = [(aid, cls) for aid, cls in analyzers
                if not ({r.name for r in cls().requirements} & unsupported)]
    if not eligible:
        return None

    if progress:
        progress(0, eligible[0][0])
    ready = pool.wait_ready(_STARTUP_TIMEOUT, pump=check_abort)
    _flush(pool, log)
    outcome = Outcome()
    if check_abort and check_abort():
        outcome.aborted = True
        return outcome
    if len(ready) < 2:
        log("Parallel processing unavailable, measuring in FreeCAD's process "
            "(the worker processes did not start).")
        return None

    fd, path = tempfile.mkstemp(prefix="dfm-shape-", suffix=".brep")
    os.close(fd)
    try:
        write_shape(shape, path)
        job = _Job(pool, eligible, shape, face_index, edge_index, kwargs, encoded, path,
                   progress, check_abort, log, warn, status, finished)
        job.run(outcome)
        if not outcome.aborted:
            for aid, cls in eligible:  # normally all merged as they finished
                if aid not in outcome.merged:
                    outcome.results[aid] = cls().merge(
                        _in_order(outcome.results.get(aid, {})),
                        outcome.contributions.get(aid, {}))
                    outcome.merged.add(aid)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    outcome.workers = len(ready)
    outcome.wall_seconds = time.perf_counter() - started
    _flush(pool, log)
    return outcome


def _in_order(results: dict) -> dict:
    """Batches come back in any order; put the faces back in model order, as
    when measured in FreeCAD's process (so findings list the same way)."""
    try:
        return dict(sorted(results.items(), key=lambda item: item[0]))
    except TypeError:
        return results


def _flush(pool, log):
    for note in pool.take_notes():
        log(note)


class _Job:
    """One parallel phase: batches out, results in."""

    def __init__(self, pool, analyzers, shape, face_index, edge_index, kwargs, encoded, path,
                 progress, check_abort, log, warn, status=None, finished=None):
        self.pool = pool
        self.status = status
        self.finished = finished
        self.shape, self.face_index, self.edge_index = shape, face_index, edge_index
        self.kwargs = kwargs
        self.progress, self.check_abort, self.log, self.warn = progress, check_abort, log, warn
        self.run_id = uuid.uuid4().hex
        self.load = {"op": "load", "run": self.run_id, "brep": path, "kwargs": encoded,
                     "fingerprint": fingerprint(shape, face_index, edge_index)}
        self.timeout = batch_timeout()

        # Most expensive analyzers first, so the long ones aren't left for last.
        ordered = sorted(analyzers, key=lambda a: -getattr(a[1], "relative_cost", 1))
        self.classes = dict(ordered)
        self.pending = collections.deque()
        for aid, cls in ordered:
            for batch in cls().batches(face_index, edge_index):
                self.pending.append((aid, batch))
        self.attempts = collections.Counter()
        self.in_process = {}  # analyzer id -> prepared analyzer, for fallbacks
        self.failed_load = set()
        self.done_items = 0
        self.jobs = {}  # job id -> (aid, batch)
        self.next_job = 0
        self.reported_errors = set()
        self.state = {
            aid: {"done": 0, "total": cls().item_count(face_index, edge_index), "busy": 0,
                  "started": None, "finished": None, "cpu": 0.0}
            for aid, cls in ordered
        }
        self.remaining = collections.Counter(aid for aid, _batch in self.pending)

    # -- Main loop -----------------------------------------------------------

    def run(self, outcome: Outcome) -> None:
        for aid in self.classes:
            outcome.results[aid] = {}
        for worker in self.pool.workers:
            if worker.ready and not worker.dead:
                self._send_load(worker)
        self._report_status()

        while self.pending or self._busy():
            if self.check_abort and self.check_abort():
                self.pool.kill_all()
                outcome.aborted = True
                return
            self._dispatch()
            if not self._usable_workers() and self.pending:
                # No worker left that can take work: finish here.
                self._finish_in_process(outcome)
                return
            try:
                worker, message = self.pool.inbox.get(timeout=0.05)
            except queue.Empty:
                self._check_timeouts(outcome)
                continue
            self._handle(worker, message, outcome)
            self._check_timeouts(outcome)

    def _busy(self):
        return any(w.job is not None for w in self.pool.workers)

    def _usable_workers(self):
        return [w for w in self.pool.workers
                if not w.dead and w not in self.failed_load and (w.ready or w.job is None)]

    def _send_load(self, worker):
        if worker.send(self.load):
            worker.loaded_run = self.run_id

    def _dispatch(self):
        for worker in self.pool.workers:
            if not self.pending:
                return
            if (worker.dead or not worker.ready or worker.job is not None
                    or worker.loaded_run != self.run_id or worker in self.failed_load):
                continue
            aid, batch = self.pending.popleft()
            self.next_job += 1
            job_id = self.next_job
            if worker.send({"op": "batch", "run": self.run_id, "job": job_id,
                            "analyzer": aid, "items": batch}):
                self.jobs[job_id] = (aid, batch)
                worker.job = job_id
                worker.job_started = time.monotonic()
                if self.state[aid]["started"] is None:
                    self.state[aid]["started"] = worker.job_started
                self._report_status()
            else:
                self.pending.appendleft((aid, batch))

    # -- Messages ----------------------------------------------------------------

    def _handle(self, worker, message, outcome):
        op = message.get("op")
        if self.pool.handle_startup(worker, message):
            if worker.ready and worker.loaded_run != self.run_id:
                self._send_load(worker)  # a replacement worker just started
            return
        if op == "loaded":
            return
        if op == "error":  # couldn't load the shape
            self.failed_load.add(worker)
            self._report("load",
                         f"A worker could not load the model:\n{message.get('error', '')}")
            return
        if op in ("done", "failed"):
            entry = self.jobs.pop(message.get("job"), None)
            if worker.job == message.get("job"):
                worker.job = None
            if entry is None:
                return
            aid, batch = entry
            if message.get("unloaded"):
                # The worker couldn't load the model: someone else takes the batch.
                self.failed_load.add(worker)
                self.pending.appendleft((aid, batch))
                return
            if op == "done":
                self._collect(outcome, aid, message.get("results", {}),
                              message.get("contributions", {}))
                seconds = float(message.get("seconds", 0.0))
                outcome.worker_seconds[aid] += seconds
                self.state[aid]["cpu"] += seconds
            else:
                self._report(f"failed:{aid}",
                             f"{aid} raised an error in a worker; measuring those faces here.\n"
                             f"{message.get('error', '')}")
                self._collect(outcome, aid, *self._run_here(aid, batch))
            self._advance(len(batch), aid, outcome)
            return
        if op == "exit":
            self._lost(worker, outcome, "stopped unexpectedly")

    def _check_timeouts(self, outcome):
        now = time.monotonic()
        for worker in list(self.pool.workers):
            if worker.job is not None and now - worker.job_started > self.timeout:
                self._lost(worker, outcome, f"took over {int(self.timeout)} s on one batch")

    def _lost(self, worker, outcome, why):
        """A worker crashed or hung: replace it, and retry its batch once."""
        if worker not in self.pool.workers:
            return  # already dealt with (a killed worker's late exit message)
        job_id, worker.job = worker.job, None
        entry = self.jobs.pop(job_id, None) if job_id is not None else None
        tail = worker.log_tail(4)
        replacement = self.pool.replace(worker)
        if replacement is not None:
            replacement.loaded_run = None  # loads when it says it is ready
        if entry is None:
            return
        aid, batch = entry
        key = (aid, tuple(batch))
        self.attempts[key] += 1
        if self.attempts[key] < 2:
            self.log(f"A worker {why} while measuring {aid}; trying those faces again.")
            self.pending.appendleft((aid, batch))
            return
        outcome.skipped[aid].extend(batch)
        names = ", ".join(f"{self.classes[aid]().item_kind}{i}" for i in batch)
        self.warn(f"DFM: {aid} {why} twice on {names}; those were not measured."
                  + (f"\n{tail}" if tail else ""))
        self._advance(len(batch), aid, outcome)

    # -- Here, in FreeCAD's process ------------------------------------------------

    def _run_here(self, aid, batch):
        started = time.perf_counter()
        try:
            return self._run_here_timed(aid, batch)
        finally:
            self.state[aid]["cpu"] += time.perf_counter() - started

    def _run_here_timed(self, aid, batch):
        analyzer = self.in_process.get(aid)
        if analyzer is None:
            analyzer = self.classes[aid]()
            analyzer._check_abort = self.check_abort
            analyzer.prepare(self.shape, self.face_index, self.edge_index, **self.kwargs)
            self.in_process[aid] = analyzer
        return analyzer.run_batch(batch, self.face_index, self.edge_index,
                                  check_abort=self.check_abort)

    def _finish_in_process(self, outcome):
        self.log("No worker processes left; measuring the rest in FreeCAD's process.")
        while self.pending:
            if self.check_abort and self.check_abort():
                outcome.aborted = True
                return
            aid, batch = self.pending.popleft()
            self._collect(outcome, aid, *self._run_here(aid, batch))
            self._advance(len(batch), aid, outcome)

    @staticmethod
    def _collect(outcome, aid, results, contributions):
        outcome.results[aid].update(results)
        given = outcome.contributions[aid]
        for key, values in contributions.items():
            given.setdefault(key, []).extend(values)

    # -- Reporting ---------------------------------------------------------------

    def _advance(self, items, aid, outcome):
        self.done_items += items
        state = self.state[aid]
        state["done"] += items
        self.remaining[aid] -= 1
        just_finished = self.remaining[aid] <= 0 and state["finished"] is None
        if just_finished:
            state["finished"] = time.monotonic()
        if self.progress:
            busy = [self.jobs[w.job][0] for w in self.pool.workers if w.job in self.jobs]
            self.progress(self.done_items, busy[0] if busy else aid)
        self._report_status()
        if just_finished:
            self._finish(aid, outcome)

    def _finish(self, aid, outcome):
        """All of an analyzer's batches are in: merge them, and hand the
        results over while the workers carry on with the others."""
        if outcome.aborted:
            return
        outcome.results[aid] = self.classes[aid]().merge(
            _in_order(outcome.results.get(aid, {})), outcome.contributions.get(aid, {}))
        outcome.merged.add(aid)
        if not self.finished:
            return
        self._dispatch()  # keep the workers busy while its rules are checked
        try:
            self.finished(aid, outcome.results[aid])
        except Exception as exc:
            self._report(f"finished:{aid}", f"checking {aid}'s results failed: {exc}")

    def _report_status(self):
        if not self.status:
            return
        busy = collections.Counter(self.jobs[w.job][0] for w in self.pool.workers
                                   if w.job in self.jobs)
        for aid, state in self.state.items():
            state["busy"] = busy.get(aid, 0)
        workers = sum(1 for w in self.pool.workers if not w.dead and w not in self.failed_load)
        try:
            self.status(workers, {aid: dict(s) for aid, s in self.state.items()})
        except Exception:
            pass

    def _report(self, key, text):
        if key not in self.reported_errors:
            self.reported_errors.add(key)
            self.warn("DFM: " + text)
