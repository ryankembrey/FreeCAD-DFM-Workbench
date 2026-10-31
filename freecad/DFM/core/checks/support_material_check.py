# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""
Maximum Support Material check.

Uses the OVERHANG_ANALYZER output (per-face overhang angles) together with
the overhang angle limit to estimate the percentage of total surface area
that would require support material.

Cross-rule dependency
---------------------
This check reads the MAX_OVERHANG_ANGLE rule config from ``kwargs`` to
determine which faces count as "overhanging".  The overhang angle limit
defines the threshold: any face whose overhang angle exceeds that limit
needs support.  If MAX_OVERHANG_ANGLE is disabled (unchecked by the user),
the check falls back to a 45° default.

The overhang comparison is intentionally duplicated here rather than
depending on the overhang check's results.  The per-face angle test is
cheap and avoids adding cross-check architecture.
"""

from typing import Any, Optional

from OCP.TopoDS import TopoDS

from ...core.models import CheckResult, Severity
from ...core.base.base_check import BaseCheck
from ...core.processes.process import RuleLimit, RuleFeedback
from ...core.rules import Rulebook
from ...core.registries import register_check
from ...core.utils.geometry import FaceIndex, face_area


_DEFAULT_OVERHANG_LIMIT = 45.0


@register_check(Rulebook.MAX_SUPPORT_MATERIAL)
class SupportMaterialCheck(BaseCheck):
    @property
    def name(self) -> str:
        return "Support Material Checker"

    @property
    def required_analyzer_id(self) -> str:
        return "OVERHANG_ANALYZER"

    def run_check(
        self,
        analysis_data_map: dict[Any, float],
        rule_config: RuleLimit,
        rule: Rulebook,
        feedback: Optional[RuleFeedback] = None,
        **kwargs,
    ) -> list[CheckResult]:
        target = self.safe_float(rule_config.target)
        limit = self.safe_float(rule_config.limit)
        unit = rule.unit

        if target is None and limit is None:
            return []

        # --- Resolve the overhang angle limit from the sibling rule ---
        overhang_limit = self._resolve_overhang_limit(kwargs)

        # --- Collect face areas for faces exceeding the overhang limit ---
        face_index: Optional[FaceIndex] = kwargs.get("face_index")
        total_area: float = kwargs.get("total_surface_area", 0.0)

        if total_area <= 0.0 or face_index is None:
            return []

        support_area = 0.0
        support_faces: list = []
        for face_key, overhang_angle in analysis_data_map.items():
            if overhang_angle > overhang_limit:
                _kind, idx = face_key
                face = face_index.face_at(idx)
                support_area += face_area(face)
                support_faces.append(face_key)

        measured = (support_area / total_area) * 100.0

        # --- Evaluate against thresholds ---
        tolerance = 1e-4
        fb = feedback or RuleFeedback()
        severity: Optional[Severity] = None
        template = ""

        if limit is not None and measured > (limit + tolerance):
            severity = Severity.ERROR
            template = fb.error_msg
        elif target is not None and measured > (target + tolerance):
            severity = Severity.WARNING
            template = fb.warning_msg

        if severity is None:
            return []

        effective_limit = limit if limit is not None else 0.0
        effective_target = target if target is not None else 0.0

        formatted_msg = self.format_feedback(
            template, measured, effective_target, effective_limit, unit
        )
        threshold = effective_limit if severity == Severity.ERROR else effective_target

        return [
            CheckResult(
                rule_id=rule,
                overview=f"{measured:.1f}{unit} > {threshold:.1f}{unit}",
                message=formatted_msg,
                severity=severity,
                failing_geometry=support_faces,
                ignore=False,
                value=round(float(measured), 2),
                limit=float(effective_limit),
                comparison=">",
                unit=unit,
            )
        ]

    def _resolve_overhang_limit(self, kwargs: dict) -> float:
        """Extract the overhang angle limit from kwargs.

        The analysis runner injects the resolved MAX_OVERHANG_ANGLE
        config as ``overhang_rule_config``.  If that key is missing
        (e.g. MAX_OVERHANG_ANGLE is not in active_rules), fall back
        to a conservative 45° default.
        """
        overhang_config: Optional[RuleLimit] = kwargs.get("overhang_rule_config")
        if overhang_config is not None:
            val = self.safe_float(overhang_config.limit)
            if val is not None:
                return val
        return _DEFAULT_OVERHANG_LIMIT
