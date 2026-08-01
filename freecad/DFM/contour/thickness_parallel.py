# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


import os
import sys
import math
import importlib.util


_TOL = 1e-3


_BACKEND = None


def _init_worker(brep_path, backend_path, method, tol):
    # Load the backend directly from its file so the child never imports the
    # freecad.* package chain (and therefore never imports FreeCAD).
    global _BACKEND
    spec = importlib.util.spec_from_file_location("_dfm_thickness_backend", backend_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load backend from {backend_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from OCP.TopoDS import TopoDS_Shape
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools

    shape = TopoDS_Shape()
    BRepTools.Read_s(shape, brep_path, BRep_Builder())
    _BACKEND = module.make_backend(method, shape)


def _measure_chunk(chunk):
    backend = _BACKEND
    if backend is None:
        return [0.0] * len(chunk)
    out = []
    for centroid, outward, margin in chunk:
        try:
            out.append(backend.at(centroid, outward, margin))
        except Exception:
            out.append(0.0)
    return out


def _cpu_count():
    try:
        return max(1, os.cpu_count() or 1)
    except Exception:
        return 1


def _looks_safe():
    """spawn re-runs sys.executable; only allow it when that looks like a real
    Python, so we never risk launching the FreeCAD GUI as a "worker"."""
    exe = os.path.basename((sys.executable or "")).lower()
    return "python" in exe


def _pick_context():
    """Choose a multiprocessing context, or None if none is safe here.

    fork is preferred: the workers inherit the running interpreter (FreeCAD, OCP
    and this package are already imported), so there's no fresh import chain and
    no dependence on sys.executable. spawn is only used as a fallback, and only
    when the interpreter looks like a real Python.
    """
    import multiprocessing

    methods = multiprocessing.get_all_start_methods()
    if "fork" in methods:
        return multiprocessing.get_context("fork")
    if _looks_safe():
        return multiprocessing.get_context("spawn")
    return None


def _chunks(tasks, n_chunks):
    size = max(1, math.ceil(len(tasks) / max(1, n_chunks)))
    for i in range(0, len(tasks), size):
        yield i, tasks[i : i + size]


def _log(message):
    try:
        import FreeCAD as App  # only ever called in the main process

        App.Console.PrintMessage(f"DFM thickness: {message}\n")
    except Exception:
        print(f"DFM thickness: {message}")


def measure_thickness(
    part_shape,
    tasks,
    method,
    progress_cb=None,
    check_abort=None,
    tol=_TOL,
    workers=None,
    enable=True,
    min_tasks=2000,
):
    """Measure thickness for a list of (centroid, outward_normal, margin) tasks.

    Uses a process pool when it's available and worthwhile, otherwise measures
    serially. Returns one value per task, in order.
    """
    n = len(tasks)
    if n == 0:
        return []
    ctx = _pick_context()
    use_parallel = enable and n >= min_tasks and _cpu_count() > 1 and ctx is not None
    if use_parallel:
        try:
            w = workers or _cpu_count()
            _log(f"measuring {n} points on {w} workers ({ctx.get_start_method()}).")
            return _parallel(part_shape, tasks, method, progress_cb, check_abort, tol, workers, ctx)
        except Exception as exc:
            _log(f"parallel measuring failed, using serial. {exc}")
    else:
        reason = (
            "no fork/spawn context"
            if ctx is None
            else f"{n} points below the {min_tasks} threshold"
            if n < min_tasks
            else "single core"
        )
        _log(f"measuring {n} points serially ({reason}).")
    return _serial(part_shape, tasks, method, progress_cb, check_abort, tol)


def _serial(part_shape, tasks, method, progress_cb, check_abort, tol):
    from ..core.utils.conversion import freecad_to_ocp
    from .thickness_backend import make_backend

    backend = make_backend(method, freecad_to_ocp(part_shape))
    n = len(tasks)
    out = []
    for i, (centroid, outward, margin) in enumerate(tasks):
        if check_abort and (i & 0x3FF) == 0 and check_abort():
            break
        try:
            out.append(backend.at(centroid, outward, margin))
        except Exception:
            out.append(0.0)
        if progress_cb and (i & 0xFF) == 0:
            progress_cb(i, n)
    while len(out) < n:
        out.append(0.0)
    if progress_cb:
        progress_cb(n, n)
    return out


def _parallel(part_shape, tasks, method, progress_cb, check_abort, tol, workers, ctx):
    import sys
    import tempfile
    from concurrent.futures import ProcessPoolExecutor, as_completed

    workers = workers or _cpu_count()
    backend_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thickness_backend.py")

    fd, brep_path = tempfile.mkstemp(suffix=".brep")
    os.close(fd)
    part_shape.exportBrep(brep_path)

    n = len(tasks)
    results = [0.0] * n
    executor = None

    # FreeCAD replaces sys.stdin with a console object that has no close(), and a
    # forked child calls sys.stdin.close() while booting -> AttributeError kills
    # every worker. Give the children a real stdin to close while they fork.
    saved_stdin = sys.stdin
    devnull = None
    try:
        try:
            devnull = open(os.devnull)
            sys.stdin = devnull
        except Exception:
            pass

        executor = ProcessPoolExecutor(
            max_workers=workers,
            mp_context=ctx,
            initializer=_init_worker,
            initargs=(brep_path, backend_path, method, tol),  # type: ignore[arg-type]
        )
        # Smoke-test one tiny job so a broken environment fails fast (and within
        # a timeout) instead of hanging, then we fall back to serial.
        probe = executor.submit(_measure_chunk, [tasks[0]])
        results[0] = probe.result(timeout=60)[0]

        futures = {}
        for start, chunk in _chunks(tasks, workers * 8):
            futures[executor.submit(_measure_chunk, chunk)] = start

        done = 0
        for fut in as_completed(futures):
            start = futures[fut]
            vals = fut.result()
            results[start : start + len(vals)] = vals
            done += len(vals)
            if progress_cb:
                progress_cb(min(done, n), n)
            if check_abort and check_abort():
                break
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        sys.stdin = saved_stdin
        if devnull is not None:
            try:
                devnull.close()
            except Exception:
                pass
        try:
            os.remove(brep_path)
        except OSError:
            pass
    if progress_cb:
        progress_cb(n, n)
    return results
