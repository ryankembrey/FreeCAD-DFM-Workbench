# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The process pages, the processes index and the reference landing page."""

from __future__ import annotations

from . import md
from .pages_citations import row_source

TO_CONTENT = "../../"

#: What the analysis setup asks for, in words for the docs.
INPUTS = {
    "PULL_DIRECTION": ("Pull direction", "the direction the mold opens. Pick a face or edge, or an axis."),
    "SIDE_ACTIONS": ("Side actions", "extra directions that slides or lifters move in, if the mold has any."),
    "NEUTRAL_PLANE": ("Neutral plane", "the plane the part's draft is measured from."),
    "PRINT_ORIENTATION": ("Print direction", "which way is up on the printer."),
    "NOZZLE_DIAMETER": ("Nozzle diameter", "the printer's nozzle. Some values are worked out from it."),
    "LAYER_HEIGHT": ("Layer height", "the slicer's layer height, for the stair-stepping on slopes."),
    "NOMINAL_WALL_THICKNESS": ("Nominal wall", "the thickness most of the part's walls share. "
                                                "Measured for you, or typed in."),
}


def _input(req) -> tuple[str, str]:
    return INPUTS.get(req.name, (req.name.replace("_", " ").capitalize(), ""))


def _sources(doc) -> str:
    return (f"freecad/DFM/core/processes/{doc.stem}.yaml, freecad/DFM/core/processes/process.py, "
            "freecad/DFM/core/rules.py, docs/_data/citations.yaml")


def _default_values(r, doc) -> str:
    parts = []
    for attr, label in zip(r.attrs, r.labels):
        value = doc.value("Default", r.rule, attr)[0]
        if value:
            parts.append(f"{label}: {md.value(value, r.unit, r.unit_suffix)}")
    return " · ".join(parts) or "–"


def process_page(doc, rules, cites) -> str:
    from freecad.DFM.core.rules import Criticality

    by_name = {r.name: r for r in rules}
    p = doc.process
    out = md.header(_sources(doc))
    out += f"# {p.name}\n\n{md.plain(p.description)}\n\n"
    out += f"**Process family:** {p.archetype.label}. {p.archetype.description}\n\n"

    out += "## What you set up\n\n"
    if doc.inputs:
        out += "Before it runs, the analysis asks for:\n\n"
        for req in doc.inputs:
            label, text = _input(req)
            out += f"- **{label}**: {text}\n"
        out += "\n"
    else:
        out += "Nothing: pick the part, the process and a material, and run.\n\n"

    active = [by_name[x.name] for x in p.active_rules]
    order = list(Criticality)
    active.sort(key=lambda r: (order.index(p.get_criticality(r.rule)), r.label))
    out += f"## Rules ({len(active)})\n\n"
    out += (f"Sorted by [criticality]({TO_CONTENT}concepts/criticality.md). The values are the "
            "Default material's; the materials below change some of them.\n\n")
    out += md.table(
        ["Rule", "Criticality", "Default values", "Source"],
        [[f"[{r.label}](../rules/{r.slug}.md)", p.get_criticality(r.rule).label,
          _default_values(r, doc), row_source(doc, r, "Default", cites, TO_CONTENT)] for r in active],
    )

    names = [m for m in doc.material_names() if m != "Default"]
    out += f"## Materials ({len(names)})\n\n"
    out += ("Each material changes only the values it needs to; everything else comes from the "
            "Default material. You can add your own materials in the Process Library.\n\n")
    rows = []
    for m in names:
        mat = p.materials[m]
        changes = []
        for rule in sorted(mat.rule_limits, key=lambda x: by_name[x.name].label):
            r = by_name[rule.name]
            vals = " · ".join(
                f"{label}: {md.value(doc.value(m, rule, a)[0], r.unit, r.unit_suffix)}"
                for a, label in zip(r.attrs, r.labels) if doc.value(m, rule, a)[0]
            )
            changes.append(f"[{r.label}](../rules/{r.slug}.md): {vals}")
        rows.append([m, mat.category, "<br>".join(changes) or "Nothing: all Default values"])
    out += md.table(["Material", "Category", "Values that differ from Default"], rows)
    return out


def index_page(processes) -> str:
    out = md.header("freecad/DFM/core/processes/*.yaml")
    out += "# Processes\n\n"
    out += ("A process is a manufacturing method with the rules that matter for it, and values "
            "for each material. These ship with the workbench; in the Process Library you can "
            "change them, or add processes and materials of your own.\n\n")
    rows = []
    for doc in processes:
        p = doc.process
        inputs = ", ".join(_input(r)[0] for r in doc.inputs) or "Nothing"
        rows.append([f"[{p.name}]({doc.slug}.md)", p.archetype.label, str(len(p.active_rules)),
                     str(len(p.materials) - (1 if "Default" in p.materials else 0)), inputs])
    out += md.table(["Process", "Family", "Rules", "Materials", "You set up"], rows,
                    ["left", "left", "right", "right", "left"])
    return out


def reference_index(rules, processes, cites) -> str:
    out = md.header("freecad/DFM/core/rules.py, freecad/DFM/core/processes/*.yaml")
    out += "# Reference\n\n"
    out += ("Everything on these pages is generated from the workbench's code and its built-in "
            "processes, so it always matches the latest version.\n\n")
    out += (f"- [Rules](rules/index.md): all {len(rules)} rules, with diagrams, values, "
            "messages and sources.\n")
    out += (f"- [Processes](processes/index.md): the {len(processes)} built-in processes, what "
            "each asks you to set up, and their materials.\n\n")
    return out


def nav_lines(processes) -> list[str]:
    return [f'{{ "{d.name}" = "reference/processes/{d.slug}.md" }},' for d in processes]


def write(out, rules, processes, cites) -> None:
    for doc in processes:
        out.write(f"content/reference/processes/{doc.slug}.md", process_page(doc, rules, cites))
    out.write("content/reference/processes/index.md", index_page(processes))
    out.write("content/reference/index.md", reference_index(rules, processes, cites))
    out.patch_block("zensical.toml", "processes", nav_lines(processes))
