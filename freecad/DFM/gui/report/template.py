# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Report templates: page size, style and header and footer text.

Three templates are built in. Users can add their own as small YAML files
in the template folder (Preferences > DFM > Reports), so a team can share
one look. A template file only needs the keys it changes, for example::

    name: Acme
    page_size: Letter
    accent: "#0b5394"
    footer_text: "Acme confidential · {project} · Rev {revision}"

Header and footer text can use these placeholders: {title}, {project},
{revision}, {document}, {model}, {process}, {material}, {date},
{company} and {prepared_by}. Parts of the text separated by " · " are
left out when all of their placeholders are empty.
"""

import os
import re
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

import FreeCAD as App  # type: ignore

from ...app.prefs import dfm_params

# Page sizes in points (1/72 inch), portrait.
PAGE_SIZES: dict[str, tuple[float, float]] = {
    "A4": (595.28, 841.89),
    "A3": (841.89, 1190.55),
    "Letter": (612.0, 792.0),
    "Legal": (612.0, 1008.0),
}

TEMPLATE_FOLDER_PREF = "ReportTemplateFolder"


@dataclass
class ReportTemplate:
    """How a report looks. Content options live in ``ReportSettings``."""

    name: str = "Standard"
    page_size: str = "A4"
    orientation: str = "portrait"  # portrait | landscape
    margin_mm: float = 16.0
    accent: str = "#1B2A4A"
    font_family: str = ""  # empty: the application font
    font_scale: float = 1.0
    images_per_row: int = 2
    diagram_width_mm: float = 105.0
    rule_page_break: bool = True  # start each rule on a new page
    header_text: str = "{title}"
    footer_text: str = "{company} · {project} · Rev {revision}"

    # Not stored in the file.
    builtin: bool = field(default=False, compare=False, metadata={"stored": False})
    path: str = field(default="", compare=False, metadata={"stored": False})

    @property
    def page_points(self) -> tuple[float, float]:
        w, h = PAGE_SIZES.get(self.page_size, PAGE_SIZES["A4"])
        return (h, w) if self.orientation == "landscape" else (w, h)

    @property
    def margin(self) -> float:
        return self.margin_mm * 72.0 / 25.4

    def copy(self, **changes) -> "ReportTemplate":
        return replace(self, **changes)

    def same_look(self, other: "ReportTemplate") -> bool:
        """True when both templates would produce the same pages."""
        return self.to_dict() == other.to_dict() | {"name": self.name}

    def to_dict(self) -> dict:
        data = asdict(self)
        for f in fields(self):
            if not f.metadata.get("stored", True):
                data.pop(f.name, None)
        return data

    @classmethod
    def from_dict(cls, data: dict, **extra) -> "ReportTemplate":
        known = {f.name: f for f in fields(cls) if f.metadata.get("stored", True)}
        values = {}
        for key, value in (data or {}).items():
            f = known.get(key)
            if f is None:
                continue
            try:
                if isinstance(f.default, bool):
                    value = bool(value)
                elif isinstance(f.default, int):
                    value = int(value)
                elif isinstance(f.default, float):
                    value = float(value)
                else:
                    value = str(value)
            except (TypeError, ValueError):
                continue
            values[key] = value
        template = cls(**values, **extra)
        if template.page_size not in PAGE_SIZES:
            template.page_size = "A4"
        if template.orientation not in ("portrait", "landscape"):
            template.orientation = "portrait"
        template.images_per_row = min(max(template.images_per_row, 1), 4)
        template.font_scale = min(max(template.font_scale, 0.7), 1.4)
        return template


BUILTIN_TEMPLATES: dict[str, ReportTemplate] = {
    "Standard": ReportTemplate(name="Standard", builtin=True),
    "Compact": ReportTemplate(
        name="Compact",
        margin_mm=12.0,
        font_scale=0.9,
        images_per_row=3,
        diagram_width_mm=80.0,
        rule_page_break=False,
        builtin=True,
    ),
    "Detailed": ReportTemplate(
        name="Detailed",
        margin_mm=18.0,
        font_scale=1.05,
        images_per_row=1,
        diagram_width_mm=120.0,
        builtin=True,
    ),
}


# ---------------------------------------------------------------------------
# User templates
# ---------------------------------------------------------------------------


def default_template_folder() -> Path:
    return Path(App.getUserAppDataDir()) / "DFM" / "report_templates"


def template_folder() -> Path:
    folder = dfm_params().GetString(TEMPLATE_FOLDER_PREF, "")
    return Path(folder).expanduser() if folder else default_template_folder()


def _yaml():
    import yaml  # PyYAML ships with FreeCAD

    return yaml


def load_template_file(path: str | os.PathLike) -> ReportTemplate:
    """Read a template YAML file. Raises ValueError when it can't be read."""
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as fh:
            data = _yaml().safe_load(fh) or {}
    except Exception as exc:  # unreadable file or bad YAML
        raise ValueError(f"Could not read {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is not a report template.")
    data.setdefault("name", path.stem)
    return ReportTemplate.from_dict(data, path=str(path))


