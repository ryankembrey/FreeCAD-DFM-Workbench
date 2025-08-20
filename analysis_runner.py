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

from registry import dfm_registry
from enums import AnalysisType, CheckType
from data_types import CheckResult
from OCC.Core.TopoDS import TopoDS_Shape


class AnalysisRunner:
    def __init__(self):
        self._analysis_cache: dict[AnalysisType, dict] = {}

    def run(
        self,
        shape: TopoDS_Shape,
        active_checks_with_params: dict[CheckType, dict],
        global_params: dict,
    ) -> list[CheckResult]:
        self._analysis_cache.clear()
        all_findings: list[CheckResult] = []

        required_analysis_types = self._get_required_analysis_types(
            list(active_checks_with_params.keys())
        )
        print(f"Analysis Runner: Required analyzers: {[t.name for t in required_analysis_types]}")

        for analysis_type in required_analysis_types:
            analyzer = dfm_registry.get_analyzer(analysis_type)
            if analyzer:
                print(f"Analysis Runner: Executing {analyzer.name}...")
                raw_data = analyzer.execute(shape, **global_params)
                self._analysis_cache[analysis_type] = raw_data
            else:
                print(
                    f"!! WARNING: Analyzer for type '{analysis_type.name}' not found in registry. Checks depending on it will be skipped."
                )

        print("Analysis Runner: Executing checks...")
        for check_type, params in active_checks_with_params.items():
            check_to_run = dfm_registry.get_check(check_type)

            if not check_to_run:
                print(f"!! WARNING: Check for '{check_type.name}' not found in registry. Skipping.")
                continue

            required_data_map = {}
            can_run = True
            for dependency_type in check_to_run.dependencies:
                if dependency_type in self._analysis_cache:
                    required_data_map[dependency_type] = self._analysis_cache[dependency_type]
                else:
                    can_run = False
                    break

            if can_run:
                findings = list(check_to_run.run_check(required_data_map, params, check_type))
                if findings:
                    all_findings.extend(findings)

        return all_findings

    def _get_required_analysis_types(
        self, selected_check_types: list[CheckType]
    ) -> set[AnalysisType]:
        """
        Determines the unique set of analyzers needed for a list of checks.
        """
        required_types: set[AnalysisType] = set()
        for check_type in selected_check_types:
            check_instance = dfm_registry.get_check(check_type)
            if check_instance and hasattr(check_instance, "dependencies"):
                required_types.update(check_instance.dependencies)
        return required_types
