# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Taking the screenshots: when, and how.

Shots are re-taken only when something they could show has changed: the
workbench's code and resources, the example parts, the shot code itself or
the pinned environment (pixi.lock). A fingerprint of all of that is kept in
docs/_data/shots.json. When it matches and every image is there, nothing runs.

They are taken in FreeCAD from the pixi environment, under Xvfb, with a
throwaway FreeCAD user folder (nobody's own settings or saved processes) and
a private font configuration (the environment's fonts only). So the same
code gives the same pictures on any machine.

A new image replaces the old one only if it visibly differs, so rendering
noise doesn't turn into commits.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import env
from . import REGISTRY, path

MANIFEST = "_data/shots.json"  # relative to docs/
#: A pixel counts as changed past this difference (0-255) in any channel...
PIXEL_TOLERANCE = 40
#: ...and an image as changed when more than this many pixels did. Small on
#: purpose: a changed word in a label is a few hundred pixels and must count;
#: the renderer is deterministic, so real noise is rare.
CHANGED_PIXELS = 50

_FINGERPRINT_DIRS = ("freecad/DFM", "resources", "docs/_pipeline/shots")
_FINGERPRINT_SUFFIXES = {".py", ".yaml", ".ui", ".svg", ".qrc", ".stp", ".step", ".STEP", ".FCStd", ".png"}


def fingerprint() -> str:
    h = hashlib.sha256()
    files = []
    for rel in _FINGERPRINT_DIRS:
        for p in sorted((env.REPO / rel).rglob("*")):
            if p.is_file() and p.suffix in _FINGERPRINT_SUFFIXES and "__pycache__" not in p.parts:
                files.append(p)
    lock = env.REPO / "pixi.lock"
    if lock.exists():
        files.append(lock)
    for p in files:
        h.update(str(p.relative_to(env.REPO)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def _load_manifest() -> dict:
    p = env.DOCS / MANIFEST
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _images_differ(old: bytes, new: bytes) -> bool:
    import numpy as np
    from PySide6 import QtGui

    a, b = QtGui.QImage(), QtGui.QImage()
    a.loadFromData(old)
    b.loadFromData(new)
    if a.isNull() or b.isNull() or a.size() != b.size():
        return True
    fmt = QtGui.QImage.Format.Format_RGBA8888
    a, b = a.convertToFormat(fmt), b.convertToFormat(fmt)

    def array(img):
        return np.frombuffer(img.constBits(), np.uint8, img.sizeInBytes()).reshape(img.height(), img.bytesPerLine())

    diff = np.abs(array(a).astype(np.int16) - array(b).astype(np.int16))
    changed = int((diff > PIXEL_TOLERANCE).reshape(a.height(), -1, 4).any(axis=2).sum())
    return changed > CHANGED_PIXELS


def _fonts_conf(tmp: Path) -> Path | None:
    """A fontconfig file that sees only the environment's fonts."""
    prefix = os.environ.get("CONDA_PREFIX")
    if not prefix or not (Path(prefix) / "fonts").is_dir():
        return None
    conf = tmp / "fonts.conf"
    aliases = "".join(
        f"<alias><family>{name}</family><prefer><family>{font}</family></prefer></alias>"
        for name, font in (("sans-serif", "Ubuntu"), ("Sans Serif", "Ubuntu"), ("Sans", "Ubuntu"),
                           ("serif", "DejaVu Serif"), ("monospace", "Ubuntu Mono"))
    )
    conf.write_text(
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f"<dir>{prefix}/fonts</dir><cachedir>{tmp}/fc-cache</cachedir>{aliases}</fontconfig>",
        encoding="utf-8",
    )
    return conf


def _missing_tools() -> list[str]:
    missing = []
    if not os.environ.get("CONDA_PREFIX") or not shutil.which("freecad"):
        missing.append("FreeCAD from the pixi environment (run through `pixi run docs`)")
    if not shutil.which("xvfb-run"):
        missing.append("xvfb-run (Debian/Ubuntu: `sudo apt install xvfb xauth`; "
                       "Fedora: `sudo dnf install xorg-x11-server-Xvfb xauth`)")
    return missing


def take(ids: list[str], timeout: int = 3600) -> tuple[dict[str, bytes], dict[str, str]]:
    """Run FreeCAD and take *ids*; returns ({id: png bytes}, {id: error})."""
    with tempfile.TemporaryDirectory(prefix="dfm-shots-") as tmp:
        tmp = Path(tmp)
        out = tmp / "out"
        home = tmp / "freecad-home"
        out.mkdir()
        home.mkdir()
        e = dict(os.environ)
        e.update({
            "DFM_REPO": str(env.REPO),
            "DFM_SHOTS_OUT": str(out),
            "DFM_SHOTS_IDS": ",".join(ids),
            "FREECAD_USER_HOME": str(home),
            "LC_ALL": "C.UTF-8",
            "PYTHONPATH": "",
            "PYTHONNOUSERSITE": "1",
            # One meshing thread: gmsh's parallel meshing isn't repeatable, and the
            # contour pictures would differ by a few pixels on every run.
            "OMP_NUM_THREADS": "1",
        })
        # FreeCAD draws on Xvfb's X display, never on your desktop: on a Wayland
        # session (Sway, GNOME...) Qt would otherwise pick Wayland and open the
        # window there, where the compositor sizes it and the pictures change.
        e["QT_QPA_PLATFORM"] = "xcb"
        e["XDG_SESSION_TYPE"] = "x11"
        for var in ("WAYLAND_DISPLAY", "WAYLAND_SOCKET", "QT_WAYLAND_DISABLE_WINDOWDECORATION",
                    "QT_SCALE_FACTOR", "QT_SCREEN_SCALE_FACTORS", "QT_AUTO_SCREEN_SCALE_FACTOR",
                    "QT_ENABLE_HIGHDPI_SCALING", "QT_FONT_DPI", "QT_STYLE_OVERRIDE", "QT_QPA_PLATFORMTHEME"):
            e.pop(var, None)
        conf = _fonts_conf(tmp)
        if conf:
            e["FONTCONFIG_FILE"] = str(conf)
        runner = Path(__file__).with_name("freecad_runner.py")
        cmd = ["xvfb-run", "-a", "-s", "-screen 0 1920x1200x24", shutil.which("freecad"), str(runner)]
        proc = subprocess.run(cmd, env=e, capture_output=True, text=True, timeout=timeout)
        try:
            results = json.loads((out / "results.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            tail = (proc.stdout + proc.stderr)[-3000:]
            return {}, {i: "FreeCAD stopped before writing results:\n" + tail for i in ids}
        images, errors = {}, {}
        for i in ids:
            status = results.get(i, "not taken")
            if status == "ok":
                images[i] = (out / f"{i}.png").read_bytes()
            else:
                errors[i] = status
        if "_runner" in results:
            errors["_runner"] = results["_runner"]
        return images, errors


def run(out, check: bool, force: bool = False) -> list[str]:
    """Bring the screenshots up to date (or, with *check*, only say whether they are).
    Returns notes to print."""
    ids = sorted(REGISTRY)
    current = fingerprint()
    manifest = _load_manifest()
    present = all((env.CONTENT / path(i)).exists() for i in ids)
    fresh = manifest.get("fingerprint") == current and manifest.get("shots") == ids and present

    def keep_all():
        for i in ids:
            if (env.CONTENT / path(i)).exists():
                out.keep("content/" + path(i))
        out.keep(MANIFEST)

    if (fresh and not force) or check:
        keep_all()
        if check and not fresh:
            return ["Screenshots are older than the code. `pixi run docs` retakes them "
                    "(CI retakes them for the site either way)."]
        return []

    missing = _missing_tools()
    if missing:
        keep_all()
        return ["Screenshots not retaken: this needs " + " and ".join(missing) + "."]

    print(f"Taking {len(ids)} screenshots in FreeCAD (a few minutes)...", flush=True)
    images, errors = take(ids)
    notes = []
    for i in ids:
        rel = "content/" + path(i)
        if i in images:
            old = (env.CONTENT / path(i))
            if old.exists() and not _images_differ(old.read_bytes(), images[i]):
                out.keep(rel)
            else:
                out.write(rel, images[i])
        else:
            if (env.CONTENT / path(i)).exists():
                out.keep(rel)  # keep the last good one
            notes.append(f"Screenshot {i} failed:\n{errors.get(i, '')}")
    if "_runner" in errors:
        notes.append("The screenshot runner failed:\n" + errors["_runner"])
    if not errors:
        out.write(MANIFEST, json.dumps({"fingerprint": current, "shots": ids}, indent=1) + "\n")
    else:
        out.keep(MANIFEST)
    return notes
