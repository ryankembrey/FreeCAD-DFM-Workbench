# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Writing generated files: only what changed, and nothing left over.

A file is rewritten only when its bytes differ, so a rebuild with nothing new
leaves git clean. Files in the generated folders that this run didn't write
are deleted (a renamed rule leaves no orphan page). In check mode nothing is
written and the differences are only reported.
"""

from __future__ import annotations

import re
from pathlib import Path


class Output:
    def __init__(self, root: Path, check: bool = False):
        self.root = root
        self.check = check
        self.written: set[Path] = set()
        self.changed: list[str] = []

    def write(self, rel: str, data) -> None:
        path = self.root / rel
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.written.add(path.resolve())
        old = path.read_bytes() if path.exists() else None
        if old == data:
            return
        self.changed.append(("updated " if old is not None else "added ") + rel)
        if not self.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def keep(self, rel: str) -> None:
        """Count *rel* as written this run, unchanged (so prune leaves it)."""
        self.written.add((self.root / rel).resolve())

    def prune(self, *managed: str) -> None:
        """Delete files under the *managed* paths (folders or files, relative to
        root) that this run didn't write."""
        for rel in managed:
            base = self.root / rel
            candidates = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file())
            for path in candidates:
                if path.resolve() not in self.written:
                    self.changed.append("removed " + str(path.relative_to(self.root)))
                    if not self.check:
                        path.unlink()

    def patch_block(self, rel: str, name: str, lines: list[str]) -> None:
        """Replace the lines between ``# BEGIN GENERATED <name>`` and
        ``# END GENERATED <name>`` in a hand-written file, keeping the markers'
        indentation."""
        path = self.root / rel
        text = path.read_text(encoding="utf-8")
        pattern = re.compile(
            rf"^([ \t]*)# BEGIN GENERATED {re.escape(name)}\n.*?^[ \t]*# END GENERATED {re.escape(name)}\n",
            re.M | re.S,
        )
        m = pattern.search(text)
        if not m:
            raise SystemExit(f"{rel}: no '# BEGIN GENERATED {name}' / '# END GENERATED {name}' markers")
        indent = m.group(1)
        block = f"{indent}# BEGIN GENERATED {name}\n"
        block += "".join(f"{indent}{line}\n" for line in lines)
        block += f"{indent}# END GENERATED {name}\n"
        self.written.discard(path.resolve())
        self.write(rel, text[: m.start()] + block + text[m.end():])
