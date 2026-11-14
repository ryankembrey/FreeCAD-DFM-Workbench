# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The parts the docs show: docs/models/models.yaml.

Read by the build (which checks it before any screenshot is taken) and by the
scene shots inside FreeCAD. Only the standard library and PyYAML, so it loads
in both.
"""

from __future__ import annotations

import datetime as _dt
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

FOLDER = Path(__file__).resolve().parent.parent / "models"
CONFIG = FOLDER / "models.yaml"
REPO = FOLDER.parent.parent

#: The processes the two parts are analysed with.
PROCESS = {"molded": "Plastic Injection Molding", "printed": "FDM"}
_SUFFIXES = {".step", ".stp", ".fcstd"}


@dataclass
class Part:
    key: str  # "molded" or "printed"
    path: Path
    label: str
    material: str
    object: str = ""  # for an .FCStd: the object's Label or Name
    options: dict = field(default_factory=dict)

    @property
    def process(self) -> str:
        return PROCESS[self.key]

    def direction(self, name: str) -> tuple[float, float, float]:
        x, y, z = self.options[name]
        return float(x), float(y), float(z)


@dataclass
class Report:
    part: str
    date: _dt.date
    settings: dict


@dataclass
class Models:
    parts: dict[str, Part]
    report: Report

    @property
    def molded(self) -> Part:
        return self.parts["molded"]

    @property
    def printed(self) -> Part:
        return self.parts["printed"]

    def files(self) -> list[Path]:
        """Everything a change to should retake the pictures: this file and the parts."""
        return [CONFIG] + [p.path for p in self.parts.values()]


def load(path: Path = CONFIG) -> tuple[Models | None, list[str]]:
    """The config and its problems. Problems that need the workbench (materials,
    report options) are checked by ``check_against``."""
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return None, [f"can't read it: {exc}"]
    errors: list[str] = []
    parts = {}
    for key in PROCESS:
        entry = raw.get(key)
        if not isinstance(entry, dict):
            errors.append(f"`{key}:` is missing")
            continue
        part = _part(key, entry, path.parent, errors)
        if part is not None:
            parts[key] = part

    rep = raw.get("report") or {}
    report_part = rep.get("part", "molded")
    if report_part not in PROCESS:
        errors.append(f"report: `part: {report_part}` must be one of {', '.join(PROCESS)}")
    date = rep.get("date", _dt.date(2026, 1, 1))
    if not isinstance(date, _dt.date):
        errors.append(f"report: `date: {date}` isn't a date (write it as 2026-11-14)")
        date = _dt.date(2026, 1, 1)
    settings = rep.get("settings") or {}
    if not isinstance(settings, dict):
        errors.append("report: `settings:` must be a list of `name: value` lines")
        settings = {}
    unknown = sorted(set(raw) - set(PROCESS) - {"report"})
    if unknown:
        errors.append(f"unknown section(s): {', '.join(unknown)}")
    if errors:
        return None, errors
    return Models(parts, Report(report_part, date, settings)), []


def _part(key: str, entry: dict, folder: Path, errors: list[str]) -> Part | None:
    def need(name):
        value = entry.get(name)
        if value in (None, ""):
            errors.append(f"{key}: `{name}:` is missing")
        return value

    rel, label, material = need("part"), need("label"), need("material")
    if rel is None or label is None or material is None:
        return None
    path = (folder / str(rel)).resolve()
    if not path.is_file():
        errors.append(f"{key}: the part {rel!r} isn't in docs/models/ (looked for {path})")
        return None
    if path.suffix.lower() not in _SUFFIXES:
        errors.append(f"{key}: {rel!r} isn't a STEP or FCStd file")
    try:
        path.relative_to(REPO)
    except ValueError:
        errors.append(f"{key}: {rel!r} is outside the repository; CI couldn't open it")
    else:
        if _git_ignores(path):
            errors.append(f"{key}: git ignores {rel!r}, so it wouldn't be committed and CI couldn't "
                          "open it. Keep the parts in docs/models/ (not ignored).")
    obj = str(entry.get("object", "") or "")
    if path.suffix.lower() == ".fcstd" and not obj:
        errors.append(f"{key}: {rel!r} is a FreeCAD document; say which `object:` in it to use")

    options = {k: v for k, v in entry.items() if k not in ("part", "label", "material", "object")}
    directions = {"molded": ("pull_direction",), "printed": ("print_direction",)}[key]
    numbers = {"molded": (), "printed": ("nozzle_diameter", "layer_height")}[key]
    for name in directions:
        v = options.get(name)
        if not (isinstance(v, list) and len(v) == 3 and all(isinstance(c, (int, float)) for c in v)
                and any(v)):
            errors.append(f"{key}: `{name}:` must be a direction like [0, 0, 1]")
    for name in numbers:
        v = options.get(name)
        if not isinstance(v, (int, float)) or v <= 0:
            errors.append(f"{key}: `{name}:` must be a number above 0")
    rng = options.get("thickness_range")
    if rng is not None and not (isinstance(rng, list) and len(rng) == 2 and rng[0] < rng[1]):
        errors.append(f"{key}: `thickness_range:` must be [low, high] in mm")
    known = {"molded": {"pull_direction", "thickness_range", "history_first_material"},
             "printed": {"print_direction", "nozzle_diameter", "layer_height"}}[key]
    for name in sorted(set(options) - known):
        errors.append(f"{key}: unknown setting `{name}:`")
    return Part(key, path, str(label), str(material), obj, options)


def _git_ignores(path: Path) -> bool:
    try:
        r = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=REPO,
                           capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def check_against(models: Models, processes, settings_fields: set[str]) -> list[str]:
    """Problems only the workbench can see: materials the process doesn't have,
    report options that don't exist."""
    errors = []
    by_name = {p.name: p for p in processes}
    for part in models.parts.values():
        proc = by_name.get(part.process)
        if proc is None:
            errors.append(f"{part.key}: no built-in process called {part.process!r}")
            continue
        names = proc.material_names()
        materials = [part.material]
        if part.options.get("history_first_material"):
            materials.append(part.options["history_first_material"])
        for m in materials:
            if m not in names:
                errors.append(f"{part.key}: {part.process} has no material {m!r}. It has: "
                              + ", ".join(n for n in names if n != "Default"))
    for name in sorted(set(models.report.settings) - settings_fields):
        errors.append(f"report: no report option `{name}` (see ReportSettings in "
                      "freecad/DFM/gui/report/settings.py)")
    return errors
