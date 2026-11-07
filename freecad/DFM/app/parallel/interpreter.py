# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Finding a plain Python to run the worker processes.

Inside FreeCAD, ``sys.executable`` is FreeCAD itself, so it can't run a
worker. FreeCAD ships (or runs on) a Python interpreter of the same version,
though: next to FreeCAD.exe on Windows, in the app bundle on macOS, in the
AppImage, Flatpak or Snap, or the system one for distribution packages and
source builds. The candidates are tried in turn, each by actually running it:
it must be the same Python version as FreeCAD's and load the very same OCP
build FreeCAD's DFM uses, with FreeCAD's module path. The first that passes is
used for the rest of the session.

Set the DFM preference "ParallelPython" (Tools > Edit parameters, under
BaseApp/Preferences/Mod/DFM) to a path to use that interpreter instead.
"""

import json
import os
import shutil
import subprocess
import sys
from typing import Optional

_PROBE = r"""
import json, os, sys
paths = json.loads(sys.stdin.read())
for p in reversed(paths):
    if p not in sys.path:
        sys.path.insert(0, p)
out = {"version": list(sys.version_info[:2])}
try:
    import OCP
    out["ocp"] = os.path.realpath(getattr(OCP, "__file__", "") or "")
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    BRepPrimAPI_MakeBox(1.0, 1.0, 1.0).Shape()
except Exception as e:
    out["error"] = type(e).__name__ + ": " + str(e)
sys.stdout.write("DFMPROBE" + json.dumps(out) + "\n")
"""

# Interpreter flags: isolated, so neither PYTHONPATH/PYTHONHOME nor the user
# site of whoever runs FreeCAD can change what the worker imports; the module
# path is passed in explicitly.
FLAGS = ["-I"]

_found: Optional[str] = None
_reason = ""
_searched = False


def creation_flags() -> int:
    """No console window per worker on Windows."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def worker_env() -> dict:
    env = dict(os.environ)
    # Never let a worker think it is FreeCAD's GUI, or open a display.
    env.pop("PYTHONSTARTUP", None)
    env["QT_QPA_PLATFORM"] = "offscreen"
    return env


def module_path() -> list:
    """FreeCAD's module path, for the workers (they run isolated)."""
    return [p for p in sys.path if p and os.path.isdir(p)]


def _candidates() -> list:
    major, minor = sys.version_info[:2]
    names = [f"python{major}.{minor}", f"python{major}", "python"]
    if os.name == "nt":
        names = ["python.exe", f"python{major}{minor}.exe", f"python{major}.exe"]

    dirs = []
    exe_dir = os.path.dirname(os.path.realpath(sys.executable or ""))
    for base in (exe_dir, sys.prefix, sys.exec_prefix, getattr(sys, "base_prefix", "")):
        if base:
            dirs += [base, os.path.join(base, "bin"), os.path.join(base, "Scripts")]
    if sys.platform == "darwin" and exe_dir:
        dirs.append(os.path.join(os.path.dirname(exe_dir), "Resources", "bin"))

    out = []
    custom = _preferred()
    if custom:
        out.append(custom)
    base_exe = getattr(sys, "_base_executable", "")
    if base_exe and "python" in os.path.basename(base_exe).lower():
        out.append(base_exe)
    for d in dirs:
        for n in names:
            out.append(os.path.join(d, n))
    for n in names:
        found = shutil.which(n)
        if found:
            out.append(found)

    seen, unique = set(), []
    freecad = os.path.realpath(sys.executable or "")
    for path in out:
        if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
            continue
        real = os.path.realpath(path)
        if real in seen or real == freecad:
            continue
        seen.add(real)
        unique.append(path)
    return unique


def _preferred() -> str:
    try:
        from ..prefs import dfm_params

        return dfm_params().GetString("ParallelPython", "").strip()
    except Exception:
        return ""


def _parent_ocp() -> str:
    try:
        import OCP

        return os.path.realpath(getattr(OCP, "__file__", "") or "")
    except Exception:
        return ""


def probe(python: str, timeout: float = 60.0) -> tuple:
    """(ok, reason) for running workers with *python*."""
    try:
        done = subprocess.run(
            [python, *FLAGS, "-c", _PROBE],
            input=json.dumps(module_path()),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=worker_env(),
            creationflags=creation_flags(),
        )
    except Exception as exc:
        return False, f"{python}: {exc}"
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("DFMPROBE")), None)
    if line is None:
        tail = (done.stderr or "").strip().splitlines()[-1:] or ["no output"]
        return False, f"{python}: {tail[0]}"
    info = json.loads(line[len("DFMPROBE"):])
    if tuple(info.get("version", ())) != tuple(sys.version_info[:2]):
        return False, f"{python}: Python {info.get('version')} is not FreeCAD's {sys.version_info[:2]}"
    if "error" in info:
        return False, f"{python}: {info['error']}"
    parent = _parent_ocp()
    if parent and info.get("ocp") != parent:
        return False, f"{python}: loads a different OCP ({info.get('ocp')})"
    return True, f"{python}"


def find_worker_python() -> tuple:
    """(path or None, reason). Searched once per session."""
    global _found, _reason, _searched
    if _searched:
        return _found, _reason
    reasons = []
    for candidate in _candidates():
        ok, why = probe(candidate)
        if ok:
            _found, _reason, _searched = candidate, why, True
            return _found, _reason
        reasons.append(why)
    _found, _searched = None, True
    _reason = "no usable Python found" + (": " + "; ".join(reasons) if reasons else "")
    return _found, _reason


def forget() -> None:
    """Search again next time (after the preference changes)."""
    global _found, _reason, _searched
    _found, _reason, _searched = None, "", False
