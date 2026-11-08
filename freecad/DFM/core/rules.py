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

    # -- Injection moulding features (recognised; see core/base/feature_check.py) --
    MAX_BOSS_WALL_RATIO = RuleType(
        "Boss Wall Thickness",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="%",
        comparison="max",
        unit_suffix="of nominal wall",
        field_labels=("Aim for", "At most"),
        description="Boss wall thickness compared with the nominal wall.",
    )
    MAX_RIB_THICKNESS_RATIO = RuleType(
        "Rib Thickness",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="%",
        comparison="max",
        unit_suffix="of nominal wall",
        field_labels=("Aim for", "At most"),
        description="Rib thickness at its base compared with the nominal wall.",
    )
    MAX_BOSS_HEIGHT_RATIO = RuleType(
        "Boss Height",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="×",
        comparison="max",
        unit_suffix="outer diameter",
        field_labels=("Aim for", "At most"),
        description="Boss height compared with its outer diameter.",
    )
    MAX_RIB_HEIGHT_RATIO = RuleType(
        "Rib Height",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="×",
        comparison="max",
        unit_suffix="nominal wall",
        field_labels=("Aim for", "At most"),
        description="Rib height compared with the nominal wall.",
    )
    MIN_BOSS_CORE_DEPTH = RuleType(
        "Boss Core Depth",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="%",
        comparison="min",
        unit_suffix="of boss height",
        field_labels=("Aim for", "At least"),
        description="How far the hole in a screw boss reaches down towards its base.",
    )
    STANDALONE_BOSS = RuleType(
        "Standalone Boss",
        shape=RuleShape.BINARY,
        unit=None,
        description="Bosses not tied to a side wall, rib or gusset.",
    )
    MIN_BOSS_SPACING = RuleType(
        "Boss Spacing",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="×",
        comparison="min",
        unit_suffix="nominal wall",
        description="Clear gap between neighbouring bosses compared with the nominal wall.",
    )
    MIN_RIB_SPACING = RuleType(
        "Rib Spacing",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="×",
        comparison="min",
        unit_suffix="nominal wall",
        description="Gap between parallel ribs compared with the nominal wall.",
    )
    MIN_BOSS_ROOT_RADIUS = RuleType(
        "Boss Root Radius",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="%",
        comparison="min",
        unit_suffix="of nominal wall",
        description="Fillet radius where a boss meets its base, compared with the nominal wall.",
    )
    MIN_RIB_ROOT_RADIUS = RuleType(
        "Rib Root Radius",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="%",
        comparison="min",
        unit_suffix="of nominal wall",
        description="Fillet radius where a rib meets its base, compared with the nominal wall.",
    )
    MAX_HOLE_DEPTH_RATIO = RuleType(
        "Cored Hole Depth",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="×",
        comparison="max",
        unit_suffix="hole diameter",
        field_labels=("Aim for", "At most"),
        description="Depth of holes formed by core pins compared with their diameter.",
    )
    MIN_HOLE_EDGE_DISTANCE = RuleType(
        "Hole Spacing",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="×",
        comparison="min",
        unit_suffix="wall or hole Ø (larger)",
        description="Gap from a hole to the next hole or the edge of its face.",
    )
    MAX_WALL_THICKNESS_VARIATION = RuleType(
        "Wall Thickness Transition",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="%",
        comparison="max",
        unit_suffix="of the thicker wall",
        field_labels=("Aim for", "At most"),
        description="Abrupt change in thickness where one wall steps to another.",
    )
    MIN_MOLD_STEEL_THICKNESS = RuleType(
        "Thin Steel",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="mm",
        comparison="min",
        description="Narrowest gap between facing part walls: the mould steel between them.",
        per_material=False,
    )
    MAX_LIVING_HINGE_WEB = RuleType(
        "Living Hinge",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="mm",
        comparison="max",
        field_labels=("Aim for", "At most"),
        description="Web thickness of living hinges.",
    )
    MAX_SNAP_FIT_STRAIN = RuleType(
        "Snap-Fit Strain",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="%",
        comparison="max",
        unit_suffix="strain at the root",
        field_labels=("Aim for", "At most"),
        description="Bending strain at a snap-fit's root as it is pushed home.",
    )
    MIN_RIB_DRAFT = RuleType(
        "Rib Draft",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="°",
        comparison="min",
        unit_suffix="per side",
        description="Draft on the sides of ribs, which replaces the general draft rule there.",
    )
    MIN_BOSS_DRAFT = RuleType(
        "Boss Draft",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="°",
        comparison="min",
        unit_suffix="outside (half for the hole)",
        description="Draft on boss walls and their holes, which replaces the general draft rule there.",
    )
    MIN_BOSS_HOLE_BASE_RADIUS = RuleType(
        "Boss Hole Base Radius",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="%",
        comparison="min",
        unit_suffix="of nominal wall",
        description="Fillet radius at the bottom of the hole in a screw boss.",
    )
    BOSS_HOLE_LEAD_IN = RuleType(
        "Screw Boss Lead-In",
        shape=RuleShape.BINARY,
        unit=None,
        description="Screw bosses whose hole has no chamfer or countersink to start the screw.",
    )
    MIN_BOSS_OD_RATIO = RuleType(
        "Boss Outer Diameter",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="×",
        comparison="min",
        unit_suffix="hole diameter",
        description="Screw boss outer diameter compared with its hole.",
    )
    MIN_INTERNAL_CORNER_RADIUS = RuleType(
        "Internal Corner Radius",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="%",
        comparison="min",
        unit_suffix="of nominal wall",
        description="Radius of filleted inside corners compared with the nominal wall.",
    )
    MIN_TEXT_STROKE = RuleType(
        "Moulded Text",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="mm",
        comparison="min",
        description="Stroke width of moulded lettering; also warns of text recessed into the part.",
        per_material=False,
    )
    # -- 3D printing features (recognised for the print direction) --
    MIN_PRINTED_HOLE_DIAMETER = RuleType(
        "Printed Hole Diameter",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="mm",
        comparison="min",
        description="Smallest hole that prints open and close to size.",
        per_material=False,
    )
    HORIZONTAL_HOLE_TEARDROP = RuleType(
        "Horizontal Hole Shape",
        shape=RuleShape.BINARY,
        unit=None,
        description="Large round holes lying across the print direction, which sag without a teardrop top.",
        per_material=False,
    )
    MIN_PIN_DIAMETER = RuleType(
        "Pin Diameter",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="mm",
        comparison="min",
        description="Diameter of slender pins and posts (at least twice as tall as wide).",
        per_material=False,
    )
    MIN_PRINTED_TEXT_DEPTH = RuleType(
        "Printed Text",
        shape=RuleShape.TARGET_AND_LIMIT,
        unit="mm",
        comparison="min",
        description="Height of embossed or depth of engraved lettering; also warns of small characters.",
        per_material=False,
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
