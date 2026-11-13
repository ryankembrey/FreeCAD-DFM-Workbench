# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Saved symmetry checks and comparisons, in the document tree.

A saved check keeps its settings, the ignored differences, a
one-line summary and a hash of each model. Double-clicking it opens the
panel again: it runs the check again if the model is unchanged, and says
it is out of date if the model changed.
"""

from __future__ import annotations

import FreeCAD as App  # type: ignore

SYMMETRY, COMPARE = "symmetry", "compare"
ICONS = {SYMMETRY: ":/icons/dfm_symmetry.svg", COMPARE: ":/icons/dfm_compare.svg"}
NAMES = {SYMMETRY: ("SymmetryCheck", "Symmetry Check"), COMPARE: ("Comparison", "Comparison")}


def shape_hash(obj) -> str:
    try:
        from ...app.document_object import compute_shape_hash

        return compute_shape_hash(obj.Shape)
    except Exception:
        return ""


class InspectionFeature:
    def __init__(self, obj, tool: str):
        obj.Proxy = self
        obj.addProperty("App::PropertyString", "Tool", "DFM", "symmetry or compare")
        obj.addProperty("App::PropertyLinkGlobal", "Source", "DFM", "The model checked (B, for a comparison)")
        obj.addProperty("App::PropertyLinkGlobal", "Reference", "DFM", "A, the reference, for a comparison")
        obj.addProperty("App::PropertyPythonObject", "Settings", "DFM", "The panel's settings")
        obj.addProperty("App::PropertyPythonObject", "Ignored", "DFM", "Differences ignored")
        obj.addProperty("App::PropertyString", "Summary", "DFM", "The verdict when saved")
        obj.addProperty("App::PropertyString", "SourceHash", "DFM", "Shape hash of the model when saved")
        obj.addProperty("App::PropertyString", "ReferenceHash", "DFM", "Shape hash of A when saved")
        for prop in ("Tool", "SourceHash", "ReferenceHash", "Summary"):
            try:
                obj.setEditorMode(prop, 1)  # read-only
            except Exception:
                pass
        obj.Tool = tool
        obj.Settings = {}
        obj.Ignored = []
        self._live_panel = None

    def execute(self, obj):
        pass

    def onChanged(self, obj, prop):
        pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        self._live_panel = None
        return None


class InspectionViewProvider:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object
        try:
            from pivy import coin

            vobj.addDisplayMode(coin.SoSeparator(), "Default")
        except Exception:
            pass

    def getDisplayModes(self, vobj):
        return ["Default"]

    def getDefaultDisplayMode(self):
        return "Default"

    def setDisplayMode(self, mode):
        return mode

    def doubleClicked(self, vobj):
        open_panel_for(vobj.Object)
        return True

    def setEdit(self, vobj, mode=0):
        open_panel_for(vobj.Object)
        return True

    def unsetEdit(self, vobj, mode=0):
        return False

    def getIcon(self):
        return ICONS.get(getattr(getattr(self, "Object", None), "Tool", ""), ":/icons/dfm_analysis.svg")

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def label_for(tool: str, source, reference=None) -> str:
    if tool == COMPARE:
        a = getattr(reference, "Label", "A")
        b = getattr(source, "Label", "B")
        return f"Comparison ({a} → {b})"
    return f"Symmetry Check ({getattr(source, 'Label', 'model')})"


def save(obj, tool: str, source, settings: dict, ignored, summary: str, reference=None):
    """Create (obj None) or update the saved check; returns it."""
    doc = getattr(source, "Document", None) or App.ActiveDocument
    if doc is None:
        raise RuntimeError("No document to save into.")
    if obj is None:
        name, _title = NAMES[tool]
        obj = doc.addObject("App::FeaturePython", name)
        InspectionFeature(obj, tool)
        if App.GuiUp and getattr(obj, "ViewObject", None) is not None:
            InspectionViewProvider(obj.ViewObject)
    obj.Source = source
    if tool == COMPARE:
        obj.Reference = reference
        obj.ReferenceHash = shape_hash(reference) if reference is not None else ""
    obj.Settings = dict(settings)
    obj.Ignored = list(ignored)
    obj.Summary = summary
    obj.SourceHash = shape_hash(source)
    obj.Label = label_for(tool, source, reference)
    doc.recompute()
    return obj


def is_out_of_date(obj) -> bool:
    """True when a model changed after *obj* was saved."""
    try:
        if obj.Source is not None and obj.SourceHash and shape_hash(obj.Source) != obj.SourceHash:
            return True
        ref = getattr(obj, "Reference", None)
        if ref is not None and obj.ReferenceHash and shape_hash(ref) != obj.ReferenceHash:
            return True
    except Exception:
        return False
    return False


def open_panel_for(obj):
    import FreeCADGui as Gui  # type: ignore

    tool = getattr(obj, "Tool", SYMMETRY)
    if tool == COMPARE:
        from .compare_panel import ComparePanel

        panel = ComparePanel(saved=obj)
    else:
        from .symmetry_panel import SymmetryPanel

        panel = SymmetryPanel(saved=obj)
    Gui.Control.showDialog(panel)
