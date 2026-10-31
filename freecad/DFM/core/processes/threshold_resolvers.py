# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""
Threshold resolution strategies for each ProcessArchetype.

Each resolver knows how to build a RuleLimit for a given rule, using
material data, machine parameters, or a combination of both.
"""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

import FreeCAD as App  # type: ignore

from ..models import ProcessRequirement
from ..rules import Rulebook, RuleType
from .process import ProcessArchetype, RuleLimit

if TYPE_CHECKING:
    from .process import Material, Process


class ThresholdResolver:
    """Base resolver: all thresholds come from material data (Default → override)."""

    def resolve(
        self,
        rule_id: Rulebook,
        process: "Process",
        target_material: Optional["Material"],
        **kwargs: Any,
    ) -> RuleLimit:
        """Build a RuleLimit for *rule_id* by layering Default + material override."""
        config = RuleLimit(target="N/A", limit="N/A", binary_severity=None)

        default_material = process.materials.get("Default")
        if default_material and rule_id in default_material.rule_limits:
            default_limit = default_material.rule_limits[rule_id]
            config.target = default_limit.target
            config.limit = default_limit.limit
            config.binary_severity = default_limit.binary_severity

        # Only apply material-specific overrides for per-material rules.
        if rule_id.per_material and target_material and rule_id in target_material.rule_limits:
            override = target_material.rule_limits[rule_id]
            if override.target not in ("N/A", "", None):
                config.target = override.target
            if override.limit not in ("N/A", "", None):
                config.limit = override.limit
            if override.binary_severity is not None:
                config.binary_severity = override.binary_severity

        if config.binary_severity is None:
            config.binary_severity = "ERROR"

        return config


class InjectionMoldingResolver(ThresholdResolver):
    """Injection molding: identical to base (all thresholds from material)."""

    pass


class FDMResolver(ThresholdResolver):
    """FDM 3D Printing: wall thickness derived from nozzle diameter.

    For MIN_WALL_THICKNESS:
        target = nozzle_diameter × target_multiplier  (default 1.5)
        limit  = nozzle_diameter × limit_multiplier   (default 1.0)

    For MAX_WALL_THICKNESS:
        Falls back to material data (same as base), since max thickness
        in FDM is more about structural concerns than nozzle size.

    All other rules: delegate to base (material data).
    """

    # Nozzle-diameter multipliers for MIN_WALL_THICKNESS.
    # target: 2× nozzle width — two full perimeters, the practical minimum
    #         for a part that will be handled or used.
    # limit:  1× nozzle width — absolute physical floor; a single extrusion
    #         line that the slicer may skip or under-extrude.
    # Sources: Raise3D, QIDI, PrintCalcs design-for-FDM guides.
    MIN_THICKNESS_TARGET_MULT = 2.0
    MIN_THICKNESS_LIMIT_MULT = 1.0

    def resolve(
        self,
        rule_id: Rulebook,
        process: "Process",
        target_material: Optional["Material"],
        **kwargs: Any,
    ) -> RuleLimit:
        if rule_id == Rulebook.MIN_WALL_THICKNESS:
            return self._resolve_min_thickness(rule_id, process, target_material, **kwargs)

        # Everything else: standard material-based resolution
        return super().resolve(rule_id, process, target_material, **kwargs)

    def _resolve_min_thickness(
        self,
        rule_id: Rulebook,
        process: "Process",
        target_material: Optional["Material"],
        **kwargs: Any,
    ) -> RuleLimit:
        nozzle = kwargs.get(ProcessRequirement.NOZZLE_DIAMETER.name)

        if nozzle is None:
            App.Console.PrintWarning(
                "DFM: FDM process requires nozzle diameter for wall thickness. "
                "Falling back to material data.\n"
            )
            return super().resolve(rule_id, process, target_material, **kwargs)

        try:
            nozzle_val = float(nozzle)
        except (TypeError, ValueError):
            App.Console.PrintWarning(
                f"DFM: Invalid nozzle diameter value '{nozzle}'. "
                "Falling back to material data.\n"
            )
            return super().resolve(rule_id, process, target_material, **kwargs)

        target = round(nozzle_val * self.MIN_THICKNESS_TARGET_MULT, 3)
        limit = round(nozzle_val * self.MIN_THICKNESS_LIMIT_MULT, 3)

        return RuleLimit(
            target=str(target),
            limit=str(limit),
            binary_severity="ERROR",
        )


# ---- Registry ---------------------------------------------------------------

_RESOLVERS: dict[ProcessArchetype, ThresholdResolver] = {
    ProcessArchetype.GENERIC: ThresholdResolver(),
    ProcessArchetype.INJECTION_MOLDING: InjectionMoldingResolver(),
    ProcessArchetype.FDM: FDMResolver(),
}


def get_resolver(archetype: ProcessArchetype) -> ThresholdResolver:
    """Return the threshold resolver for the given archetype."""
    return _RESOLVERS.get(archetype, _RESOLVERS[ProcessArchetype.GENERIC])
