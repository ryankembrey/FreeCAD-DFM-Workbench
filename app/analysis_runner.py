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


from typing import Any, Callable, Optional

import FreeCAD as App  # type: ignore
import Part  # type: ignore

from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.gp import gp_Dir

from dfm.registries.process_registry import ProcessRegistry
from dfm.registries.checks_registry import get_check_class
from dfm.registries.analyzers_registry import get_analyzer_class
from dfm.models import CheckResult, ProcessRequirement
from dfm.processes.process import RuleFeedback, RuleLimit

from app.analysis_timer import AnalysisTiming

ENABLE_TIMING = False

ProgressCallback = Optional[Callable[[int, int, str], None]]
AbortCallback = Optional[Callable[[], bool]]


class AnalysisRunner:
    """Orchestrates DFM analysis by running analyzers and checks for a given process and material."""

    def __init__(self):
        self.analyzer_cache: dict[str, Any] = {}

    def run_analysis(
        self,
        process_name: str,
        material_name: str,
        shape: Part.Shape,
        progress_cb: ProgressCallback = None,
        check_abort: AbortCallback = None,
        **kwargs: Any,
    ) -> list[CheckResult]:
        """Run all active checks for the given process and material against a shape."""
        self.analyzer_cache.clear()

        process = ProcessRegistry.get_instance().get_process_by_name(process_name)
        if not process:
            App.Console.PrintDeveloperError(f"Process '{process_name}' not found.\n")
            return []

        target_material = process.materials.get(material_name)
        if not target_material and material_name != "Default":
            App.Console.PrintDeveloperError(f"Material '{material_name}' not found.\n")
            return []

        shape_occ: TopoDS_Shape = Part.__toPythonOCC__(shape)
        pull_direction = kwargs.get(ProcessRequirement.PULL_DIRECTION.name, gp_Dir(0, 0, 1))
        num_faces = len(shape.Faces)
        active_rules, total_steps = self._calculate_total_steps(process, num_faces)

        timing = AnalysisTiming() if ENABLE_TIMING else None
        if timing:
            timing.start_total()

        results: list[CheckResult] = []
        current_step = 0

        for rule_id in active_rules:
            if check_abort and check_abort():
                break

            check_class = get_check_class(rule_id)
            if not check_class:
                continue

            check_instance = check_class()
            analyzer_id = check_instance.required_analyzer_id

            if analyzer_id not in self.analyzer_cache:
                if timing:
                    timing.start(analyzer_id)

                success = self._run_analyzer(
                    analyzer_id,
                    shape_occ,
                    pull_direction,
                    current_step,
                    total_steps,
                    num_faces,
                    progress_cb,
                    check_abort,
                    **kwargs,
                )

                if timing:
                    timing.stop_analyzer(analyzer_id)

                if not success:
                    continue
                current_step += num_faces

            rule_config = self._resolve_rule_config(process, target_material, rule_id)

            if timing:
                timing.start(rule_id.name)

            check_results = self._execute_check(
                rule_id,
                check_instance,
                process,
                rule_config,
                current_step,
                total_steps,
                progress_cb,
            )

            if timing:
                timing.stop_check(rule_id.name)

            results.extend(check_results)
            current_step += 1

        if timing:
            timing.stop_total()
            timing.report()

        return results

    def _calculate_total_steps(self, process: Any, num_faces: int) -> tuple[list, int]:
        """Return the ordered list of active rule IDs and the total progress step count."""
        active_rules = []
        unique_analyzers = set()

        for rule_id in process.active_rules:
            check_class = get_check_class(rule_id)
            if check_class:
                unique_analyzers.add(check_class().required_analyzer_id)
                active_rules.append(rule_id)

        total_steps = (len(unique_analyzers) * num_faces) + len(active_rules)
        return active_rules, total_steps

    def _resolve_rule_config(self, process: Any, target_material: Any, rule_id: Any) -> RuleLimit:
        """Build a RuleLimit by layering the Default material config with any material override."""
        config = RuleLimit(target="N/A", limit="N/A", binary_severity=None)

        default_material = process.materials.get("Default")
        if default_material and rule_id in default_material.rule_limits:
            default_limit = default_material.rule_limits[rule_id]
            config.target = default_limit.target
            config.limit = default_limit.limit
            config.binary_severity = default_limit.binary_severity

        if target_material and rule_id in target_material.rule_limits:
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

    def _run_analyzer(
        self,
        analyzer_id: str,
        shape_occ: Any,
        pull_direction: Any,
        step_offset: int,
        total_steps: int,
        num_faces: int,
        progress_cb: ProgressCallback,
        check_abort: AbortCallback,
        **kwargs: Any,
    ) -> bool:
        """Instantiate and execute an analyzer, storing its result in the cache."""
        analyzer_class = get_analyzer_class(analyzer_id)
        if not analyzer_class:
            return False

        analyzer_instance = analyzer_class()

        def on_progress(faces_done: int):
            if progress_cb:
                progress_cb(step_offset + faces_done, total_steps, analyzer_instance.name)

        self.analyzer_cache[analyzer_id] = analyzer_instance.execute(
            shape_occ,
            pull_direction=pull_direction,
            progress_cb=on_progress,
            check_abort=check_abort,
            **kwargs,
        )
        return True

    def _execute_check(
        self,
        rule_id: Any,
        check_instance: Any,
        process: Any,
        rule_config: RuleLimit,
        current_step: int,
        total_steps: int,
        progress_cb: ProgressCallback,
    ) -> list[CheckResult]:
        """Run a single check against cached analyzer data and return its results."""
        analysis_data = self.analyzer_cache[check_instance.required_analyzer_id]
        feedback_templates = process.rule_feedback.get(rule_id) or RuleFeedback()

        if progress_cb:
            progress_cb(current_step, total_steps, check_instance.name)

        try:
            return check_instance.run_check(
                analysis_data, rule_config, rule_id, feedback=feedback_templates
            )
        except Exception as e:
            App.Console.PrintError(f"Error in {rule_id.name}: {e}\n")
            return []
