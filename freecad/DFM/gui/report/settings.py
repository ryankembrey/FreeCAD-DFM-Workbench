# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Options for the PDF report, and where they are remembered.

Every option is a field of ``ReportSettings``. Its preference entry is
``Report`` followed by the field name in CamelCase (``rule_diagrams`` is
stored as ``ReportRuleDiagrams``), in the DFM preference group. The
Reports preference page and the Export PDF report dialog both read and
write through ``load()`` and ``save()``, so they always agree.

Fields marked ``persist=False`` belong to one document (project, revision,
notes) and are never written to the preferences.
"""

from dataclasses import dataclass, field, fields, replace

from ...app.prefs import dfm_params


def _opt(default, persist: bool = True):
    return field(default=default, metadata={"persist": persist})


# Choices for the options with a fixed set of values: key -> [(value, label)].
CHOICES: dict[str, list[tuple[str, str]]] = {
    "overview_view": [
        ("isometric", "Isometric"),
        ("current", "Current view"),
        ("front", "Front"),
        ("top", "Top"),
        ("right", "Right"),
    ],
    "group_by": [
        ("criticality", "Criticality, then rule"),
        ("severity", "Severity, then rule"),
        ("rule", "Rule name"),
    ],
    "image_mode": [
        ("none", "No viewport images"),
        ("per_rule", "One per rule"),
        ("per_finding", "One per finding"),
    ],
    "image_findings": [
        ("errors", "Errors only"),
        ("errors_warnings", "Errors and warnings"),
        ("all", "All findings, including ignored"),
    ],
    "framing": [
        ("zoom", "Zoom to the highlighted faces"),
        ("model", "Whole model"),
    ],
    "view_direction": [
        ("best", "Best view for each finding"),
        ("isometric", "Isometric"),
        ("current", "Current view"),
    ],
    "resolution": [
        ("low", "Low (96 dpi)"),
        ("medium", "Medium (150 dpi)"),
        ("high", "High (300 dpi)"),
    ],
    "background": [
        ("white", "White"),
        ("viewport", "Viewport background"),
    ],
}

RESOLUTION_DPI = {"low": 96, "medium": 150, "high": 300}


@dataclass
class ReportSettings:
    """Everything that decides what goes into a PDF report."""

    # Cover page
    title: str = _opt("DFM report: {model}")
    project: str = _opt("", persist=False)
    prepared_by: str = _opt("")
    revision: str = _opt("", persist=False)
    notes: str = _opt("", persist=False)
    show_notes: bool = _opt(False)
    overview_image: bool = _opt(True)
    overview_view: str = _opt("isometric")
    design_image: bool = _opt(True)  # the model as designed, beside the overview
    summary_table: bool = _opt(True)

    # Findings
    include_errors: bool = _opt(True)
    include_warnings: bool = _opt(True)
    include_passed: bool = _opt(True)
    include_ignored: bool = _opt(False)
    group_by: str = _opt("criticality")
    rule_diagrams: bool = _opt(True)
    feedback_messages: bool = _opt(True)
    target_and_limit: bool = _opt(True)
    settings_appendix: bool = _opt(False)
    list_at_most: int = _opt(25)
    findings_appendix: bool = _opt(True)

    # Viewport images
    image_mode: str = _opt("per_rule")
    image_findings: str = _opt("errors_warnings")
    images_per_rule: int = _opt(6)
    framing: str = _opt("zoom")
    view_direction: str = _opt("best")
    fade_rest: bool = _opt(True)
    see_through: int = _opt(50)  # % transparency for overview and per-rule images
    hide_markers: bool = _opt(True)
    resolution: str = _opt("medium")
    background: str = _opt("white")

    # Layout and branding
    template: str = _opt("Standard")
    company_name: str = _opt("")
    company_logo: str = _opt("")
    show_company_logo: bool = _opt(True)
    show_dfm_logo: bool = _opt(True)
    show_freecad_logo: bool = _opt(False)

    # File
    file_name: str = _opt("{document}_{material}_DFM_{date}")
    open_after: bool = _opt(True)

    # ------------------------------------------------------------------

    @classmethod
    def load(cls, params=None) -> "ReportSettings":
        """Settings from the preferences, with defaults for anything unset."""
        params = params or dfm_params()
        values = {}
        for f in fields(cls):
            if not f.metadata.get("persist", True):
                continue
            key = pref_key(f.name)
            if isinstance(f.default, bool):
                values[f.name] = params.GetBool(key, f.default)
            elif isinstance(f.default, int):
                values[f.name] = params.GetInt(key, f.default)
            else:
                value = params.GetString(key, f.default)
                valid = CHOICES.get(f.name)
                if valid and value not in {v for v, _ in valid}:
                    value = f.default
                values[f.name] = value
        return cls(**values)

    def save(self, params=None) -> None:
        """Write every persisted option to the preferences."""
        params = params or dfm_params()
        for f in fields(self):
            if not f.metadata.get("persist", True):
                continue
            key, value = pref_key(f.name), getattr(self, f.name)
            if isinstance(f.default, bool):
                params.SetBool(key, bool(value))
            elif isinstance(f.default, int):
                params.SetInt(key, int(value))
            else:
                params.SetString(key, str(value))

    def copy(self, **changes) -> "ReportSettings":
        return replace(self, **changes)

    @property
    def image_dpi(self) -> int:
        return RESOLUTION_DPI.get(self.resolution, 150)


def pref_key(name: str) -> str:
    """Preference entry for a settings field: ``rule_diagrams`` -> ``ReportRuleDiagrams``."""
    return "Report" + "".join(part.capitalize() for part in name.split("_"))


def defaults() -> ReportSettings:
    """The built-in defaults, ignoring the preferences."""
    return ReportSettings()


def choice_label(name: str, value: str) -> str:
    return dict(CHOICES.get(name, ())).get(value, value)
