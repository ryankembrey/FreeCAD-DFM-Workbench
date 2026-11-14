# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The generated citations pages (coverage, bibliography) and the source text
shown beside every value on the rule and process pages."""

from __future__ import annotations

from collections import Counter, defaultdict

from . import md
from .citations import KINDS, UNSOURCED

SOURCES = "docs/_data/citations.yaml, freecad/DFM/core/processes/*.yaml"


def _title(text: str) -> str:
    return text.replace('"', "'")


def claim_text(claim, cites, to_content: str) -> str:
    """'Published, [Protolabs](...)' for a claim; *to_content* leads from the
    page to docs/content."""
    if claim is None:
        return UNSOURCED
    kind = KINDS[claim.kind]
    if claim.note and not claim.cite:
        kind = f'<abbr title="{_title(claim.note)}">{kind}</abbr>'
    parts = [kind]
    for i, sid in enumerate(claim.cite):
        src = cites.sources[sid]
        title = f' "{_title(claim.note)}"' if claim.note and i == 0 else ""
        parts.append(f"[{src.publisher}]({to_content}citations/bibliography.md#{sid}{title})")
    return ", ".join(parts)


def row_source(doc, rule, material: str, cites, to_content: str) -> str:
    """The source text for one table row (all of a rule's values for one
    material), with each value's own source when they differ."""
    per_attr = []
    for attr, label in zip(rule.attrs, rule.labels):
        value, written = doc.value(material, rule.rule, attr)
        if not value:
            continue
        per_attr.append((label, cites.claim(doc.stem, written, rule.name, attr)))
    if not per_attr:
        return ""
    if len({c for _l, c in per_attr}) == 1:
        return claim_text(per_attr[0][1], cites, to_content)
    return "<br>".join(f"{label}: {claim_text(c, cites, to_content)}" for label, c in per_attr)


def _values(processes, rules_by_name, cites):
    """Every written value: (doc, material, RuleDoc, attr, value, claim)."""
    for doc in processes:
        for material, rule, attr, value in doc.written_values():
            r = rules_by_name[rule.name]
            yield doc, material, r, attr, value, cites.claim(doc.stem, material, rule.name, attr)


def coverage_page(rules, processes, cites) -> str:
    rules_by_name = {r.name: r for r in rules}
    values = list(_values(processes, rules_by_name, cites))
    out = md.header(SOURCES)
    out += "# Coverage\n\n"
    out += (
        "Every value the built-in processes ship with, and where it comes from. "
        "A material that doesn't set a value uses the Default material's, and its source. "
        "[How we cite](index.md) explains the kinds of source.\n\n"
    )
    kinds = list(KINDS) + [None]
    head = ["Process", "Values"] + [KINDS[k] for k in KINDS] + [UNSOURCED]
    rows = []
    totals = Counter()
    for doc in processes:
        counts = Counter(c.kind if c else None for d, *_rest, c in values if d is doc)
        n = sum(counts.values())
        totals.update(counts)
        rows.append([f"[{doc.name}](#{doc.slug})", str(n)] + [str(counts[k]) for k in kinds])
    rows.append(["**All processes**", f"**{sum(totals.values())}**"] + [f"**{totals[k]}**" for k in kinds])
    out += md.table(head, rows, ["left"] + ["right"] * (len(head) - 1))

    for doc in processes:
        out += f"## {doc.name} {{ #{doc.slug} }}\n\n"
        rows = []
        for d, material, r, attr, value, claim in values:
            if d is not doc:
                continue
            label = r.labels[r.attrs.index(attr)]
            sources = ", ".join(
                f"[{cites.sources[s].publisher}](bibliography.md#{s})" for s in (claim.cite if claim else ())
            )
            rows.append([
                f"[{r.label}](../reference/rules/{r.slug}.md)", material, label,
                md.value(value, r.unit, r.unit_suffix),
                KINDS[claim.kind] if claim else UNSOURCED,
                sources, claim.note if claim else "",
            ])
        counts = Counter(r[4] for r in rows)
        out += ", ".join(f"{n} {k.lower()}" for k, n in counts.most_common()) + ".\n\n"
        out += md.admonition_collapsed(
            "abstract", f"Every value ({len(rows)})",
            md.table(["Rule", "Material", "Setting", "Value", "Kind", "Source", "Note"], rows),
        )
    return out


def bibliography_page(rules, processes, cites) -> str:
    rules_by_name = {r.name: r for r in rules}
    used = defaultdict(lambda: defaultdict(set))  # source -> RuleDoc -> process names
    count = Counter()
    for doc, material, r, attr, value, claim in _values(processes, rules_by_name, cites):
        for sid in (claim.cite if claim else ()):
            used[sid][r.name].add(doc.name)
            count[sid] += 1

    out = md.header(SOURCES)
    out += "# Bibliography\n\n"
    out += (
        "Every document the built-in values are taken from or checked against. "
        "Where a link hasn't been recorded yet, the source is named so you can find it.\n\n"
    )
    for src in sorted(cites.sources.values(), key=lambda s: (s.publisher.lower(), s.title.lower(), s.id)):
        out += f"### {src.publisher}: {src.title} {{ #{src.id} }}\n\n"
        facts = []
        facts.append(f"[Read it]({src.url})" if src.url else "*No link recorded yet.*")
        if src.archive:
            facts.append(f"[Archived copy]({src.archive})")
        if src.accessed:
            facts.append(f"Read on {src.accessed}")
        out += " · ".join(facts) + "\n\n"
        if src.note:
            out += f"{src.note}\n\n"
        if count[src.id]:
            items = []
            for name in sorted(used[src.id], key=lambda n: rules_by_name[n].label):
                r = rules_by_name[name]
                procs = ", ".join(sorted(used[src.id][name]))
                items.append(f"[{r.label}](../reference/rules/{r.slug}.md) ({procs})")
            n = count[src.id]
            out += f"Cited for {n} value{'s' if n != 1 else ''}: " + "; ".join(items) + ".\n\n"
        else:
            out += "Not cited for any value yet.\n\n"
    return out


def write(out, rules, processes, cites) -> None:
    out.write("content/citations/coverage.md", coverage_page(rules, processes, cites))
    out.write("content/citations/bibliography.md", bibliography_page(rules, processes, cites))
