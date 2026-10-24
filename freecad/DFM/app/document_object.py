# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""
DFM Results FeaturePython object.

Stores analysis results in native FreeCAD document properties so they
persist inside the .FCStd file.  Appears in the model tree as
"DFM Results" under the analysed body.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Optional

import FreeCAD as App  # type: ignore

from ..core.models import CheckResult, GeometryRef, Severity
from ..core.rules import Rulebook


# Property names stored on the FeaturePython object

_PROP_TARGET_LABEL = "DFM_TargetLabel"
_PROP_PROCESS = "DFM_Process"
_PROP_MATERIAL = "DFM_Material"
_PROP_VERDICT = "DFM_Verdict"
_PROP_TIMESTAMP = "DFM_Timestamp"
_PROP_SHAPE_HASH = "DFM_ShapeHash"
_PROP_FINDINGS_JSON = "DFM_FindingsJSON"
_PROP_ANALYZER_CACHE_JSON = "DFM_AnalyzerCacheJSON"
_PROP_RUN_SETTINGS_JSON = "DFM_RunSettingsJSON"
_PROP_STALE = "DFM_Stale"

_PROP_GROUP = "DFM"


def compute_shape_hash(shape) -> str:
    """Compute a hash of a Part.Shape for change detection.

    Uses the BRep (boundary representation) string which captures the
    full geometry and topology.  The BRep output is deterministic for a
    given shape, so a hash of it reliably detects any modelling change.
    """
    try:
        brep = shape.exportBrepToString()
        h = hashlib.sha256(brep.encode()).hexdigest()[:32]
        return h
    except Exception as exc:
        # Fallback: geometric fingerprint for shapes that can't export BRep
        bb = shape.BoundBox
        parts = [
            f"{bb.XMin:.6f},{bb.YMin:.6f},{bb.ZMin:.6f}",
            f"{bb.XMax:.6f},{bb.YMax:.6f},{bb.ZMax:.6f}",
            f"V{shape.Volume:.6f}",
            f"A{shape.Area:.6f}",
            f"F{len(shape.Faces)}",
            f"E{len(shape.Edges)}",
        ]
        h = hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]
        return h


# Serialisation helpers


def _result_to_dict(result: CheckResult) -> dict:
    return {
        "rule": result.rule_id.name,
        "severity": result.severity.name,
        "overview": result.overview,
        "message": result.message,
        "value": result.value,
        "limit": result.limit,
        "comparison": result.comparison,
        "unit": result.unit,
        "ignore": result.ignore,
        "refs": [{"type": ref.type, "index": ref.index, "label": ref.label} for ref in result.refs],
    }


def _result_from_dict(d: dict) -> CheckResult:
    return CheckResult(
        rule_id=Rulebook[d["rule"]],
        severity=Severity[d["severity"]],
        overview=d.get("overview", ""),
        message=d.get("message", ""),
        value=d.get("value"),
        limit=d.get("limit"),
        comparison=d.get("comparison", ""),
        unit=d.get("unit", ""),
        ignore=d.get("ignore", False),
        refs=[
            GeometryRef(type=ref["type"], index=ref["index"], label=ref["label"])
            for ref in d.get("refs", [])
        ],
    )


def results_to_json(results: list[CheckResult]) -> str:
    return json.dumps([_result_to_dict(r) for r in results])


def results_from_json(json_str: str) -> list[CheckResult]:
    if not json_str:
        return []
    try:
        data = json.loads(json_str)
        return [_result_from_dict(d) for d in data]
    except (json.JSONDecodeError, KeyError):
        return []


# FeaturePython proxy


class DFMResultsObject:
    """Proxy attached to the FeaturePython object in the document tree."""

    def __init__(self, obj):
        obj.Proxy = self
        self._add_properties(obj)

    # noinspection PyMethodMayBeStatic
    def _add_properties(self, obj):
        """Add custom properties if they don't already exist."""
        existing = set(obj.PropertiesList)

        def _add(name, ptype, group, doc, default=None):
            if name not in existing:
                obj.addProperty(ptype, name, group, doc)
                if default is not None:
                    setattr(obj, name, default)

        _add(_PROP_TARGET_LABEL, "App::PropertyString", _PROP_GROUP, "Label of the analysed shape")
        _add(_PROP_PROCESS, "App::PropertyString", _PROP_GROUP, "Manufacturing process used")
        _add(_PROP_MATERIAL, "App::PropertyString", _PROP_GROUP, "Material used for this analysis")
        _add(_PROP_VERDICT, "App::PropertyString", _PROP_GROUP, "Pass/Fail verdict")
        _add(
            _PROP_TIMESTAMP,
            "App::PropertyString",
            _PROP_GROUP,
            "ISO timestamp of when the analysis ran",
        )
        _add(
            _PROP_SHAPE_HASH,
            "App::PropertyString",
            _PROP_GROUP,
            "Hash of the analysed shape for change detection",
        )
        _add(
            _PROP_FINDINGS_JSON,
            "App::PropertyString",
            _PROP_GROUP,
            "JSON-encoded analysis findings",
        )
        _add(
            _PROP_ANALYZER_CACHE_JSON,
            "App::PropertyString",
            _PROP_GROUP,
            "JSON-encoded analyzer cache keys (not data)",
        )
        _add(
            _PROP_RUN_SETTINGS_JSON,
            "App::PropertyString",
            _PROP_GROUP,
            "JSON-encoded settings used for the run",
        )
        _add(
            _PROP_STALE,
            "App::PropertyBool",
            _PROP_GROUP,
            "True when the shape has changed since this analysis",
            False,
        )

    # FreeCAD persistence callbacks
    def onDocumentRestored(self, obj):
        self._add_properties(obj)

    def execute(self, obj):
        pass

    def __getstate__(self):
        return None

    def __setstate__(self, _state):
        pass


