# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore


_BAND_STEPS = {
    "Smooth": 0.0,
    "1 unit bands": 1.0,
    "2 unit bands": 2.0,
    "5 unit bands": 5.0,
    "10 unit bands": 10.0,
}

_MEASURE_TITLES = {
    "draft": ("Draft Analysis", ":/icons/dfm_draft_contour.svg"),
    "thickness": ("Thickness Analysis", ":/icons/dfm_draft_contour.svg"),
}


def _band_step(name):
    return _BAND_STEPS.get(name, 0.0)


class ContourAnalysisFeature:
    """Proxy holding the parameters and computed field of a saved analysis."""

    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyLinkGlobal", "Source", "DFM", "Analyzed object")
        obj.addProperty("App::PropertyString", "Measure", "DFM", "Measure id")
        obj.addProperty("App::PropertyVector", "PullDirection", "DFM", "Pull direction")
        obj.addProperty("App::PropertyString", "PullReference", "DFM", "Pull reference name")
        obj.addProperty("App::PropertyString", "Resolution", "DFM", "Resolution preset")
        obj.addProperty("App::PropertyFloat", "ElementSize", "DFM", "Mesh element size (mm)")
        obj.addProperty("App::PropertyString", "ColorMap", "DFM", "Color map name")
        obj.addProperty("App::PropertyFloat", "RangeLow", "DFM", "Color range low")
        obj.addProperty("App::PropertyFloat", "RangeHigh", "DFM", "Color range high")
        obj.addProperty("App::PropertyString", "Bands", "DFM", "Banding mode")
        obj.addProperty("App::PropertyBool", "Smooth", "DFM", "Smooth (blended) shading")
        obj.addProperty("App::PropertyPythonObject", "Options", "DFM", "Measure options")
        obj.addProperty("App::PropertyPythonObject", "FieldData", "DFM", "Computed field")
        obj.Options = {}
        obj.FieldData = None

    def store(self, obj, params, field):
        obj.Source = params.get("source")
        obj.Measure = params.get("measure", "")
        pull = params.get("pull_direction")
        if pull is not None:
            obj.PullDirection = App.Vector(*pull)
        obj.PullReference = params.get("pull_reference", "")
        obj.Resolution = params.get("resolution", "")
        obj.ElementSize = float(params.get("element_size") or 0.0)
        obj.ColorMap = params.get("colormap", "")
        obj.RangeLow = float(params.get("range_low", 0.0))
        obj.RangeHigh = float(params.get("range_high", 0.0))
        obj.Bands = params.get("bands", "Smooth")
        obj.Smooth = bool(params.get("smooth", False))
        obj.Options = dict(params.get("options", {}))
        obj.FieldData = field

    def execute(self, obj):
        # Nothing to draw persistently; the contour is shown by the task panel
        # (preview) when the object is double-clicked.
        pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class ContourAnalysisViewProvider:
    """View provider for a saved analysis. It draws nothing itself; double-click
    opens the task panel, which shows the contour as a preview."""

    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        pass

    def getDisplayModes(self, vobj):
        return ["Contour"]

    def getDefaultDisplayMode(self):
        return "Contour"

    def setDisplayMode(self, mode):
        return mode

    def doubleClicked(self, vobj):
        open_panel_for(vobj.Object)
        return True

    def setEdit(self, vobj, mode=0):
        open_panel_for(vobj.Object)
        return True

    def onDelete(self, vobj, subelements):
        return True

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def create_or_update_analysis(obj, params, field):
    """Create a new analysis object, or update an existing one. It stores the
    analysis; the contour itself is shown by the task panel on double-click."""
    doc = App.ActiveDocument
    if doc is None:
        raise RuntimeError("No active document to save into.")
    if obj is None:
        title = _MEASURE_TITLES.get(params.get("measure", ""), ("Analysis", ""))[0]
        name = title.replace(" ", "")
        obj = doc.addObject("App::FeaturePython", name)
        ContourAnalysisFeature(obj)
        obj.Label = title
        obj.Proxy.store(obj, params, field)
        if App.GuiUp and obj.ViewObject is not None:
            ContourAnalysisViewProvider(obj.ViewObject)
    else:
        obj.Proxy.store(obj, params, field)
    obj.touch()
    doc.recompute()
    return obj


def open_panel_for(obj):
    """Reopen the task panel bound to a saved analysis object."""
    from .panel import ContourTaskPanel
    from ...app.contour.measures import DraftMeasure, ThicknessMeasure

    measure_id = getattr(obj, "Measure", "draft")
    measure = ThicknessMeasure() if measure_id == "thickness" else DraftMeasure()
    title, icon = _MEASURE_TITLES.get(measure_id, ("Analysis", ":/icons/dfm_draft_contour.svg"))
    Gui.Control.showDialog(ContourTaskPanel(measure, title, icon, analysis_obj=obj))
