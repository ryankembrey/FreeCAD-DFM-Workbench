# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""A DFM worker process: run as a script by a plain Python, never by FreeCAD.

Speaks length-prefixed pickles over stdin/stdout (see pool.py). Imports only
OCP and the DFM core; FreeCAD itself is replaced by a small stand-in that
covers what the core uses (Console messages). Nothing is imported until the
parent has said where to find it, so this file must stay import-free at the
top beyond the standard library.

Messages from the parent, each answered with one message:

    hello  {sys_path, freecad_dir}           -> ready {pid, ocp, python}
    load   {run, brep, kwargs, fingerprint}  -> loaded {run} | error
    batch  {run, job, analyzer, items}       -> done {job, results, contributions, seconds}
                                                | failed {job, error}
    quit                                     -> (exits)
"""

import os
import pickle
import struct
import sys
import threading
import time
import traceback
import types

_HEADER = struct.Struct(">Q")


def _read(stream):
    head = stream.read(_HEADER.size)
    if len(head) < _HEADER.size:
        return None
    (size,) = _HEADER.unpack(head)
    data = stream.read(size)
    if len(data) < size:
        return None
    return pickle.loads(data)


def _write(stream, message):
    data = pickle.dumps(message, protocol=pickle.HIGHEST_PROTOCOL)
    stream.write(_HEADER.pack(len(data)) + data)
    stream.flush()


# -- Stand-ins for FreeCAD -------------------------------------------------------


class _Console:
    """FreeCAD.Console: messages go to stderr (the worker's log)."""

    def __getattr__(self, name):
        def write(*args, **_kwargs):
            sys.stderr.write(f"[{name}] " + " ".join(str(a) for a in args))
            if args and not str(args[-1]).endswith("\n"):
                sys.stderr.write("\n")

        return write


class _Params:
    def __getattr__(self, name):
        if name.startswith("Get"):
            return lambda _key=None, default=None: default
        return lambda *a, **k: None

    def GetContents(self):
        return []


def _install_stand_ins(freecad_dir):
    app = types.ModuleType("FreeCAD")
    app.Console = _Console()
    app.GuiUp = False
    app.ParamGet = lambda _path: _Params()
    app.getUserAppDataDir = lambda: os.path.join(os.path.expanduser("~"), ".dfm-worker") + os.sep
    sys.modules["FreeCAD"] = app
    part = types.ModuleType("Part")
    part.Shape = object
    sys.modules["Part"] = part
    # The DFM package without FreeCAD's own "freecad" package around it.
    package = types.ModuleType("freecad")
    package.__path__ = [freecad_dir]
    sys.modules["freecad"] = package


# -- Kwargs ---------------------------------------------------------------------


def _decode(value):
    """Rebuild the OCP values measure.py encoded (see measure._encode)."""
    if isinstance(value, dict) and "__ocp__" in value:
        from OCP.gp import gp_Dir, gp_Pnt, gp_Vec, gp_Pln, gp_Ax3

        kind, data = value["__ocp__"], value["data"]
        if kind == "gp_Dir":
            return gp_Dir(*data)
        if kind == "gp_Pnt":
            return gp_Pnt(*data)
        if kind == "gp_Vec":
            return gp_Vec(*data)
        if kind == "gp_Pln":
            loc, axis = data
            return gp_Pln(gp_Ax3(gp_Pnt(*loc), gp_Dir(*axis)))
        raise ValueError(f"unknown value {kind}")
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_decode(v) for v in value)
    if isinstance(value, dict):
        return {k: _decode(v) for k, v in value.items()}
    return value


# -- Work ------------------------------------------------------------------------


