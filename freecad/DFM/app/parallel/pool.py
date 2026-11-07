# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The worker processes: started once, kept warm for the session.

Each worker is ``python -I worker.py`` (see interpreter.py) with pipes for
stdin and stdout and a log file for stderr. Messages are length-prefixed
pickles. One small reader thread per worker blocks on its stdout and puts what
arrives on a queue; the threads only read pipes and never touch FreeCAD or Qt.
Everything else happens on FreeCAD's main thread, which takes messages off
the queue between Qt event updates (measure.py), so the window stays alive
and Abort works.

A worker that dies shows up as end-of-file on its stdout; one that hangs is
killed after a timeout. Either way only that worker is lost; it is replaced
when work next needs it. When FreeCAD exits (or crashes) the workers see their
stdin close, or their parent change, and exit too.
"""

import atexit
import os
import pickle
import queue
import struct
import subprocess
import tempfile
import threading
import time
from typing import Optional

from . import interpreter

_HEADER = struct.Struct(">Q")
_WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker.py")
# The directory holding the "freecad" package this addon lives in.
_FREECAD_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _params():
    from ..prefs import dfm_params

    return dfm_params()


def configured_workers() -> int:
    """Worker count: the preference, or all cores but one (at most 12)."""
    try:
        wanted = int(_params().GetInt("ParallelWorkers", 0))
    except Exception:
        wanted = 0
    if wanted > 0:
        return wanted
    cores = os.cpu_count() or 1
    return max(1, min(cores - 1, 12))


class Worker:
    """One worker process and the thread reading its replies."""

    def __init__(self, python: str, number: int, inbox: "queue.Queue"):
        self.number = number
        self.inbox = inbox
        self.ready = False
        self.dead = False
        self.loaded_run = None
        self.job = None  # the batch it is working on
        self.job_started = 0.0
        self.info = {}
        self._log = tempfile.NamedTemporaryFile(
            prefix=f"dfm-worker-{number}-", suffix=".log", delete=False)
        self.log_path = self._log.name
        self.proc = subprocess.Popen(
            [python, *interpreter.FLAGS, _WORKER_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log,
            env=interpreter.worker_env(),
            creationflags=interpreter.creation_flags(),
            close_fds=True,
        )
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_loop, name=f"dfm-worker-{number}",
                                        daemon=True)
        self._reader.start()
        self.send({"op": "hello", "sys_path": interpreter.module_path(),
                   "freecad_dir": _FREECAD_DIR})

    def _read_loop(self):
        stream = self.proc.stdout
        try:
            while True:
                head = stream.read(_HEADER.size)
                if len(head) < _HEADER.size:
                    break
                (size,) = _HEADER.unpack(head)
                data = stream.read(size)
                if len(data) < size:
                    break
                self.inbox.put((self, pickle.loads(data)))
        except Exception:
            pass
        self.inbox.put((self, {"op": "exit"}))

    def send(self, message) -> bool:
        if self.dead:
            return False
        data = pickle.dumps(message, protocol=pickle.HIGHEST_PROTOCOL)
        try:
            with self._lock:
                self.proc.stdin.write(_HEADER.pack(len(data)) + data)
                self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            self.dead = True
            return False

    def log_tail(self, lines: int = 12) -> str:
        try:
            with open(self.log_path, "r", errors="replace") as f:
                return "".join(f.readlines()[-lines:]).strip()
        except Exception:
            return ""

    def kill(self) -> None:
        self.dead = True
        try:
            self.proc.kill()
        except Exception:
            pass

    def close(self, wait: float = 1.0) -> None:
        if not self.dead:
            self.send({"op": "quit"})
            try:
                self.proc.wait(timeout=wait)
            except Exception:
                pass
        self.kill()
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except Exception:
                pass
        try:
            self._log.close()
            os.unlink(self.log_path)
        except Exception:
            pass


class WorkerPool:
    """The session's workers. Use from FreeCAD's main thread only (prewarm()
    may start it from a helper thread, under the lock)."""

    def __init__(self):
        self.inbox: "queue.Queue" = queue.Queue()
        self.workers: list[Worker] = []
        self.python: Optional[str] = None
        self.reason = ""
        self._lock = threading.RLock()
        self._numbers = 0
        self.notes: list[str] = []  # messages for the console, printed by the main thread

    # -- Starting -------------------------------------------------------------

    def ensure(self, count: int) -> bool:
        """Have *count* workers starting or running. False if workers can't run."""
        with self._lock:
            if self.python is None:
                self.python, self.reason = interpreter.find_worker_python()
                if self.python is None:
                    return False
                self.notes.append(f"parallel workers use {self.python}")
            self.workers = [w for w in self.workers if not w.dead]
            while len(self.workers) < count:
                self._numbers += 1
                try:
                    self.workers.append(Worker(self.python, self._numbers, self.inbox))
                except Exception as exc:
                    self.reason = f"could not start a worker: {exc}"
                    return bool(self.workers)
            return True

    def replace(self, worker: Worker) -> Optional[Worker]:
        """Kill *worker* and start another in its place."""
        with self._lock:
            worker.kill()
            if worker in self.workers:
                self.workers.remove(worker)
            if self.python is None:
                return None
            self._numbers += 1
            try:
                fresh = Worker(self.python, self._numbers, self.inbox)
            except Exception:
                return None
            self.workers.append(fresh)
            return fresh

    def wait_ready(self, timeout: float, pump=None) -> list:
        """Wait up to *timeout* s for the workers to finish starting; the ready ones."""
        deadline = time.monotonic() + timeout
        while True:
            pending = [w for w in self.workers if not w.ready and not w.dead]
            if not pending or time.monotonic() > deadline:
                break
            if pump is not None and pump():
                break
            try:
                worker, message = self.inbox.get(timeout=0.05)
            except queue.Empty:
                continue
            self.handle_startup(worker, message)
        for w in [w for w in self.workers if not w.ready and not w.dead]:
            self.notes.append(f"worker {w.number} did not start in time: {w.log_tail(3)}")
            w.kill()
        self.workers = [w for w in self.workers if not w.dead]
        return [w for w in self.workers if w.ready]

    def handle_startup(self, worker: Worker, message) -> bool:
        """Deal with a startup message; True if it was one."""
        op = message.get("op")
        if op == "ready":
            worker.ready = True
            worker.info = message
            return True
        if op == "error" and not worker.ready:
            self.notes.append(f"worker {worker.number} failed to start:\n{message.get('error', '')}")
            worker.kill()
            return True
        if op == "exit" and not worker.ready:
            self.notes.append(f"worker {worker.number} exited while starting: {worker.log_tail(3)}")
            worker.dead = True
            return True
        return False

    # -- Stopping ---------------------------------------------------------------

    def kill_all(self) -> None:
        """Stop every worker now (Abort): long batches can't be interrupted otherwise."""
        with self._lock:
            for w in self.workers:
                w.close(wait=0.0)
            self.workers = []
            self._drain()

    def shutdown(self) -> None:
        with self._lock:
            for w in self.workers:
                w.close(wait=1.0)
            self.workers = []
            self._drain()

    def _drain(self) -> None:
        while True:
            try:
                self.inbox.get_nowait()
            except queue.Empty:
                return

    def take_notes(self) -> list:
        notes, self.notes = self.notes, []
        return notes


_POOL: Optional[WorkerPool] = None
_POOL_LOCK = threading.Lock()


def get_pool() -> WorkerPool:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = WorkerPool()
        return _POOL


def prewarm() -> None:
    """Start the workers in the background (e.g. when the DFM panel opens), so
    the first analysis doesn't wait for them. Does nothing if parallel
    processing is off or already running."""
    from .measure import parallel_enabled

    if not parallel_enabled():
        return
    pool = get_pool()
    if pool.workers or (pool.python is None and interpreter._searched):
        return

    def start():
        try:
            pool.ensure(configured_workers())
        except Exception:
            pass

    threading.Thread(target=start, name="dfm-prewarm", daemon=True).start()


def shutdown() -> None:
    global _POOL
    with _POOL_LOCK:
        pool, _POOL = _POOL, None
    if pool is not None:
        pool.shutdown()


atexit.register(shutdown)
