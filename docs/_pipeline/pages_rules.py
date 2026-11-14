# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The rule pages (one per rule) and the rules index."""

from __future__ import annotations

from . import diagrams, env, md
from .citations import UNSOURCED
from .pages_citations import row_source

TO_CONTENT = "../../"  # from content/reference/rules/ back to content/


def _sources(r, processes) -> str:
    files = [f"freecad/DFM/core/processes/{p.stem}.yaml" for p in processes if r.rule in p.process.active_rules]
    return ", ".join(["freecad/DFM/core/rules.py", "gui/rule_information/_diagrams.py"] + files
                     + ["docs/_data/citations.yaml"])


def _values_section(r, doc, cites) -> str:
    out = ""
    crit = doc.process.get_criticality(r.rule).label
    out += f"Criticality in this process: **{crit}** ([what this means]({TO_CONTENT}concepts/criticality.md)).\n\n"
    hint = doc.process.archetype.derived_rules.get(r.rule)
    if hint:
        out += md.admonition(
            "info", "Worked out at analysis time",
            f"{hint} The values below are used only when there is nothing to work them out from.",
        )
    materials = doc.material_names()
    shown = [m for m in materials if m == "Default" or doc.overrides(m, r.rule)]
    rest = [m for m in materials if m not in shown]
    head = ["Material", *r.labels, "Source"]
    rows = []
    for m in shown:
        vals = [md.value(doc.value(m, r.rule, a)[0], r.unit, r.unit_suffix) for a in r.attrs]
        rows.append([f"**{m}**" if m == "Default" else m, *vals, row_source(doc, r, m, cites, TO_CONTENT)])
    out += md.table(head, rows)
    if rest:
        out += "Every other material uses the Default values: " + ", ".join(rest) + ".\n\n"
    return out


def _messages_section(r, doc) -> str:
    fb = doc.process.rule_feedback.get(r.rule)
    out = ""
    if fb is None:
        return "This process gives no message for this rule.\n"
    for kind, title, text in (("warning", "Warning", fb.warning_msg), ("failure", "Error", fb.error_msg)):
        text = md.plain(text)
        if text and text != "N/A":
            out += md.admonition(kind, title, md.placeholders_as_code(text))
    return out or "This process gives no message for this rule.\n"


def rule_page(r, processes, cites) -> str:
    used = [p for p in processes if r.rule in p.process.active_rules]
    out = md.header(_sources(r, processes))
    out += f"# {r.label}\n\n{r.description}\n\n"
    if r.svg:
        out += '<figure markdown="span">\n'
        out += diagrams.images(r.slug, f"{r.label}: on the left what to avoid, on the right what to aim for",
                               TO_CONTENT, 'width="640"') + "\n"
        out += f"<figcaption>{r.brief}</figcaption>\n</figure>\n\n"

    authored = env.AUTHORED / "rules" / f"{r.slug}.md"
    if authored.exists():
        out += authored.read_text(encoding="utf-8").strip() + "\n\n"

    out += "## Where it applies\n\n"
    if used:
        out += md.table(
            ["Process", "Criticality"],
            [[f"[{p.name}]({TO_CONTENT}reference/processes/{p.slug}.md)",
              p.process.get_criticality(r.rule).label] for p in used],
        )
    else:
        out += ("No built-in process uses this rule. You can switch it on for a process of "
                "your own in the Process Library.\n\n")

    if used:
        out += "## Values\n\n"
        if r.binary:
            out += ("This rule has no number to meet: when it finds the problem, it reports it "
                    "as the error or warning set here.\n\n")
        else:
            out += (f"**{r.labels[0]}** is what to design for; crossing it gives a warning. "
                    + (f"**{r.labels[1]}** is the hard limit; crossing it gives an error. " if len(r.labels) > 1 else "")
                    + f"See [aim for and limits]({TO_CONTENT}concepts/aim-for-and-limits.md).\n\n")
        out += md.tabs([(p.name, _values_section(r, p, cites)) for p in used])

        out += "## Messages\n\n"
        out += ("What the results panel says about a finding. You can rewrite these in the "
                "Process Library; the words in `{braces}` are filled in for each finding.\n\n")
        out += md.tabs([(p.name, _messages_section(r, p)) for p in used])
        if r.placeholders:
            out += md.table(
                ["Placeholder", "Replaced with"],
                [[f"`{{{k}}}`", v] for k, v in r.placeholders.items()],
            )

        cited, total, sourced = [], 0, 0
        for p in used:
            for m in p.material_names():
                for a in r.attrs:
                    value, written = p.value(m, r.rule, a)
                    if not value or (m != "Default" and not p.overrides(m, r.rule)):
                        continue
                    total += 1
                    c = cites.claim(p.stem, written, r.name, a)
                    if c is not None:
                        sourced += 1
                        cited += [s for s in c.cite if s not in cited]
        out += "## Sources\n\n"
        if cited:
            out += "".join(
                f"- [{cites.sources[s].publisher}: {cites.sources[s].title}]({TO_CONTENT}citations/bibliography.md#{s})\n"
                for s in cited
            ) + "\n"
        out += (f"{sourced} of the {total} values on this page have a recorded source. "
                f"The rest show *{UNSOURCED}* until one is added. "
                f"See [how we cite]({TO_CONTENT}citations/index.md).\n")
    return out


def index_page(rules, processes) -> str:
    out = md.header("freecad/DFM/core/rules.py, gui/rule_information/_diagrams.py, freecad/DFM/core/processes/*.yaml")
    out += "# Rules\n\n"
    out += (f"The workbench checks {len(rules)} rules. Each process switches on the ones that "
            "matter for it, with its own values for each material. Every rule's page shows "
            "what it checks, its values, its messages and where the values come from.\n\n")
    by_name = {r.name: r for r in rules}
    for p in processes:
        active = sorted((by_name[x.name] for x in p.process.active_rules), key=lambda r: r.label)
        out += f"## {p.name}\n\n"
        out += f"[{p.name}](../processes/{p.slug}.md) checks {len(active)} rules.\n\n"
        out += '<div class="grid cards" markdown>\n\n'
        for r in active:
            card = ""
            if r.svg:
                card += diagrams.images(r.slug, r.label, TO_CONTENT, "loading=lazy") + "\n\n"
            card += f"**[{r.label}]({r.slug}.md)**\n\n{r.description}\n"
            out += "-   " + md.indent(card).lstrip() + "\n"
        out += "</div>\n\n"
    return out


def nav_lines(rules) -> list[str]:
    return [f'{{ "{r.label}" = "reference/rules/{r.slug}.md" }},' for r in sorted(rules, key=lambda r: r.label)]


def write(out, rules, processes, cites) -> None:
    for r in rules:
        out.write(f"content/reference/rules/{r.slug}.md", rule_page(r, processes, cites))
    out.write("content/reference/rules/index.md", index_page(rules, processes))
    out.patch_block("zensical.toml", "rules", nav_lines(rules))
