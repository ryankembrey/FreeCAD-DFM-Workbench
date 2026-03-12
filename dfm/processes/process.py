#  ***************************************************************************
#  *   Copyright (c) 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>              *
#  *                                                                         *
#  *   This file is part of the FreeCAD CAx development system.              *
#  *                                                                         *
#  *   This library is free software; you can redistribute it and/or         *
#  *   modify it under the terms of the GNU Library General Public           *
#  *   License as published by the Free Software Foundation; either          *
#  *   version 2 of the License, or (at your option) any later version.      *
#  *                                                                         *
#  *   This library  is distributed in the hope that it will be useful,      *
#  *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
#  *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
#  *   GNU Library General Public License for more details.                  *
#  *                                                                         *
#  *   You should have received a copy of the GNU Library General Public     *
#  *   License along with this library; see the file COPYING.LIB. If not,    *
#  *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
#  *   Suite 330, Boston, MA  02111-1307, USA                                *
#  *                                                                         *
#  ***************************************************************************

from dataclasses import dataclass, field, fields
from dfm.rules import Rulebook


@dataclass
class RuleLimit:
    """The specific thresholds for a single rule on a specific material."""

    target: str = ""
    limit: str = ""

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
    active_rules: list[Rulebook] = field(default_factory=list)
    materials: dict[str, Material] = field(default_factory=dict)
    rule_feedback: dict[Rulebook, RuleFeedback] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, data: dict):
        raw_materials = data.pop("materials", {})
        raw_active_rules = data.pop("active_rules", [])
        raw_feedback = data.pop("rule_feedback", {})

        valid_field_names = {f.name for f in fields(cls)}
        clean_data = {k: v for k, v in data.items() if k in valid_field_names}

        active_rules = []
        for r_id in raw_active_rules:
            try:
                active_rules.append(Rulebook[r_id])
            except KeyError:
                print(f"Warning: Rule '{r_id}' in process '{data.get('name')}' is invalid.")

        process = cls(**clean_data, active_rules=active_rules)

        for r_id, f_data in raw_feedback.items():
            try:
                rule_member = Rulebook[r_id]
                process.rule_feedback[rule_member] = RuleFeedback(**f_data)
            except (KeyError, TypeError) as e:
                print(f"Warning: Could not parse feedback for '{r_id}': {e}")

        for mat_name, mat_data in raw_materials.items():
            process.materials[mat_name] = Material.from_dict(mat_name, mat_data)

        return process
