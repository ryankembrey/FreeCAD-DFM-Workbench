# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from typing import Any, Callable, Optional

import FreeCAD as App  # type: ignore
import Part  # type: ignore

from OCP.TopoDS import TopoDS_Shape
from OCP.gp import gp_Dir

from ..core.registries.process_registry import ProcessRegistry
from ..core.registries.checks_registry import get_check_class
from ..core.registries.analyzers_registry import get_analyzer_class
from ..core.models import CheckResult, ProcessRequirement
from ..core.processes.process import RuleFeedback, RuleLimit
from ..core.processes.threshold_resolvers import get_resolver
from ..core.rules import Rulebook
from ..core.utils.conversion import freecad_to_ocp
from ..core.utils.geometry import EdgeIndex, FaceIndex, face_area
from .prefs import dfm_params

from ..app.analysis_timer import AnalysisTiming


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
        kwargs["prefs"] = self._load_prefs()

        process = ProcessRegistry.get_instance().get_process_by_name(process_name)
        if not process:
            App.Console.PrintDeveloperError(f"Process '{process_name}' not found.\n")
            return []

        target_material = process.materials.get(material_name)
        if not target_material and material_name != "Default":
            App.Console.PrintDeveloperError(f"Material '{material_name}' not found.\n")
            return []

        shape_occ: TopoDS_Shape = freecad_to_ocp(shape)
        pull_direction = kwargs.get(ProcessRequirement.PULL_DIRECTION.name, gp_Dir(0, 0, 1))
        active_rules, analyzer_counts, total_steps = self._calculate_total_steps(process, shape_occ)

        self.face_index = FaceIndex(shape_occ)
        self.edge_index = EdgeIndex(shape_occ)

        # Precompute total surface area for checks that need it (e.g. support %).
        self.total_surface_area = sum(face_area(f) for f in self.face_index)
        kwargs["face_index"] = self.face_index
        kwargs["total_surface_area"] = self.total_surface_area
        kwargs["_target_material"] = target_material

        prefs = kwargs.get("prefs", {})
        enable_timing = prefs.get("GeneralPrintTimingReport", False)
        timing = AnalysisTiming() if enable_timing else None
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

                iter_count = analyzer_counts.get(analyzer_id, 0)
                success = self._run_analyzer(
                    analyzer_id,
                    shape_occ,
                    pull_direction,
                    current_step,
                    total_steps,
                    iter_count,
                    progress_cb,
                    check_abort,
                    **kwargs,
                )

                if timing:
                    timing.stop_analyzer(analyzer_id)

                if not success:
                    continue
                current_step += iter_count

            rule_config = self._resolve_rule_config(
                process, target_material, rule_id, **kwargs,
            )

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
                **kwargs,
            )

            if timing:
                timing.stop_check(rule_id.name)

            results.extend(check_results)
            current_step += 1

        if timing:
            timing.stop_total()
            timing.report()

        return results

    def rerun_checks_only(
        self,
        process_name: str,
        material_name: str,
        progress_cb: ProgressCallback = None,
        **kwargs: Any,
    ) -> list[CheckResult]:
        """Re-evaluate checks using the existing analyzer cache.

        This is used when the user switches material in the results panel.
        Analyzers are geometry-dependent and don't change with material;
        only the checks (which compare analyzer output against material
        thresholds) need to be re-run.

        The caller must ensure ``self.analyzer_cache``, ``self.face_index``
        and ``self.edge_index`` are still populated from a prior
        ``run_analysis`` call on the same shape.
        """
        if not self.analyzer_cache:
            App.Console.PrintError("Cannot rerun checks: no analyzer cache.\n")
            return []

        kwargs["prefs"] = self._load_prefs()

        process = ProcessRegistry.get_instance().get_process_by_name(process_name)
        if not process:
            return []

        target_material = process.materials.get(material_name)
        if not target_material and material_name != "Default":
            return []

        # Re-inject runtime data that checks may need.
        kwargs["face_index"] = self.face_index
        kwargs["total_surface_area"] = self.total_surface_area
        kwargs["_target_material"] = target_material

        active_rules = [
            rule_id for rule_id in process.active_rules if get_check_class(rule_id)
        ]
        total_steps = len(active_rules)
        results: list[CheckResult] = []

        for current_step, rule_id in enumerate(active_rules):
            check_class = get_check_class(rule_id)
            if not check_class:
                continue

            check_instance = check_class()
            analyzer_id = check_instance.required_analyzer_id

            if analyzer_id not in self.analyzer_cache:
                continue

            rule_config = self._resolve_rule_config(
                process, target_material, rule_id, **kwargs,
            )

            check_results = self._execute_check(
                rule_id,
                check_instance,
                process,
                rule_config,
                current_step,
                total_steps,
                progress_cb,
                **kwargs,
            )
            results.extend(check_results)

        return results

    def _load_prefs(self) -> dict:
        params = dfm_params()
        contents = params.GetContents()
        if not contents:
            return {}
        return {key: value for _, key, value in contents}

    def _calculate_total_steps(
        self, process: Any, shape_occ: TopoDS_Shape,
    ) -> tuple[list, dict[str, int], int]:
        """Return active rule IDs, per-analyzer iteration counts, and total step count."""
        active_rules = []
        analyzer_counts: dict[str, int] = {}

        for rule_id in process.active_rules:
            check_class = get_check_class(rule_id)
            if check_class:
                analyzer_id = check_class().required_analyzer_id
                if analyzer_id not in analyzer_counts:
                    analyzer_class = get_analyzer_class(analyzer_id)
                    if analyzer_class:
                        analyzer_counts[analyzer_id] = analyzer_class().iteration_count(shape_occ)
                    else:
                        analyzer_counts[analyzer_id] = 0
                active_rules.append(rule_id)

        total_steps = sum(analyzer_counts.values()) + len(active_rules)
        return active_rules, analyzer_counts, total_steps

    def _resolve_rule_config(
        self, process: Any, target_material: Any, rule_id: Any, **kwargs: Any,
    ) -> RuleLimit:
        """Build a RuleLimit by delegating to the process archetype's resolver."""
        resolver = get_resolver(process.archetype)
        return resolver.resolve(rule_id, process, target_material, **kwargs)

    def _run_analyzer(
        self,
        analyzer_id: str,
        shape_occ: Any,
        pull_direction: Any,
        step_offset: int,
        total_steps: int,
        iter_count: int,
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

        # face_index and edge_index are passed positionally below, but they
        # also live in kwargs (injected for checks that need them).  Strip
        # them so execute() doesn't receive duplicate keyword arguments.
        fwd = {k: v for k, v in kwargs.items() if k not in ("face_index", "edge_index")}
        self.analyzer_cache[analyzer_id] = analyzer_instance.execute(
            shape_occ,
            self.face_index,
            self.edge_index,
            progress_cb=on_progress,
            check_abort=check_abort,
            **fwd,
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
        **kwargs,
    ) -> list[CheckResult]:
        """Run a single check against cached analyzer data and return its results."""
        analysis_data = self.analyzer_cache[check_instance.required_analyzer_id]
        feedback_templates = process.rule_feedback.get(rule_id) or RuleFeedback()

        if progress_cb:
            progress_cb(current_step, total_steps, check_instance.name)

        # Inject cross-rule config for checks that depend on another rule's
        # thresholds.  MAX_SUPPORT_MATERIAL needs the overhang angle limit.
        if rule_id == Rulebook.MAX_SUPPORT_MATERIAL:
            target_material = kwargs.get("_target_material")
            overhang_config = self._resolve_rule_config(
                process, target_material, Rulebook.MAX_OVERHANG_ANGLE, **kwargs,
            )
            kwargs["overhang_rule_config"] = overhang_config

        try:
            return check_instance.run_check(
                analysis_data,
                rule_config,
                rule_id,
                feedback=feedback_templates,
                **kwargs,
            )
        except Exception as e:
            App.Console.PrintError(f"Error in {rule_id.name}: {e}\n")
            return []
