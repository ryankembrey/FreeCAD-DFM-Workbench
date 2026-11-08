# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from abc import ABC, abstractmethod
from typing import Optional

from ...core.models import CheckResult
from ...core.processes.process import RuleFeedback, RuleLimit
from ...core.rules import SHAPE_FIELD_ATTRS, Rulebook
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
    def progress_text(self) -> str:
        """Plain words for the progress bar, such as "Checking for thin walls".

        This is the rule's only message: it shows while the model is measured
        for the rule and while the results are checked. Users don't need to
        know about analyzers and checks; say what is being checked.
        """
        name = self.name.replace("Checker", "").replace("Check", "").strip().lower()
        return f"Checking {name}"

    @property
    @abstractmethod
    def required_analyzer_id(self) -> str:
        """
        A string ID for the analyzer this check depends on.
        """
        pass

    #: Rules this one judges some faces for instead: their findings on the faces
    #: in covered_faces() are dropped while this rule is on (a rib's own draft
    #: rule replaces the general draft rule on the rib's sides).
    supersedes: tuple[Rulebook, ...] = ()

    def covered_faces(self, analysis_data, **kwargs) -> set[int]:
        """Face numbers this rule takes over from the rules it supersedes."""
        return set()

    #: Placeholders this check's messages can use besides the standard ones
    #: (message_placeholders): name -> what it is replaced with, in a few words.
    message_fields: dict[str, str] = {}

    def message_placeholders(self, rule: Rulebook) -> dict[str, str]:
        """Every {placeholder} a message of *rule* can use, with what it is
        replaced with: what the rule feedback editor offers, in this order.
        A test checks the process files use no others."""
        return {**self.value_placeholders(rule), **self.context_placeholders(rule),
                **self.message_fields}

    def value_placeholders(self, rule: Rulebook) -> dict[str, str]:
        """The measured value and the rule's thresholds."""
        fields = {"measured": "The measured value, with its unit"}
        for attr in ("target", "limit"):
            if attr in SHAPE_FIELD_ATTRS.get(rule.shape, ()):
                fields[attr] = f'The rule\'s "{rule.field_label_for(attr)}" value'
        return fields

    def context_placeholders(self, rule: Rulebook) -> dict[str, str]:
        """What the finding is about (none for checks that report faces)."""
        return {}

    @abstractmethod
    def run_check(
        self,
        analysis_data_map,
        rule_config: RuleLimit,
        rule: Rulebook,
        feedback: RuleFeedback,
        **kwargs,
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

    def format_number(self, value: float, unit: str = "") -> str:
        """A measured value or threshold as messages show it, such as "1.20mm"."""
        return f"{value:.2f}{unit}"

    def format_feedback(
        self,
        template: str,
        measured: float,
        target: float,
        limit: float,
        unit: str = "",
        **extra: object,
    ) -> str:
        """
        Replaces placeholders in the feedback template with formatted values.

        ``{measured}``, ``{target}`` and ``{limit}`` are formatted with the
        rule's unit (see format_number). Any *extra* keyword fills the
        placeholder of the same name as text: ``feature="Boss 3"`` fills
        ``{feature}``. Placeholders with no value are left as they are.
        """
        replacements = {
            "{measured}": self.format_number(measured, unit),
            "{limit}": self.format_number(limit, unit),
            "{target}": self.format_number(target, unit),
        }
        replacements.update({f"{{{key}}}": str(value) for key, value in extra.items()})

        formatted_msg = template
        for placeholder, value in replacements.items():
            formatted_msg = formatted_msg.replace(placeholder, value)

        return formatted_msg

    def severity_from_rule_config(self, rule_config: RuleLimit) -> Severity:
        try:
            return Severity[rule_config.binary_severity]
        except (KeyError, AttributeError):
            return Severity.ERROR
