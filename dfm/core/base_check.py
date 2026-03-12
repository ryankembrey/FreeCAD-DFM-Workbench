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

from abc import ABC, abstractmethod
from typing import Optional

from dfm.models import CheckResult
from dfm.processes.process import RuleFeedback, RuleLimit
from dfm.rules import Rulebook


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
            "{value}": f"{measured:.2f}{unit}",
            "{limit}": f"{limit:.2f}{unit}",
            "{target}": f"{target:.2f}{unit}",
        }

        formatted_msg = template
        for placeholder, value in replacements.items():
            formatted_msg = formatted_msg.replace(placeholder, value)

        return formatted_msg