class DFMResultsViewProvider:
    """ViewProvider for the DFM Results tree item."""

    def __init__(self, vobj):
        vobj.Proxy = self

    def getIcon(self):
        return ":/icons/dfm_analysis.svg"

    def attach(self, vobj):
        self.Object = vobj.Object
        try:
            from pivy import coin  # type: ignore

            vobj.addDisplayMode(coin.SoSeparator(), "Default")
        except Exception:
            pass
        try:
            vobj.Visibility = True
        except Exception:
            pass

    def getDisplayModes(self, vobj):
        return ["Default"]

    def getDefaultDisplayMode(self):
        return "Default"

    def setDisplayMode(self, mode):
        return mode

    def claimChildren(self):
        return []

    def doubleClicked(self, vobj):
        """Open the saved DFM results panel when the tree item is double-clicked."""
        try:
            self._open_results_panel(vobj.Object)
        except Exception as e:
            import traceback

            App.Console.PrintError(f"Failed to open DFM results: {e}\n")
            App.Console.PrintError(traceback.format_exc())
        return True

    def _open_results_panel(self, obj):
        """Load stored results and open the results panel."""
        import FreeCADGui as Gui  # type: ignore
        from pathlib import Path

        from ..core.registries.process_registry import ProcessRegistry
        from ..gui.results.bridge import DFMViewProvider as ViewBridge
        from ..gui.results.models import DFMReportModel
        from ..gui.results.presenter import TaskResultsPresenter
        from ..gui.task_results import TaskResults
        from ..app.history import HistoryManager

        data = load_results_from_document(obj)
        if data is None:
            App.Console.PrintWarning("DFM: No results stored in this object.\n")
            return

        doc = obj.Document
        target_label = data["target_label"]

        # Find the target object in the document
        target_obj = None
        for candidate in doc.Objects:
            if candidate.Label == target_label and hasattr(candidate, "Shape"):
                target_obj = candidate
                break

        if target_obj is None:
            App.Console.PrintError(
                f"DFM: Cannot find target object '{target_label}' in the document.\n"
            )
            return

        #  Shape change check
        # Detect if the shape changed since the last analysis, whether
        # the document was just reopened or modified in the same session.
        if check_shape_changed(obj, target_obj.Shape):
            mark_stale(obj, True)
            from PySide6.QtWidgets import QMessageBox

            msg = QMessageBox()
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("DFM: Shape Changed")
            msg.setText(
                f"The shape '{target_label}' has changed since the last DFM analysis.\n\n"
                f"The saved results may no longer be accurate. "
                f"Topological references (face/edge labels) may have shifted."
            )
            msg.setInformativeText("Would you like to rerun the analysis with the same settings?")
            msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            msg.setDefaultButton(QMessageBox.StandardButton.Yes)
            result = msg.exec()
            if result == QMessageBox.StandardButton.Yes:
                # Defer to next event-loop tick so the message box is fully
                # gone before we tear down and replace the dialog.
                from PySide6.QtCore import QTimer

                _data = data
                _target = target_obj

                def _do_rerun():
                    from ..gui.task_setup import TaskSetup

                    Gui.Selection.clearSelection()
                    Gui.Selection.addSelection(_target)
                    Gui.Control.closeDialog()
                    task = TaskSetup(
                        preset_process=_data["process"],
                        preset_material=_data["material"],
                        auto_run=True,
                    )
                    Gui.Control.showDialog(task)

                QTimer.singleShot(0, _do_rerun)
                return
            # User chose No — show the stale results anyway

        # Get the process from the registry
        registry = ProcessRegistry.get_instance()
        process = registry.get_process_by_id(data["process"])
        if process is None:
            App.Console.PrintError(f"DFM: Process '{data['process']}' not found in registry.\n")
            return

        material_name = data["material"]
        results = data["findings"]
        run_settings = data.get("run_settings", {})

        history_manager = HistoryManager(Path(App.getUserAppDataDir()))

        report_model = DFMReportModel(
            results=results,
            process=process,
            material=material_name,
        )
        view_bridge = ViewBridge(target_obj)
        results_view = TaskResults()

        # Close any open task panel first
        Gui.Control.closeDialog()

        TaskResultsPresenter(
            view=results_view,
            model=report_model,
            bridge=view_bridge,
            history_manager=history_manager,
            doc_name=doc.Name,
            shape_name=target_obj.Label,
            runner=None,  # No analyzer cache — full rerun needed for material switching
            run_settings=run_settings,
        )

    def __getstate__(self):
        return None

    def __setstate__(self, _state):
        pass


