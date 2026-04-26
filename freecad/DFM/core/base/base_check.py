# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from abc import ABC, abstractmethod
from typing import Optional

from ...core.models import CheckResult
from ...core.processes.process import RuleFeedback, RuleLimit
from ...core.rules import Rulebook
from ...core.models import Severity


class BaseCheck(ABC):
    """
    The base class for all checks. This class defines how all checks should behave.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def required_analyzer_id(self) -> str:
        """
        A string ID for the analyzer this check depends on.
        """
        pass

    @abstractmethod
    def run_check(
        self,
        analysis_data_map,
        rule_config: RuleLimit,
        rule: Rulebook,
        feedback: RuleFeedback,
    ) -> list[CheckResult]:
        pass

    def safe_float(self, value: str) -> Optional[float]:
        """Helper to convert YAML 'N/A' or numeric strings to floats safely."""
        if value == "N/A" or not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def format_feedback(
        self, template: str, measured: float, target: float, limit: float, unit: str = ""
    ) -> str:
        """
        Replaces placeholders in the feedback template with formatted values.
        """
        replacements = {
            "{measured}": f"{measured:.2f}{unit}",
            "{limit}": f"{limit:.2f}{unit}",
            "{target}": f"{target:.2f}{unit}",
        }

        formatted_msg = template
        for placeholder, value in replacements.items():
            formatted_msg = formatted_msg.replace(placeholder, value)

        return formatted_msg

    def severity_from_rule_config(self, rule_config: RuleLimit) -> Severity:
        try:
            return Severity[rule_config.binary_severity]
        except (KeyError, AttributeError):
            return Severity.ERROR
