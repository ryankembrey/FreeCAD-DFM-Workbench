# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""What the reference pages are made from: the rules and the built-in processes.

Read from the code (``Rulebook``, the rule diagrams, the checks) and from the
built-in process files, never from the Process Registry, which would add the
user's own saved processes.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from . import env

#: Built-in processes in the order the site lists them. Any other process file
#: found in core/processes is listed after these, by file name.
PROCESS_ORDER = (
    "plastic_injection_molding",
    "fdm_3d_printing",
    "resin_3d_printing",
    "powder_bed_fusion",
)


@dataclass
class RuleDoc:
    rule: object  # Rulebook member
    slug: str
    label: str
    description: str
    brief: str
    svg: Optional[bytes]
    unit: str
    unit_suffix: str
    attrs: tuple[str, ...]  # RuleLimit fields this rule uses, e.g. ("target", "limit")
    labels: tuple[str, ...]  # their labels, e.g. ("Aim for", "At least")
    binary: bool
    placeholders: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.rule.name


@dataclass
class ProcessDoc:
    stem: str  # file name without .yaml
    slug: str
    process: object  # core.processes.process.Process
    raw: dict
    inputs: list = field(default_factory=list)  # ProcessRequirement members, in setup order

    @property
    def name(self) -> str:
        return self.process.name

    def material_names(self) -> list[str]:
        """Default first, then the rest in file order."""
        names = list(self.process.materials)
        if "Default" in names:
            names.remove("Default")
            names.insert(0, "Default")
        return names

    def value(self, material: str, rule, attr: str) -> tuple[str, str]:
        """(value, material it is written under) for *rule*'s *attr*, with the
        Default material's value when *material* doesn't override it."""
        mat = self.process.materials.get(material)
        if mat is not None and rule in mat.rule_limits:
            v = getattr(mat.rule_limits[rule], attr, "")
            if v not in ("", None):
                return str(v), material
        default = self.process.materials.get("Default")
        if default is not None and rule in default.rule_limits:
            v = getattr(default.rule_limits[rule], attr, "")
            if v not in ("", None):
                return str(v), "Default"
        return "", ""

    def overrides(self, material: str, rule) -> bool:
        mat = self.process.materials.get(material)
        return material != "Default" and mat is not None and rule in mat.rule_limits

    def written_values(self):
        """Every (material, rule, attr, value) written in the process file."""
        from freecad.DFM.core.rules import SHAPE_FIELD_ATTRS

        for material in self.material_names():
            limits = self.process.materials[material].rule_limits
            for rule, limit in limits.items():
                for attr in SHAPE_FIELD_ATTRS[rule.shape]:
                    v = getattr(limit, attr, "")
                    if v not in ("", None):
                        yield material, rule, attr, str(v)


def rule_slug(rule) -> str:
    return rule.name.lower().replace("_", "-")


def load_rules() -> list[RuleDoc]:
    """Every rule, in Rulebook order."""
    env.setup()
    from freecad.DFM.core.registries.checks_registry import get_check_class
    from freecad.DFM.core.rules import SHAPE_FIELD_ATTRS, Rulebook
    from freecad.DFM.gui.rule_information._diagrams import DIAGRAMS

    rules = []
    for rule in Rulebook:
        diagram = DIAGRAMS.get(rule)
        check = get_check_class(rule)
        attrs = SHAPE_FIELD_ATTRS[rule.shape]
        rules.append(RuleDoc(
            rule=rule,
            slug=rule_slug(rule),
            label=rule.label,
            description=rule.description,
            brief=diagram.brief if diagram else "",
            svg=diagram.svg if diagram else None,
            unit=rule.unit,
            unit_suffix=rule.unit_suffix,
            attrs=attrs,
            labels=tuple(rule.field_label_for(a) for a in attrs),
            binary=rule.is_binary,
            placeholders=check().message_placeholders(rule) if check else {},
        ))
    return rules


def process_files() -> list[Path]:
    files = {p.stem: p for p in env.PROCESSES.glob("*.yaml")}
    ordered = [files.pop(stem) for stem in PROCESS_ORDER if stem in files]
    return ordered + [files[stem] for stem in sorted(files)]


def load_processes() -> list[ProcessDoc]:
    """The built-in processes, read straight from their files."""
    env.setup()
    from freecad.DFM.app.process_requirements import required_inputs
    from freecad.DFM.core.models import ProcessRequirement
    from freecad.DFM.core.processes.process import Process

    order = list(ProcessRequirement)
    docs = []
    for path in process_files():
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        process = Process.from_yaml(copy.deepcopy(raw))
        inputs = sorted(required_inputs(process), key=order.index)
        docs.append(ProcessDoc(stem=path.stem, slug=path.stem.replace("_", "-"),
                               process=process, raw=raw, inputs=inputs))
    return docs