#  Public API


def find_dfm_results_object(doc, target_label: str) -> Optional[App.DocumentObject]:
    """Find an existing DFM Results object for a given target in the document."""
    for obj in doc.Objects:
        if (
            hasattr(obj, "Proxy")
            and isinstance(getattr(obj, "Proxy", None), DFMResultsObject)
            and hasattr(obj, _PROP_TARGET_LABEL)
            and getattr(obj, _PROP_TARGET_LABEL) == target_label
        ):
            return obj
    return None


def save_results_to_document(
    doc,
    target_object,
    process_name: str,
    material_name: str,
    verdict: str,
    results: list[CheckResult],
    run_settings: Optional[dict] = None,
) -> App.DocumentObject:
    """Create or update the DFM Results object in the document tree.

    Returns the FeaturePython object.
    """
    target_label = target_object.Label
    obj = find_dfm_results_object(doc, target_label)

    if obj is None:
        obj = doc.addObject("App::FeaturePython", "DFM_Results")
        DFMResultsObject(obj)
        if hasattr(obj, "ViewObject") and obj.ViewObject is not None:
            DFMResultsViewProvider(obj.ViewObject)
        obj.Label = f"DFM Results ({target_label})"

    setattr(obj, _PROP_TARGET_LABEL, target_label)
    setattr(obj, _PROP_PROCESS, process_name)
    setattr(obj, _PROP_MATERIAL, material_name)
    setattr(obj, _PROP_VERDICT, verdict)
    setattr(obj, _PROP_TIMESTAMP, datetime.now().isoformat(timespec="seconds"))
    shape_hash = compute_shape_hash(target_object.Shape)
    setattr(obj, _PROP_SHAPE_HASH, shape_hash)
    setattr(obj, _PROP_FINDINGS_JSON, results_to_json(results))
    setattr(obj, _PROP_STALE, False)

    settings = run_settings or {}
    setattr(obj, _PROP_RUN_SETTINGS_JSON, json.dumps(settings))

    doc.recompute()
    return obj


def load_results_from_document(obj) -> Optional[dict]:
    """Read stored results from a DFM Results FeaturePython object.

    Returns a dict with keys: target_label, process, material, verdict,
    timestamp, shape_hash, findings, stale, run_settings.
    Returns None if the object is not valid.
    """
    if not hasattr(obj, _PROP_FINDINGS_JSON):
        return None

    findings_json = getattr(obj, _PROP_FINDINGS_JSON, "")
    run_settings_json = getattr(obj, _PROP_RUN_SETTINGS_JSON, "{}")

    try:
        run_settings = json.loads(run_settings_json) if run_settings_json else {}
    except json.JSONDecodeError:
        run_settings = {}

    return {
        "target_label": getattr(obj, _PROP_TARGET_LABEL, ""),
        "process": getattr(obj, _PROP_PROCESS, ""),
        "material": getattr(obj, _PROP_MATERIAL, ""),
        "verdict": getattr(obj, _PROP_VERDICT, ""),
        "timestamp": getattr(obj, _PROP_TIMESTAMP, ""),
        "shape_hash": getattr(obj, _PROP_SHAPE_HASH, ""),
        "findings": results_from_json(findings_json),
        "stale": getattr(obj, _PROP_STALE, False),
        "run_settings": run_settings,
    }


def check_shape_changed(obj, current_shape) -> bool:
    """Returns True if the shape has changed since the stored analysis."""
    stored_hash = getattr(obj, _PROP_SHAPE_HASH, "")
    if not stored_hash:
        return True
    current_hash = compute_shape_hash(current_shape)
    changed = current_hash != stored_hash
    return changed


def mark_stale(obj, stale: bool = True):
    """Mark the results object as stale (shape has changed)."""
    if hasattr(obj, _PROP_STALE):
        setattr(obj, _PROP_STALE, stale)


def get_run_settings(obj) -> dict:
    """Extract the stored run settings from a DFM Results object."""
    raw = getattr(obj, _PROP_RUN_SETTINGS_JSON, "{}")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
