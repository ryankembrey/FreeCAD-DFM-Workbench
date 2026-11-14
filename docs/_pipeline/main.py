# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""docs/build.py: regenerate everything in docs/ made from the code.

    --check      write nothing; exit 1 if a generated page is out of date
    --no-shots   leave the screenshots as they are (fast)
    --shots      retake every screenshot, even if nothing changed
"""

from __future__ import annotations

import re
import time

from . import env

#: Paths under docs/ that only the generator writes. Anything in them it
#: didn't write (or keep) this run is deleted.
GENERATED = (
    "content/reference",
    "content/assets/diagrams",
    "content/assets/shots",
    "content/assets/logo.svg",
    "content/citations/coverage.md",
    "content/citations/bibliography.md",
)


def _shot_references(registry) -> list[str]:
    """Problems with screenshots used in the pages: unknown ones, unused ones."""
    used = set()
    problems = []
    for page in sorted(env.CONTENT.rglob("*.md")):
        for shot_id in re.findall(r"assets/shots/([a-z0-9-]+)\.png", page.read_text(encoding="utf-8")):
            used.add(shot_id)
            if shot_id not in registry:
                problems.append(f"{page.relative_to(env.DOCS)} uses screenshot {shot_id!r}, "
                                "which no shot in docs/_pipeline/shots makes")
    for shot_id in sorted(set(registry) - used):
        problems.append(f"screenshot {shot_id!r} isn't used by any page")
    return problems


def main(argv: list[str]) -> int:
    check = "--check" in argv
    started = time.monotonic()
    env.setup()
    from . import citations, diagrams, model, pages_citations, pages_extra, pages_processes, pages_rules
    from .output import Output
    from .shots import REGISTRY, engine, panels, scenes  # noqa: F401  (registers the shots)

    rules = model.load_rules()
    processes = model.load_processes()
    cites = citations.load(env.DATA / "citations.yaml", processes)
    if cites.errors:
        print("docs/_data/citations.yaml has problems:")
        for e in cites.errors:
            print("  - " + e)
        return 2

    out = Output(env.DOCS, check=check)
    out.write("content/assets/logo.svg", env.LOGO.read_bytes())
    diagrams.write(out, rules)
    pages_rules.write(out, rules, processes, cites)
    pages_processes.write(out, rules, processes, cites)
    pages_citations.write(out, rules, processes, cites)
    pages_extra.write(out, rules, processes)
    pages_extra.write_changelog()  # not committed (see its docstring), so not checked

    problems = _shot_references(REGISTRY)
    if problems:
        print("Screenshots and pages don't match:")
        for p in problems:
            print("  - " + p)
        return 2

    notes = []
    if "--no-shots" in argv:
        engine.run(out, check=True)  # keeps the images as they are
    else:
        notes = engine.run(out, check=check, force="--shots" in argv)
    out.prune(*GENERATED)

    seconds = time.monotonic() - started
    for note in notes:
        print(note)
    if check:
        if out.changed:
            print(f"docs/ is out of date ({len(out.changed)} files). Run `pixi run docs` and commit:")
            for line in out.changed:
                print("  " + line)
            return 1
        print(f"docs/ is up to date ({seconds:.1f}s).")
        return 0
    for line in out.changed:
        print("  " + line)
    print(f"docs/: {len(out.changed)} files changed ({seconds:.1f}s).")
    return 0
