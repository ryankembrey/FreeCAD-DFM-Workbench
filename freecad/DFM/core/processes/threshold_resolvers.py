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
    """FDM 3D Printing: thresholds that are a number of extrusion widths are
    derived from the nozzle diameter.

    MIN_WALL_THICKNESS and MIN_PRINTED_TEXT_STROKE:
        target = nozzle_diameter × 2   (two extrusion lines)
        limit  = nozzle_diameter × 1   (one line)

    All other rules: delegate to base (material data). So do these when no
    usable nozzle diameter was given.
    """

    #: rule -> (target, limit) in nozzle diameters.
    # Walls: 2× is two full perimeters, the practical minimum for a part that
    #        will be handled; 1× is a single line the slicer may skip or
    #        under-extrude (Raise3D, QIDI, PrintCalcs design-for-FDM guides).
    # Letter strokes: a stroke narrower than one line doesn't print; two lines
    #        (0.8 mm with a 0.4 mm nozzle) is Xometry's embossed stroke.
    NOZZLE_MULTIPLES: dict[Rulebook, tuple[float, float]] = {
        Rulebook.MIN_WALL_THICKNESS: (2.0, 1.0),
        Rulebook.MIN_PRINTED_TEXT_STROKE: (2.0, 1.0),
    }

    def resolve(
        self,
        rule_id: Rulebook,
        process: "Process",
        target_material: Optional["Material"],
        **kwargs: Any,
    ) -> RuleLimit:
        multiples = self.NOZZLE_MULTIPLES.get(rule_id)
        if multiples is not None:
            nozzle = self._nozzle(rule_id, kwargs.get(ProcessRequirement.NOZZLE_DIAMETER.name))
            if nozzle is not None:
                target, limit = multiples
                return RuleLimit(
                    target=str(round(nozzle * target, 3)),
                    limit=str(round(nozzle * limit, 3)),
                    binary_severity="ERROR",
                )

        # Everything else (and no nozzle): standard material-based resolution
        return super().resolve(rule_id, process, target_material, **kwargs)

    @staticmethod
    def _nozzle(rule_id: Rulebook, value: Any) -> Optional[float]:
        """The nozzle diameter as a positive number, or None (with a warning)."""
        if value is None:
            App.Console.PrintWarning(
                f"DFM: FDM process requires nozzle diameter for {rule_id.label}. "
                "Falling back to material data.\n"
            )
            return None
        try:
            nozzle = float(value)
        except (TypeError, ValueError):
            nozzle = 0.0
        if nozzle <= 0.0:
            App.Console.PrintWarning(
                f"DFM: Invalid nozzle diameter value '{value}'. Falling back to material data.\n"
            )
            return None
        return nozzle


# ---- Registry ---------------------------------------------------------------

_RESOLVERS: dict[ProcessArchetype, ThresholdResolver] = {
    ProcessArchetype.GENERIC: ThresholdResolver(),
    ProcessArchetype.INJECTION_MOLDING: InjectionMoldingResolver(),
    ProcessArchetype.FDM: FDMResolver(),
    ProcessArchetype.RESIN: ThresholdResolver(),
    ProcessArchetype.POWDER_BED: ThresholdResolver(),
}


def get_resolver(archetype: ProcessArchetype) -> ThresholdResolver:
    """Return the threshold resolver for the given archetype."""
    return _RESOLVERS.get(archetype, _RESOLVERS[ProcessArchetype.GENERIC])
