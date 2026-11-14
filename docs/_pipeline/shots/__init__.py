# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Screenshots of the workbench, made by code.

A shot is a function that sets the workbench up and returns the image. Two
engines run them:

* ``panel`` shots (panels.py) build a Qt panel offscreen with the test
  suite's FreeCAD stand-ins. Fast, and no FreeCAD needed.
* ``scene`` shots (scenes.py) run inside a real FreeCAD on a virtual display
  (Xvfb), for anything with the 3D view.

Each shot is written to docs/content/assets/shots/<id>.png. Pages use them as
``![...](../assets/shots/<id>.png)``. engine.py decides when to re-run them.

A scene shot can also make other files for the site (the sample PDF report):
it lists them in ``files`` (paths under docs/content) and writes each one to
``ctx.file(<path>)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

#: Where shots are written, relative to docs/content.
FOLDER = "assets/shots"


@dataclass(frozen=True)
class Shot:
    id: str
    engine: str  # "panel" or "scene"
    fn: Callable
    about: str  # one line: what it shows (and the image's alt text)
    files: tuple[str, ...] = ()  # other files it makes, relative to docs/content


REGISTRY: dict[str, Shot] = {}


def shot(id: str, engine: str, about: str, files: tuple[str, ...] = ()):
    """Register the decorated function as the shot *id*."""

    def decorator(fn):
        if id in REGISTRY:
            raise ValueError(f"two shots are called {id!r}")
        if files and engine != "scene":
            raise ValueError(f"shot {id!r}: only scene shots can make files")
        REGISTRY[id] = Shot(id=id, engine=engine, fn=fn, about=about, files=tuple(files))
        return fn

    return decorator


def path(shot_id: str) -> str:
    return f"{FOLDER}/{shot_id}.png"


def files() -> list[str]:
    """Every extra file the shots make, relative to docs/content."""
    return sorted(f for s in REGISTRY.values() for f in s.files)