class _Run:
    def __init__(self, message):
        from freecad.DFM.core.utils.geometry import EdgeIndex, FaceIndex
        from freecad.DFM.app.parallel.shapes import fingerprint, read_shape

        self.shape = read_shape(message["brep"])
        self.face_index = FaceIndex(self.shape)
        self.edge_index = EdgeIndex(self.shape)
        mine = fingerprint(self.shape, self.face_index, self.edge_index)
        theirs = message["fingerprint"]
        if mine[:2] != theirs[:2] or abs(mine[2] - theirs[2]) > 1e-6 * max(1.0, abs(theirs[2])):
            raise RuntimeError(f"shape read back differently: {mine} != {theirs}")
        self.kwargs = _decode(message["kwargs"])
        self.analyzers = {}

    def analyzer(self, analyzer_id):
        found = self.analyzers.get(analyzer_id)
        if found is None:
            from freecad.DFM.core.registries.analyzers_registry import get_analyzer_class

            cls = get_analyzer_class(analyzer_id)
            if cls is None:
                raise KeyError(f"no analyzer {analyzer_id}")
            found = cls()
            found._check_abort = None
            found.prepare(self.shape, self.face_index, self.edge_index, **self.kwargs)
            self.analyzers[analyzer_id] = found
        return found


def _test_faults(message):
    """Fault injection for the engine's tests only (environment variables).

    DFM_PARALLEL_TEST_FAULT="crash|hang|raise:ANALYZER_ID:item", optionally
    once: DFM_PARALLEL_TEST_ONCE=<path of a file to create on the first fault>.
    """
    spec = os.environ.get("DFM_PARALLEL_TEST_FAULT")
    if not spec:
        return
    kind, analyzer_id, item = spec.split(":")
    if message["analyzer"] != analyzer_id or int(item) not in message["items"]:
        return
    once = os.environ.get("DFM_PARALLEL_TEST_ONCE")
    if once:
        if os.path.exists(once):
            return
        open(once, "w").close()
    if kind == "crash":
        os.abort()
    if kind == "hang":
        time.sleep(3600)
    if kind == "raise":
        message["analyzer"] = "NO_SUCH_ANALYZER"  # raises KeyError in run.analyzer()


def _watch_parent():
    """Exit if the parent (FreeCAD) goes away mid-batch (POSIX)."""
    parent = os.getppid()
    while True:
        time.sleep(2.0)
        if os.getppid() != parent:
            os._exit(0)


def main():
    proto_in = sys.stdin.buffer
    # Keep the real stdout for messages; anything else printed, by Python or
    # by OpenCASCADE's C++, goes to stderr instead of into the stream.
    proto_out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    hello = _read(proto_in)
    if not hello or hello.get("op") != "hello":
        return 2
    for path in reversed(hello.get("sys_path", [])):
        if path not in sys.path:
            sys.path.insert(0, path)
    _install_stand_ins(hello["freecad_dir"])
    if os.name == "posix":
        threading.Thread(target=_watch_parent, daemon=True).start()

    try:
        import OCP

        import freecad.DFM.core.analyzers  # noqa: F401  (registers every analyzer)
    except Exception:
        _write(proto_out, {"op": "error", "error": traceback.format_exc()})
        return 3
    _write(proto_out, {
        "op": "ready",
        "pid": os.getpid(),
        "ocp": os.path.realpath(getattr(OCP, "__file__", "") or ""),
        "python": list(sys.version_info[:3]),
    })

    run_id, run = None, None
    while True:
        message = _read(proto_in)
        if message is None or message.get("op") == "quit":
            return 0
        op = message.get("op")
        if op == "load":
            run_id, run = None, None
            try:
                run = _Run(message)
                run_id = message["run"]
                _write(proto_out, {"op": "loaded", "run": run_id})
            except Exception:
                _write(proto_out, {"op": "error", "run": message.get("run"),
                                   "error": traceback.format_exc()})
        elif op == "batch":
            job = message["job"]
            if run is None or message.get("run") != run_id:
                _write(proto_out, {"op": "failed", "job": job, "error": "shape not loaded",
                                   "unloaded": True})
                continue
            started = time.perf_counter()
            _test_faults(message)
            try:
                analyzer = run.analyzer(message["analyzer"])
                results, contributions = analyzer.run_batch(
                    message["items"], run.face_index, run.edge_index)
                _write(proto_out, {"op": "done", "job": job, "results": results,
                                   "contributions": contributions,
                                   "seconds": time.perf_counter() - started})
            except Exception:
                _write(proto_out, {"op": "failed", "job": job, "error": traceback.format_exc()})


if __name__ == "__main__":
    sys.exit(main())
