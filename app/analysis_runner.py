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

from typing import Any

import FreeCAD as App  # type: ignore
import Part  # type: ignore

from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.gp import gp_Dir

from dfm.registries.process_registry import ProcessRegistry
from dfm.registries.checks_registry import get_check_class
from dfm.registries.analyzers_registry import get_analyzer_class
from dfm.rules import Rulebook
from dfm.models import CheckResult, Severity
from dfm.processes.process import RuleFeedback, RuleLimit


class AnalysisRunner:
    """
    Orchestrates a DFM analysis by dynamically connecting processes,
    checks, and analyzers from the registries.
    """

    def __init__(self):
        """Initializes the runner with an empty cache for this run."""
        self.analyzer_cache: dict[str, Any] = {}

    def run_analysis(
        self, process_name: str, material_name: str, shape: Part.Shape, **kwargs: Any
    ) -> list[CheckResult]:
        """
        The main entry point for running a complete DFM analysis.

        Args:
            process_name: The unique ID of the process to run (e.g., "PIM_STANDARD").
            material_name: The name of the material to use (e.g., "ABS").
            shape: The FreeCAD Part.Shape object to be analyzed.

        Returns:
            A list of CheckResult objects detailing all the findings.
        """
        pull_direction = kwargs.get("pull_direction", gp_Dir(0, 0, 1))

        all_results: list[CheckResult] = []
        self.analyzer_cache.clear()

        registry = ProcessRegistry.get_instance()
        process = registry.get_process_by_name(process_name)
        if not process:
            App.Console.PrintDeveloperError(f"Process '{process_name}' not found.\n")
            return []

        default_mat = process.materials.get("Default")
        target_material = process.materials.get(material_name)

        if not target_material and material_name != "Default":
            App.Console.PrintDeveloperError(f"Material '{material_name}' not found.\n")
            return []

        shape_occ: TopoDS_Shape = Part.__toPythonOCC__(shape)

        for rule_id in process.active_rules:
            rule_config = RuleLimit(target="N/A", limit="N/A")

            if default_mat and rule_id in default_mat.rule_limits:
                d_limit = default_mat.rule_limits[rule_id]
                rule_config.target = d_limit.target
                rule_config.limit = d_limit.limit

            if target_material and rule_id in target_material.rule_limits:
                m_limit = target_material.rule_limits[rule_id]
                if m_limit.target != "N/A":
                    rule_config.target = m_limit.target
                if m_limit.limit != "N/A":
                    rule_config.limit = m_limit.limit

            check_class = get_check_class(rule_id)
            if not check_class:
                continue

            check_instance = check_class()
            analyzer_id = check_instance.required_analyzer_id

            if analyzer_id not in self.analyzer_cache:
                analyzer_class = get_analyzer_class(analyzer_id)
                if not analyzer_class:
                    continue

                analyzer_instance = analyzer_class()
                self.analyzer_cache[analyzer_id] = analyzer_instance.execute(
                    shape_occ, pull_direction=pull_direction
                )

            analysis_data = self.analyzer_cache[analyzer_id]

            feedback_templates = process.rule_feedback.get(rule_id) or RuleFeedback()

            try:
                check_findings = check_instance.run_check(
                    analysis_data, rule_config, rule_id, feedback=feedback_templates
                )
                all_results.extend(check_findings)
            except Exception as e:
                App.Console.PrintError(f"Error in {rule_id.name}: {e}\n")

        return all_results
