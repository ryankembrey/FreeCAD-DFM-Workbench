# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Saved Zebra Stripes: a document object holding the panel's settings.

Like a saved contour, it shows nothing by itself in the 3D view; double-click
it to open the panel again with its settings, or add it to a PDF report. The
stripes are worked out again from the part each time, so a saved one never
goes out of date.
"""

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ...app.contour import zebra as Z

ICON = ":/icons/dfm_zebra.svg"
TOOL = "zebra"


class ZebraStripesFeature:
    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyString", "Tool", "DFM", "Always zebra")
        obj.addProperty("App::PropertyLinkGlobal", "Source", "DFM", "The model striped")
        obj.addProperty("App::PropertyIntegerList", "Faces", "DFM", "Faces striped (1-based); empty for all")
        obj.addProperty("App::PropertyEnumeration", "Mode", "DFM", "Follow the view, or fixed to the part")
        obj.addProperty("App::PropertyBool", "Horizontal", "DFM", "Horizontal stripes (Follow the view)")
        obj.addProperty("App::PropertyVector", "Direction", "DFM", "Direction (Fixed to the part)")
        obj.addProperty("App::PropertyString", "DirectionReference", "DFM", "Where the direction came from")
        obj.addProperty("App::PropertyInteger", "Count", "DFM", "Number of stripes")
        obj.addProperty("App::PropertyInteger", "Width", "DFM", "Share of a stripe pair in the first color (%)")
        obj.addProperty("App::PropertyColor", "Color1", "DFM", "Stripe color")
        obj.addProperty("App::PropertyColor", "Color2", "DFM", "Background color")
        obj.addProperty("App::PropertyEnumeration", "Quality", "DFM", "Mesh quality")
        obj.addProperty("App::PropertyBool", "Edges", "DFM", "Show edges")
        obj.Tool = TOOL
        obj.Mode = ["Follow the view", "Fixed to the part"]
        obj.Quality = list(Z.QUALITY)
        for prop in ("Tool",):
            obj.setEditorMode(prop, 2)

    def execute(self, obj):
        pass

    def onChanged(self, obj, prop):
        pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class ZebraStripesViewProvider:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def getDisplayModes(self, vobj):
        return ["Zebra"]

    def getDefaultDisplayMode(self):
        return "Zebra"

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
        return ICON

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def is_zebra(obj) -> bool:
    return getattr(obj, "Tool", "") == TOOL and hasattr(obj, "Count")


def settings_of(obj) -> dict:
    """The panel's settings from a saved object (see ``save``)."""
    d = obj.Direction

    def rgb(color):
        return tuple(int(round(c * 255)) for c in tuple(color)[:3])

    return {
        "mode": "fixed" if obj.Mode == "Fixed to the part" else "view",
        "horizontal": bool(obj.Horizontal),
        "direction": (d.x, d.y, d.z),
        "direction_ref": obj.DirectionReference or "+Z",
        "count": int(obj.Count) or 20,
        "width": int(obj.Width) or 50,
        "colors": [rgb(obj.Color1), rgb(obj.Color2)],
        "quality": obj.Quality or Z.DEFAULT_QUALITY,
        "edges": bool(obj.Edges),
        "faces": list(obj.Faces or []),
    }


def save(obj, source, settings: dict):
    """Create (*obj* None) or update a saved Zebra Stripes object."""
    doc = App.ActiveDocument if obj is None else obj.Document
    if doc is None:
        raise RuntimeError("No active document to save into.")
    if obj is None:
        obj = doc.addObject("App::FeaturePython", "ZebraStripes")
        ZebraStripesFeature(obj)
        obj.Label = f"Zebra Stripes of {source.Label}"
        if App.GuiUp and obj.ViewObject is not None:
            ZebraStripesViewProvider(obj.ViewObject)
    obj.Source = source
    obj.Faces = list(settings.get("faces") or [])
    obj.Mode = "Fixed to the part" if settings.get("mode") == "fixed" else "Follow the view"
    obj.Horizontal = bool(settings.get("horizontal"))
    obj.Direction = App.Vector(*settings.get("direction", (0, 0, 1)))
    obj.DirectionReference = str(settings.get("direction_ref", ""))
    obj.Count = int(settings.get("count", 20))
    obj.Width = int(settings.get("width", 50))
    first, second = settings.get("colors", ((0, 0, 0), (255, 255, 255)))
    obj.Color1 = tuple(c / 255.0 for c in first)
    obj.Color2 = tuple(c / 255.0 for c in second)
    if settings.get("quality") in Z.QUALITY:
        obj.Quality = settings["quality"]
    obj.Edges = bool(settings.get("edges", True))
    obj.touch()
    doc.recompute()
    return obj


def open_panel_for(obj):
    from .zebra_panel import ZebraTaskPanel

    Gui.Control.showDialog(ZebraTaskPanel(saved=obj))
