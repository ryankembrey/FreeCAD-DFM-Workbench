# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""What the analysis setup asks for before a process can run.

Each active rule's analyzer lists the inputs it needs (a pull direction, a
print orientation...). Some it can do without (``optional_requirements``):
those are asked for unless the process family has no use for them
(``ProcessArchetype.waived_requirements``): a powder bed has no print
orientation, so its feature rules run without one. An input that any
active rule's analyzer strictly needs is always asked for.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..core.models import ProcessRequirement
from ..core.registries.analyzers_registry import get_analyzer_class
from ..core.registries.checks_registry import get_check_class


def required_inputs(
    process: Any,
    warn: Optional[Callable[[str], None]] = None,
) -> set[ProcessRequirement]:
    """The inputs the setup must ask for to run *process*'s active rules."""
    if process is None:
        return set()
    strict: set[ProcessRequirement] = set()
    optional: set[ProcessRequirement] = set()
    for rule in process.active_rules:
        try:
            check_cls = get_check_class(rule)
        except KeyError:
            if warn:
                warn(f"Rule '{rule.name}' not found in Rulebook.")
            continue
        if not check_cls:
            continue
        analyzer_cls = get_analyzer_class(check_cls().required_analyzer_id)
        if not analyzer_cls:
            continue
        analyzer = analyzer_cls()
        needs = set(analyzer.requirements)
        can_skip = needs & set(analyzer.optional_requirements)
        strict |= needs - can_skip
        optional |= can_skip

    archetype = process.archetype
    # Archetype-level inputs (the nozzle diameter for FDM) are always needed.
    return strict | (optional - archetype.waived_requirements) | set(archetype.extra_requirements)
