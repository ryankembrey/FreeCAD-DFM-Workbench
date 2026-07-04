# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Criticality(Enum):
    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3

    @property
    def label(self) -> str:
        return self.name.capitalize()


class RuleShape(Enum):
    """How the material editor renders this rule's inputs."""

    TARGET_AND_LIMIT = "target_and_limit"
    TARGET_ONLY = "target_only"
    LIMIT_ONLY = "limit_only"
    MIN_AND_MAX = "min_and_max"
    BINARY = "binary"


@dataclass(frozen=True)
class RuleType:
    """Static metadata for one rule.

    unit is the suffix shown inside the number input (e.g. mm, °).
    unit_suffix is a phrase shown under the input for ratio rules
    (e.g. "of wall thickness"), empty for absolute values.
    field_labels overrides the shape's default input labels.
    description is one short line shown under the rule name.
    """

    label: str
    shape: RuleShape
    unit: Optional[str] = "mm"
    comparison: str = "min"
    unit_suffix: str = ""
    description: str = ""
    field_labels: tuple[str, ...] = ()


SHAPE_DEFAULT_LABELS: dict[RuleShape, tuple[str, ...]] = {
    RuleShape.TARGET_AND_LIMIT: ("Aim for", "At least"),
    RuleShape.TARGET_ONLY: ("Aim for",),
    RuleShape.LIMIT_ONLY: ("At most",),
    RuleShape.MIN_AND_MAX: ("Between", "and"),
    RuleShape.BINARY: ("If detected",),
}


class Rulebook(Enum):
    MIN_DRAFT_ANGLE = RuleType(
        "Minimum Draft Angle",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="°",
        comparison="min",
        description="Minimum inclination of faces relative to a reference axis.",
    )
    MIN_WALL_THICKNESS = RuleType(
        "Minimum Wall Thickness",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="mm",
        comparison="min",
        description="Minimum distance between opposing surfaces.",
    )
    MAX_WALL_THICKNESS = RuleType(
        "Maximum Wall Thickness",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="mm",
        comparison="max",
        field_labels=("Aim for", "At most"),
        description="Maximum distance between opposing surfaces.",
    )
    NO_UNDERCUTS = RuleType(
        "Undercut",
        shape=RuleShape.BINARY,
        unit=None,
        description="Geometry occluded by other features along a reference axis.",
    )
    SHARP_INTERNAL_CORNERS = RuleType(
        "Sharp Internal Corners",
        shape=RuleShape.BINARY,
        unit="°",
        description="Concave intersections of surfaces without a radius.",
    )
    SHARP_EXTERNAL_CORNERS = RuleType(
        "Sharp External Corners",
        shape=RuleShape.BINARY,
        unit="°",
        description="Convex intersections of surfaces without a radius.",
    )
    MAX_OVERHANG_ANGLE = RuleType(
        "Maximum Overhang Angle",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="°",
        comparison="max",
        field_labels=("Aim for", "At most"),
        description="Maximum unsupported surface angle relative to the print orientation.",
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
    def unit_suffix(self) -> str:
        return self.value.unit_suffix

    @property
    def description(self) -> str:
        return self.value.description

    @property
    def shape(self) -> RuleShape:
        return self.value.shape

    @property
    def is_binary(self) -> bool:
        return self.value.shape == RuleShape.BINARY

    @property
    def comparison(self) -> str:
        return self.value.comparison

    @property
    def field_labels(self) -> tuple[str, ...]:
        if self.value.field_labels:
            return self.value.field_labels
        return SHAPE_DEFAULT_LABELS[self.shape]
