# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Optional

import FreeCAD as App  # type: ignore

from ...core.rules import Criticality, Rulebook


class ProcessArchetype(Enum):
    """Defines how a process derives its rule thresholds.

    Each archetype encodes the physics of a manufacturing process family.
    Users pick an archetype when creating a new process; the archetype
    determines which rules make sense and how thresholds are resolved.
    """

    GENERIC = "GENERIC"
    INJECTION_MOLDING = "INJECTION_MOLDING"
    FDM = "FDM"

    @property
    def label(self) -> str:
        return {
            "GENERIC": "Generic",
            "INJECTION_MOLDING": "Injection Molding",
            "FDM": "FDM 3D Printing",
        }.get(self.name, self.name.replace("_", " ").title())

    @property
    def description(self) -> str:
        return {
            "GENERIC": "General-purpose process. All thresholds come from material data.",
            "INJECTION_MOLDING": (
                "Thresholds derived from material properties. "
                "Requires a pull direction for draft and undercut analysis."
            ),
            "FDM": (
                "Wall thickness derived from nozzle diameter. "
                "Overhang and bridge thresholds from material. "
                "Requires print orientation and nozzle diameter."
            ),
        }.get(self.name, "")

    @property
    def extra_requirements(self) -> set:
        """ProcessRequirements needed by the archetype's threshold resolver,
        beyond what analyzers already declare.

        Imported lazily to avoid circular imports with core.models.
        """
        from ..models import ProcessRequirement

        return {
            "GENERIC": set(),
            "INJECTION_MOLDING": set(),
            "FDM": {ProcessRequirement.NOZZLE_DIAMETER},
        }.get(self.name, set())

    @property
    def derived_rules(self) -> dict:
        """Rules whose thresholds are computed by the archetype at runtime,
        not edited manually. Returns {Rulebook member: hint string}.

        Imported lazily to avoid circular imports with core.rules.
        """
        from ..rules import Rulebook

        return {
            "GENERIC": {},
            "INJECTION_MOLDING": {},
            "FDM": {
                Rulebook.MIN_WALL_THICKNESS: (
                    "Derived from nozzle diameter during DFM analysis setup."
                ),
            },
        }.get(self.name, {})


# Mapping from category strings to archetypes for backward compatibility.
# When loading a YAML file that has no 'archetype' field, we infer from category.
_CATEGORY_TO_ARCHETYPE: dict[str, ProcessArchetype] = {
    "Injection Molding": ProcessArchetype.INJECTION_MOLDING,
    "FDM": ProcessArchetype.FDM,
    "FDM 3D Printing": ProcessArchetype.FDM,
    "3D Printing": ProcessArchetype.FDM,
}


@dataclass
class RuleLimit:
    """The specific thresholds for a single rule on a specific material."""

    target: str = ""
    limit: str = ""
    binary_severity: Optional[str] = "ERROR"
    min_value: str = ""
    max_value: str = ""

    @classmethod
    def from_dict(cls, data: dict):
        return cls(**{k: v for k, v in data.items() if k in {f.name for f in fields(cls)}})


@dataclass
class RuleFeedback:
    """Holds user-customizable feedback messages."""

    warning_msg: str = ""
    error_msg: str = ""


@dataclass
class Material:
    """A specific material and its rule overrides for a process."""

    name: str
    category: str
    is_active: bool = True
    rule_limits: dict[Rulebook, RuleLimit] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: dict):
        category = data.get("category", "Unknown")
        is_active = data.get("is_active", True)
        raw_limits = data.get("rule_limits", {})

        parsed_limits = {}
        for rule_id, limit_data in raw_limits.items():
            try:
                rule_member = Rulebook[rule_id]
                parsed_limits[rule_member] = RuleLimit.from_dict(limit_data)
            except KeyError:
                print(f"Warning: Rule ID '{rule_id}' not found in Rulebook. Skipping.")

        return cls(name=name, category=category, is_active=is_active, rule_limits=parsed_limits)


@dataclass
class Process:
    """A representation of a single manufacturing process."""

    name: str
    category: str
    description: str = ""
    archetype: ProcessArchetype = ProcessArchetype.GENERIC
    active_rules: list[Rulebook] = field(default_factory=list)
    materials: dict[str, Material] = field(default_factory=dict)
    rule_feedback: dict[Rulebook, RuleFeedback] = field(default_factory=dict)
    rule_criticality: dict[Rulebook, Criticality] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, data: dict):
        raw_materials = data.pop("materials", {})
        raw_active_rules = data.pop("active_rules", [])
        raw_feedback = data.pop("rule_feedback", {})
        raw_criticality = data.pop("rule_criticality", {})

        # --- Archetype resolution (backward compatible) ---
        raw_archetype = data.pop("archetype", None)
        if raw_archetype:
            try:
                archetype = ProcessArchetype[raw_archetype]
            except KeyError:
                App.Console.PrintWarning(
                    f"DFM: Unknown archetype '{raw_archetype}' in process "
                    f"'{data.get('name')}'. Falling back to GENERIC.\n"
                )
                archetype = ProcessArchetype.GENERIC
        else:
            # Infer from category for old YAML files
            category = data.get("category", "")
            archetype = _CATEGORY_TO_ARCHETYPE.get(category, ProcessArchetype.GENERIC)
            if archetype != ProcessArchetype.GENERIC:
                App.Console.PrintMessage(
                    f"DFM: Process '{data.get('name')}' has no archetype field. "
                    f"Inferred '{archetype.name}' from category '{category}'. "
                    f"Save the process to persist this.\n"
                )

        valid_field_names = {f.name for f in fields(cls)}
        clean_data = {k: v for k, v in data.items() if k in valid_field_names}

        active_rules = []
        for r_id in raw_active_rules:
            try:
                active_rules.append(Rulebook[r_id])
            except KeyError:
                print(f"Warning: Rule '{r_id}' in process '{data.get('name')}' is invalid.")

        process = cls(**clean_data, active_rules=active_rules, archetype=archetype)

        for r_id, f_data in raw_feedback.items():
            try:
                process.rule_feedback[Rulebook[r_id]] = RuleFeedback(**f_data)
            except (KeyError, TypeError) as e:
                print(f"Warning: Could not parse feedback for '{r_id}': {e}")

        for r_id, crit_name in raw_criticality.items():
            try:
                process.rule_criticality[Rulebook[r_id]] = Criticality[crit_name.upper()]
            except KeyError:
                print(f"Warning: Invalid criticality '{crit_name}' for rule '{r_id}'.")

        for mat_name, mat_data in raw_materials.items():
            process.materials[mat_name] = Material.from_dict(mat_name, mat_data)

        return process

    def get_criticality(self, rule: Rulebook) -> Criticality:
        """Returns the process-defined criticality, defaulting to MEDIUM."""
        return self.rule_criticality.get(rule, Criticality.MEDIUM)
