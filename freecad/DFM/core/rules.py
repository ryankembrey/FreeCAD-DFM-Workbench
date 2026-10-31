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
class RuleDependency:
    """Declares that a rule's description references a value from another rule.

    source_rule is the *name* of the Rulebook member (a string, to avoid
    forward-referencing the enum before it is defined).
    source_field is the RuleLimit attribute to read ("target", "limit", …).
    desc_before / desc_after are the prose fragments that wrap the inline
    value in the card description.
    unit is appended to the displayed value (e.g. "°").
    """

    source_rule: str  # Rulebook member name, resolved at runtime
    source_field: str
    desc_before: str
    desc_after: str
    unit: str = ""


@dataclass(frozen=True)
class RuleType:
    """Static metadata for one rule.

    unit is the suffix shown inside the number input (e.g. mm, °).
    unit_suffix is a phrase shown under the input for ratio rules
    (e.g. "of wall thickness"), empty for absolute values.
    field_labels overrides the shape's default input labels.
    description is one short line shown under the rule name.
    per_material controls whether thresholds vary per material (True)
    or are the same for all materials in a process (False).
    dependency, when set, links part of the card description to a
    value from another rule.
    """

    label: str
    shape: RuleShape
    unit: Optional[str] = "mm"
    comparison: str = "min"
    unit_suffix: str = ""
    description: str = ""
    field_labels: tuple[str, ...] = ()
    per_material: bool = True
    dependency: Optional[RuleDependency] = None


SHAPE_DEFAULT_LABELS: dict[RuleShape, tuple[str, ...]] = {
    RuleShape.TARGET_AND_LIMIT: ("Aim for", "At least"),
    RuleShape.TARGET_ONLY: ("Aim for",),
    RuleShape.LIMIT_ONLY: ("At most",),
    RuleShape.MIN_AND_MAX: ("Between", "and"),
    RuleShape.BINARY: ("If detected",),
}

# Maps each shape to its field attribute names, in the same order as the labels.
SHAPE_FIELD_ATTRS: dict[RuleShape, tuple[str, ...]] = {
    RuleShape.TARGET_AND_LIMIT: ("target", "limit"),
    RuleShape.TARGET_ONLY: ("target",),
    RuleShape.LIMIT_ONLY: ("limit",),
    RuleShape.MIN_AND_MAX: ("min_value", "max_value"),
    RuleShape.BINARY: ("binary_severity",),
}


class Rulebook(Enum):
    MIN_DRAFT_ANGLE = RuleType(
        "Minimum Draft Angle",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="°",
        comparison="min",
        description="Minimum inclination of faces relative to the pull direction.",
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
        description="Geometry hidden behind other features along the pull direction.",
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
        description="Maximum angle of unsupported downward-facing surfaces, from the vertical.",
    )
    MAX_BRIDGE_SPAN = RuleType(
        "Maximum Bridge Span",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="mm",
        comparison="max",
        field_labels=("Aim for", "At most"),
        description="Maximum unsupported horizontal span between two supported regions.",
    )
    MAX_BED_CONTACT_AREA = RuleType(
        "Maximum Bed Contact Area",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="mm²",
        comparison="max",
        field_labels=("Aim for", "At most"),
        description="Maximum area of a single flat face on the build platform.",
    )
    MAX_CUSP_HEIGHT = RuleType(
        "Maximum Cusp Height",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="mm",
        comparison="max",
        field_labels=("Aim for", "At most"),
        description="Maximum stair-step deviation on sloped surfaces.",
    )
    MAX_SUPPORT_MATERIAL = RuleType(
        "Maximum Support Material",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="%",
        comparison="max",
        field_labels=("Aim for", "At most"),
        description="Estimated percentage of surface area requiring support material.",
        per_material=False,
        dependency=RuleDependency(
            source_rule="MAX_OVERHANG_ANGLE",
            source_field="limit",
            desc_before="Percentage of surface area exceeding the ",
            desc_after=" overhang limit.",
            unit="°",
        ),
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
    def per_material(self) -> bool:
        return self.value.per_material

    @property
    def field_labels(self) -> tuple[str, ...]:
        if self.value.field_labels:
            return self.value.field_labels
        return SHAPE_DEFAULT_LABELS[self.shape]

    @property
    def dependency(self) -> Optional["RuleDependency"]:
        return self.value.dependency

    def field_label_for(self, attr: str) -> str:
        """Return the display label for a field attribute (e.g. 'limit' → 'At most')."""
        attrs = SHAPE_FIELD_ATTRS.get(self.shape, ())
        labels = self.field_labels
        try:
            return labels[attrs.index(attr)]
        except (ValueError, IndexError):
            return attr
