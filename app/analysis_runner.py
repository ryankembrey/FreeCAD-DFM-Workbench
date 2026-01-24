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

import FreeCAD
from typing import Any

import Part
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.gp import gp_Dir

from dfm.registries.process_registry import ProcessRegistry
from dfm.registries.checks_registry import get_check_class
from dfm.registries.analyzers_registry import get_analyzer_class
from dfm.rules import Rulebook
from dfm.models import CheckResult, Severity


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
        print(f"\n--- Starting DFM Analysis ---")
        print(f"Process: {process_name}, Material: {material_name}")

        all_results: list[CheckResult] = []
        self.analyzer_cache.clear()

        # 1. Get the Process definition from the ProcessRegistry
        process_registry = ProcessRegistry.get_instance()
        process = process_registry.get_process_by_id(process_name)
        if not process:
            FreeCAD.Console.PrintDeveloperError(
                f"Analysis failed: Process ID '{process_name}' not found in registry.\n"
            )
            return []

        material = process.materials.get(material_name)
        if not material:
            FreeCAD.Console.PrintDeveloperError(
                f"Analysis failed: Material '{material_name}' not found in process '{process_name}'.\n"
            )
            return []

        material_params = material.get("parameters", {})
        shape_occ: TopoDS_Shape = Part.__toPythonOCC__(shape)

        # 2. Iterate through all the rule IDs declared in the process's YAML file
        for rule_string in process.rules:
            try:
                rule_id = Rulebook[rule_string]
            except ValueError:
                FreeCAD.Console.PrintDeveloperError(
                    f"Skipping unknown rule '{rule_string}' defined in '{process_name}.yaml'.\n"
                )
                continue

            # 3. Find the correct Check class for this rule from the check_registry
            check_class = get_check_class(rule_id)
            if not check_class:
                FreeCAD.Console.PrintDeveloperError(
                    f"Skipping rule '{rule_id.name}': No registered Check class found.\n"
                )
                continue

            check_instance = check_class()

            # 4. Determine which Analyzer this Check depends on
            analyzer_id = check_instance.required_analyzer_id
            if not analyzer_id:
                FreeCAD.Console.PrintDeveloperError(
                    f"Skipping rule '{rule_id.name}': Check class '{check_instance.name}' does not specify a required_analyzer_id.\n"
                )
                continue

            # 5. --- The Caching Logic ---
            # Check if we have already run this analyzer during this analysis.
            if analyzer_id not in self.analyzer_cache:
                print(f"Cache miss. Running analyzer: '{analyzer_id}'...")

                # 6. Find the correct Analyzer class from the analyzer_registry
                analyzer_class = get_analyzer_class(analyzer_id)
                if not analyzer_class:
                    FreeCAD.Console.PrintDeveloperError(
                        f"Cannot run check '{rule_id.name}': Required analyzer '{analyzer_id}' not found.\n"
                    )
                    continue

                analyzer_instance = analyzer_class()

                # 7. Execute the analysis and store its results in the cache
                analysis_data = analyzer_instance.execute(shape_occ, pull_direction=pull_direction)
                self.analyzer_cache[analyzer_id] = analysis_data
                print(f"Analyzer '{analyzer_id}' finished.")
            else:
                print(f"Cache hit for analyzer: '{analyzer_id}'. Using cached data.")

            # 8. Run the check using the (possibly cached) analysis data
            analysis_data = self.analyzer_cache[analyzer_id]

            try:
                # The check is responsible for yielding/returning CheckResult objects
                check_findings = check_instance.run_check(analysis_data, material_params, rule_id)
                all_results.extend(check_findings)
            except Exception as e:
                FreeCAD.Console.PrintError(f"Error while running check '{rule_id.name}': {e}\n")

        print(f"--- DFM Analysis Complete. Found {len(all_results)} issues. ---\n")
        return all_results
