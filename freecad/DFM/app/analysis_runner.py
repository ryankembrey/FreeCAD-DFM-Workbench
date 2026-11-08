# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import time
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
# events(kind, data): what the analysis is doing, for a progress view.
#   "plan"      {"rules": [{"rule", "analyzer", "items", "parallel", "cost"}], "total_steps"}
#   "workers"   {"workers": n, "analyzers": {id: {"done", "total", "busy",
#                "started", "finished"}}}   (the parallel phase, as it changes)
#   "analyzer"  {"id", "state": "running" | "done", "done", "total", "seconds"}
#                                            (analyzers measured in this process)
#   "check"     {"rule", "findings", "seconds"}
EventCallback = Optional[Callable[[str, dict], None]]


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
        events: EventCallback = None,
        **kwargs: Any,
    ) -> list[CheckResult]:
        """Run all active checks for the given process and material against a shape."""
        self._events = events
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
        self._add_run_context(kwargs, active_rules)

        self._emit_plan(active_rules, analyzer_counts, total_steps)

        prefs = kwargs.get("prefs", {})
        enable_timing = prefs.get("GeneralPrintTimingReport", False)
        timing = AnalysisTiming() if enable_timing else None
        if timing:
            timing.start_total()

        results: list[CheckResult] = []
        # Rules already checked (their analyzer finished on the workers while
        # others were still measured): rule -> its findings.
        self._checked: dict[Any, list[CheckResult]] = {}
        current_step = self._measure_in_parallel(
            active_rules, analyzer_counts, shape_occ, total_steps, timing,
            progress_cb, check_abort, process, target_material, **kwargs,
        )

        for rule_id in active_rules:
            if check_abort and check_abort():
                break

            if rule_id in self._checked:  # in rule order, like the others
                results.extend(self._checked[rule_id])
                continue

            check_class = get_check_class(rule_id)
            if not check_class:
                continue

            check_instance = check_class()
            analyzer_id = check_instance.required_analyzer_id

            if analyzer_id not in self.analyzer_cache:
                if timing:
                    timing.start(analyzer_id)

                iter_count = analyzer_counts.get(analyzer_id, 0)
                # One message per rule: the measuring done for it shows the
                # same words as the check that follows.
                success = self._run_analyzer(
                    analyzer_id,
                    shape_occ,
                    pull_direction,
                    current_step,
                    total_steps,
                    iter_count,
                    check_instance.progress_text,
                    progress_cb,
                    check_abort,
                    **kwargs,
                )

                if timing:
                    timing.stop_analyzer(analyzer_id)

                if not success:
                    continue
                current_step += iter_count

            results.extend(self._check_rule(
                rule_id, check_instance, process, target_material, current_step,
                total_steps, timing, progress_cb, **kwargs,
            ))
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
        self._add_run_context(kwargs, active_rules)
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

    @staticmethod
    def _add_run_context(kwargs: dict, active_rules: list) -> None:
        """What checks may share within one run.

        ``_active_rules``: the rules being checked, in order.
        ``run_notes``: a set a check adds to so a note common to several
        rules is reported once per run (such as "the nominal wall couldn't
        be measured").
        """
        kwargs["_active_rules"] = list(active_rules)
        kwargs["run_notes"] = set()

    def _check_rule(
        self,
        rule_id: Any,
        check_instance: Any,
        process: Any,
        target_material: Any,
        current_step: int,
        total_steps: int,
        timing: Optional[AnalysisTiming],
        progress_cb: ProgressCallback,
        **kwargs: Any,
    ) -> list[CheckResult]:
        """Check one rule against its (measured) analyzer and say so."""
        rule_config = self._resolve_rule_config(process, target_material, rule_id, **kwargs)
        if timing:
            timing.start(rule_id.name)
        check_started = time.perf_counter()
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
        self._emit("check", rule=rule_id, findings=len(check_results),
                   seconds=time.perf_counter() - check_started)
        return check_results

    def _emit(self, kind: str, **data: Any) -> None:
        events = getattr(self, "_events", None)
        if events is None:
            return
        try:
            events(kind, data)
        except Exception as exc:  # a progress view must never stop the analysis
            App.Console.PrintLog(f"DFM: progress view error: {exc}\n")

    def _emit_plan(self, active_rules: list, analyzer_counts: dict, total_steps: int) -> None:
        rules = []
        for rule_id in active_rules:
            check_class = get_check_class(rule_id)
            if not check_class:
                continue
            aid = check_class().required_analyzer_id
            analyzer_class = get_analyzer_class(aid)
            rules.append({
                "rule": rule_id,
                "analyzer": aid,
                "items": analyzer_counts.get(aid, 0),
                "parallel": bool(getattr(analyzer_class, "parallel", False)),
                "cost": float(getattr(analyzer_class, "relative_cost", 1)),
            })
        self._emit("plan", rules=rules, total_steps=total_steps)

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

    def _measure_in_parallel(
        self,
        active_rules: list,
        analyzer_counts: dict[str, int],
        shape_occ: TopoDS_Shape,
        total_steps: int,
        timing: Optional[AnalysisTiming],
        progress_cb: ProgressCallback,
        check_abort: AbortCallback,
        process: Any,
        target_material: Any,
        **kwargs: Any,
    ) -> int:
        """Measure with the worker processes what's worth it (see app/parallel).

        As each analyzer finishes there, its results go in the analyzer cache
        and its rules are checked at once (into self._checked), while the
        workers carry on with the rest. Anything it doesn't measure is
        measured in the rule loop as usual. Returns the progress steps used.
        """
        texts: dict[str, str] = {}
        wanted = []
        rules_of: dict[str, list] = {}  # analyzer -> [(rule, check)], in rule order
        for rule_id in active_rules:
            check_class = get_check_class(rule_id)
            if not check_class:
                continue
            check = check_class()
            aid = check.required_analyzer_id
            rules_of.setdefault(aid, []).append((rule_id, check))
            analyzer_class = get_analyzer_class(aid)
            if analyzer_class is None or not getattr(analyzer_class, "parallel", False):
                continue
            if aid not in texts:
                texts[aid] = check.progress_text
                wanted.append((aid, analyzer_class))
        if not wanted:
            return 0

        try:
            from .parallel.measure import measure_in_parallel
        except Exception as exc:
            App.Console.PrintWarning(f"DFM: parallel processing unavailable ({exc}).\n")
            return 0

        # Steps: faces measured so far plus rules checked so far.
        measured = [0]

        def on_progress(done: int, aid: str):
            measured[0] = done
            if progress_cb:
                progress_cb(done + len(self._checked), total_steps,
                            texts.get(aid, "Measuring the model"))

        def on_finished(aid: str, data: Any):
            self.analyzer_cache[aid] = data
            for rule_id, check in rules_of.get(aid, []):
                if check_abort and check_abort():
                    return
                self._checked[rule_id] = self._check_rule(
                    rule_id, check, process, target_material,
                    measured[0] + len(self._checked), total_steps, timing, progress_cb,
                    **kwargs,
                )

        fwd = {k: v for k, v in kwargs.items() if k not in ("face_index", "edge_index")}
        try:
            outcome = measure_in_parallel(
                wanted, shape_occ, self.face_index, self.edge_index, fwd,
                progress=on_progress,
                check_abort=check_abort,
                log=lambda text: App.Console.PrintLog(f"DFM: {text}\n"),
                warn=lambda text: App.Console.PrintWarning(text + "\n"),
                status=lambda workers, analyzers: self._emit(
                    "workers", workers=workers, analyzers=analyzers),
                finished=on_finished,
            )
        except Exception as exc:
            import traceback

            App.Console.PrintWarning(
                f"DFM: parallel measuring failed, measuring in FreeCAD's process ({exc}).\n")
            App.Console.PrintLog(traceback.format_exc())
            outcome = None
        if outcome is None or outcome.aborted:
            # Analyzers that did finish stay measured (and their rules checked).
            return sum(analyzer_counts.get(aid, 0) for aid in self.analyzer_cache) \
                + len(self._checked)

        steps = 0
        for aid, data in outcome.results.items():
            self.analyzer_cache[aid] = data
            steps += analyzer_counts.get(aid, 0)
        steps += len(self._checked)
        if timing:
            timing.record_parallel(outcome.wall_seconds, outcome.workers,
                                   {aid: outcome.worker_seconds.get(aid, 0.0)
                                    for aid in outcome.results})
        App.Console.PrintMessage(
            f"DFM: measured {', '.join(outcome.results)} on {outcome.workers} worker processes "
            f"in {outcome.wall_seconds:.1f} s.\n")
        return steps

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
        progress_text: str,
        progress_cb: ProgressCallback,
        check_abort: AbortCallback,
        **kwargs: Any,
    ) -> bool:
        """Instantiate and execute an analyzer, storing its result in the cache.

        *progress_text* is the message of the rule that needs this analyzer.
        """
        analyzer_class = get_analyzer_class(analyzer_id)
        if not analyzer_class:
            return False

        analyzer_instance = analyzer_class()

        # Show this rule's message as soon as its work starts, not only when
        # the analyzer reports its first face: its setup can take a moment,
        # and the bar would otherwise sit on the previous message (#111).
        if progress_cb:
            progress_cb(step_offset, total_steps, progress_text)

        started = time.perf_counter()
        self._emit("analyzer", id=analyzer_id, state="running", done=0, total=iter_count,
                   seconds=0.0)

        def on_progress(faces_done: int):
            if progress_cb:
                progress_cb(step_offset + faces_done, total_steps, progress_text)
            self._emit("analyzer", id=analyzer_id, state="running", done=faces_done,
                       total=iter_count, seconds=time.perf_counter() - started)

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
        self._emit("analyzer", id=analyzer_id, state="done", done=iter_count, total=iter_count,
                   seconds=time.perf_counter() - started)
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
            progress_cb(current_step, total_steps, check_instance.progress_text)

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
