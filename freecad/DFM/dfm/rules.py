# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from enum import Enum
from dataclasses import dataclass
from typing import Optional


@dataclass
class RuleType:
    label: str
    unit: Optional[str] = "mm"
    is_binary: bool = False  # If True, Target/Limit columns are irrelevant
    comparison: str = "min"  # "min" or "max"


class Rulebook(Enum):
    MIN_DRAFT_ANGLE = RuleType("Minimum Draft Angle", unit="°", comparison="min")
    MIN_WALL_THICKNESS = RuleType("Minimum Wall Thickness", unit="mm", comparison="min")
    MAX_WALL_THICKNESS = RuleType("Maximum Wall Thickness", unit="mm", comparison="max")
    NO_UNDERCUTS = RuleType("Undercut", unit=None, is_binary=True)
    SHARP_INTERNAL_CORNERS = RuleType(
        "Sharp Internal Corners", unit="°", comparison="max", is_binary=True
    )
    SHARP_EXTERNAL_CORNERS = RuleType(
        "Sharp External Corners", unit="°", comparison="max", is_binary=True
    )

    @property
    def id(self) -> str:
        return self.name

    @property
    def label(self) -> str:
        return self.value.label

    @property
    def unit(self) -> str:
        return self.value.unit or ""

    @property
    def is_binary(self) -> bool:
        return self.value.is_binary

    @property
    def comparison(self) -> str:
        return self.value.comparison