def user_templates() -> dict[str, ReportTemplate]:
    folder = template_folder()
    found: dict[str, ReportTemplate] = {}
    if not folder.is_dir():
        return found
    for path in sorted(folder.glob("*.y*ml")):
        try:
            template = load_template_file(path)
        except ValueError as exc:
            App.Console.PrintWarning(f"DFM: {exc}\n")
            continue
        if template.name in BUILTIN_TEMPLATES:
            template.name = f"{template.name} ({path.stem})"
        found[template.name] = template
    return found


def all_templates() -> dict[str, ReportTemplate]:
    """Built-in templates first, then the user's, by name."""
    return {**BUILTIN_TEMPLATES, **user_templates()}


def get_template(name: str) -> ReportTemplate:
    """The template called *name*, or Standard when there is none."""
    return all_templates().get(name, BUILTIN_TEMPLATES["Standard"]).copy()


def _file_name(name: str) -> str:
    stem = re.sub(r"[^\w\- ]+", "", name).strip().replace(" ", "_") or "template"
    return stem + ".yaml"


def save_template(template: ReportTemplate, name: str) -> ReportTemplate:
    """Save *template* under *name* in the template folder and return the saved copy."""
    folder = template_folder()
    folder.mkdir(parents=True, exist_ok=True)
    saved = template.copy(name=name, builtin=False)
    path = folder / _file_name(name)
    data = saved.to_dict()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# DFM report template\n")
        _yaml().safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    saved.path = str(path)
    return saved


def import_template(source: str | os.PathLike) -> ReportTemplate:
    """Copy a template file into the template folder. Raises ValueError when invalid."""
    template = load_template_file(source)
    name = template.name
    if name in BUILTIN_TEMPLATES:
        name = f"{name} (imported)"
    return save_template(template, name)


# ---------------------------------------------------------------------------
# Placeholder text
# ---------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
SEPARATOR = " · "


def fill_text(text: str, values: dict[str, str]) -> str:
    """Fill {placeholders}; drop " · " parts whose placeholders are all empty."""
    parts = text.replace(" · ", SEPARATOR).split(SEPARATOR)
    kept = []
    for part in parts:
        names = _PLACEHOLDER.findall(part)
        filled = _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), "") or ""), part)
        if names and not any(values.get(n) for n in names):
            continue
        if filled.strip():
            kept.append(filled.strip())
    return SEPARATOR.join(kept)


def file_name_from_pattern(pattern: str, values: dict[str, str]) -> str:
    """A safe file name from a pattern such as "{document}_{material}_DFM_{date}"."""
    name = _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), "") or ""), pattern)
    name = re.sub(r'[\\/:*?"<>|]+', "-", name)
    name = re.sub(r"_{2,}", "_", name).strip(" _-.") or "DFM_report"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name
