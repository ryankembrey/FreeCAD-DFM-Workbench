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

from enums import AnalysisType, CheckType, ProcessType


class DFMRegistry:
    """
    A singleton class to hold a central registry of all available
    Analyzers and Checks in the DFM Workbench.
    """

    def __init__(self):
        print(" -> DFMRegistry created.")
        self._processes = {}
        self._analyzers = {}
        self._checks = {}

    def register_process(self, process_instance: "BaseProcess"):
        processer_name = process_instance.name
        print(f" -> Registering Process: {processer_name}")
        self._processes[processer_name] = process_instance

    def register_analyzer(self, analyzer_instance: "BaseAnalyzer"):
        analyzer_type = analyzer_instance.analysis_type
        print(f" -> Registering Analyzer: {analyzer_type}")
        self._analyzers[analyzer_type] = analyzer_instance

    def register_check(self, check_instance: "BaseCheck"):
        if not hasattr(check_instance, "handled_check_types"):
            raise AttributeError(
                f"DFM Registration Error: The check class '{type(check_instance).__name__}' "
                f"must have a 'handled_check_types' class property."
            )

        for check_type in check_instance.handled_check_types:
            if check_type in self._checks:
                print(f"!! WARNING: Overwriting existing Check type: {check_type.name}")
            print(f" -> Registering Check: {check_type}")
            self._checks[check_type] = check_instance

    def get_process(self, process_type) -> "BaseProcess | None":
        return self._processes.get(process_type)

    def get_analyzer(self, analysis_type) -> "BaseAnalyzer | None":
        return self._analyzers.get(analysis_type)

    def get_check(self, check_type: CheckType) -> "BaseCheck | None":
        return self._checks.get(check_type)


dfm_registry = DFMRegistry()
