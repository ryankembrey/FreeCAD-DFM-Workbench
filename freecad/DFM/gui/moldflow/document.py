# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The saved mold flow analysis: a tree item that keeps the settings and the
result, and opens the task panel again on double-click.

As with the contour tools (gui/contour/document.py), running the analysis
puts it in the tree, Save keeps it and Close drops it if it was never saved.
The result is shown while the panel is open. Its legend is the contour
tools' Legend item, as a child.
"""

from pivy import coin

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ..contour.document import ensure_legend_object, legend_child

_ICON = ":/icons/dfm_moldflow.svg"


def _result_labels():
    from ...app.moldflow.analysis import RESULTS

    return [label for label, _unit in RESULTS.values()]


def _colormap_names():
    from ...app.contour.colormap import COLORMAPS

    return list(COLORMAPS.keys())


def _resolution_names():
    from ...app.contour.meshing import RESOLUTION_DIVISORS

    return list(RESOLUTION_DIVISORS.keys()) + ["Custom"]


def _set_enum(obj, prop, value, choices):
    if value and value in choices:
        try:
            setattr(obj, prop, value)
        except Exception:
            pass


class MoldFlowAnalysisFeature:
    """Proxy holding a mold flow analysis's settings and result."""

    def __init__(self, obj):
        obj.Proxy = self
        add = obj.addProperty
        add("App::PropertyLinkGlobal", "Source", "Model", "Analyzed object")
        add("App::PropertyEnumeration", "Resolution", "Model", "Mesh resolution preset")
        add("App::PropertyFloat", "ElementSize", "Model", "Mesh element size (mm)")
        add("App::PropertyString", "Material", "Process", "Material")
        add("App::PropertyFloat", "MeltTemperature", "Process", "Melt temperature (°C)")
        add("App::PropertyFloat", "MoldTemperature", "Process", "Mold temperature (°C)")
        add("App::PropertyFloat", "FillTime", "Process", "Injection (fill) time, s; 0: automatic")
        add("App::PropertyFloat", "MaxPressure", "Process", "Machine pressure limit (MPa)")
        add("App::PropertyVectorList", "Gates", "Process", "Gate positions (mm)")
        add("App::PropertyEnumeration", "Result", "Display", "Result shown")
        add("App::PropertyEnumeration", "ColorMap", "Display", "Color map")
        add("App::PropertyBool", "Smooth", "Display", "Smooth (blended) shading")
        add("App::PropertyBool", "ShowWeldLines", "Display", "Show weld and meld lines")
        add("App::PropertyBool", "ShowAirTraps", "Display", "Show air traps")
        add("App::PropertyPythonObject", "FieldData", "Result", "Computed result")
        obj.Resolution = _resolution_names()
        obj.Result = _result_labels()
        obj.ColorMap = _colormap_names()
        obj.FieldData = None
        for prop in ("FieldData",):
            try:
                obj.setEditorMode(prop, 2)  # hidden: big and not editable
            except Exception:
                pass
        self._init_done = True

    def store(self, obj, params, field):
        self._storing = True
        try:
            obj.Source = params.get("source")
            _set_enum(obj, "Resolution", params.get("resolution", ""), _resolution_names())
            obj.ElementSize = float(params.get("element_size") or 0.0)
            obj.Material = params.get("material", "")
            obj.MeltTemperature = float(params.get("melt_temp", 0.0))
            obj.MoldTemperature = float(params.get("mold_temp", 0.0))
            obj.FillTime = float(params.get("fill_time") or 0.0)
            obj.MaxPressure = float(params.get("max_pressure", 100.0))
            obj.Gates = [App.Vector(*g) for g in params.get("gates", [])]
            _set_enum(obj, "Result", params.get("result", ""), _result_labels())
            _set_enum(obj, "ColorMap", params.get("colormap", ""), _colormap_names())
            obj.Smooth = bool(params.get("smooth", True))
            obj.ShowWeldLines = bool(params.get("show_welds", True))
            obj.ShowAirTraps = bool(params.get("show_traps", True))
            if field is not None:
                obj.FieldData = field
        finally:
            self._storing = False

    def execute(self, obj):
        pass

    def onChanged(self, obj, prop):
        if prop not in ("Result", "ColorMap", "Smooth", "ShowWeldLines", "ShowAirTraps"):
            return
        if getattr(self, "_storing", False):
            return
        panel = getattr(self, "_live_panel", None)
        if panel is not None and hasattr(panel, "apply_property_change"):
            try:
                panel.apply_property_change(prop)
            except Exception:
                pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class MoldFlowAnalysisViewProvider:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object
        try:
            vobj.addDisplayMode(coin.SoSeparator(), "Result")
        except Exception:
            pass

    def claimChildren(self):
        legend = legend_child(getattr(self, "Object", None))
        return [legend] if legend is not None else []

    def getDisplayModes(self, vobj):
        return ["Result"]

    def getDefaultDisplayMode(self):
        return "Result"

    def setDisplayMode(self, mode):
        return mode

    def doubleClicked(self, vobj):
        open_panel_for(vobj.Object)
        return True

    def setEdit(self, vobj, mode=0):
        open_panel_for(vobj.Object)
        return True

    def onDelete(self, vobj, subelements):
        legend = legend_child(vobj.Object)
        if legend is not None:
            try:
                vobj.Object.Document.removeObject(legend.Name)
            except Exception:
                pass
        return True

    def getIcon(self):
        return _ICON

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def create_or_update_analysis(obj, params, field):
    doc = App.ActiveDocument
    if doc is None:
        raise RuntimeError("No active document to save into.")
    if obj is None:
        obj = doc.addObject("App::FeaturePython", "MoldFlowAnalysis")
        MoldFlowAnalysisFeature(obj)
        obj.Label = "Mold Flow Analysis"
        obj.Proxy.store(obj, params, field)
        if App.GuiUp and obj.ViewObject is not None:
            MoldFlowAnalysisViewProvider(obj.ViewObject)
    else:
        obj.Proxy.store(obj, params, field)
    obj.touch()
    doc.recompute()
    return obj


def ensure_legend(analysis_obj, horizontal):
    return ensure_legend_object(analysis_obj, horizontal)


def delete_analysis(obj):
    """Remove an analysis and its legend (an unsaved one on Close)."""
    try:
        doc = obj.Document
    except Exception:
        return
    if doc is None:
        return
    try:
        legend = legend_child(obj)
        if legend is not None:
            doc.removeObject(legend.Name)
        doc.removeObject(obj.Name)
        doc.recompute()
    except Exception as exc:
        App.Console.PrintWarning(f"DFM mold flow: could not remove the unsaved analysis. {exc}\n")


def open_panel_for(obj):
    from .panel import MoldFlowTaskPanel

    Gui.Control.showDialog(MoldFlowTaskPanel(analysis_obj=obj))
