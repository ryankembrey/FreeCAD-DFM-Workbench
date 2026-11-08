# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""FreeCAD, FreeCADGui, pivy and results-scene stand-ins, so the bench's Qt
code and the history hints can be exercised without FreeCAD.

Import this before anything from the workbench (in a fresh interpreter: the
tests run their scripts in a subprocess)."""
import sys, types

class Param:
    store = {}
    def __getattr__(self, name):
        if name.startswith("Get"):
            return lambda key, default=None: Param.store.get(key, default)
        if name.startswith("Set"):
            return lambda key, value: Param.store.__setitem__(key, value)
        if name.startswith("Rem"):
            return lambda key: Param.store.pop(key, None)
        return lambda *a, **k: None
    def GetContents(self): return []

class Console:
    def __getattr__(self, name):
        return lambda *a, **k: sys.stderr.write(" ".join(map(str, a)))

app = types.ModuleType("FreeCAD")
app.Console = Console(); app.GuiUp = True; app.ParamGet = lambda p: Param()
app.ActiveDocument = None
app.getUserAppDataDir = lambda: "/tmp/"
class Vector:
    def __init__(self, x=0, y=0, z=0): self.x, self.y, self.z = x, y, z
app.Vector = Vector
sys.modules["FreeCAD"] = app

class Selection:
    observers = []
    selection = []
    @classmethod
    def addObserver(cls, o): cls.observers.append(o)
    @classmethod
    def removeObserver(cls, o): cls.observers.remove(o)
    @classmethod
    def getSelection(cls): return []
    @classmethod
    def getSelectionEx(cls): return cls.selection
    @classmethod
    def clearSelection(cls): pass
    @classmethod
    def addSelection(cls, *a): pass

class Control:
    dialog = None
    @classmethod
    def showDialog(cls, d): cls.dialog = d
    @classmethod
    def closeDialog(cls): cls.dialog = None
    @classmethod
    def activeDialog(cls): return cls.dialog is not None

gui = types.ModuleType("FreeCADGui")
gui.Selection = Selection; gui.Control = Control
gui.SendMsgToActiveView = lambda m: None
gui.addCommand = lambda name, cmd: gui.__dict__.setdefault("commands", {}).__setitem__(name, cmd)
gui.addPreferencePage = lambda *a: None
sys.modules["FreeCADGui"] = gui

part = types.ModuleType("Part"); part.Shape = object; sys.modules["Part"] = part

class FakeScene:
    calls = []
    def __init__(self, target): self.target = target; self.visible = False
    def set_highlight(self, faces, edges): FakeScene.calls.append(("highlight", len(faces), len(edges)))
    def show(self): self.visible = True; FakeScene.calls.append(("show",))
    def hide(self): self.visible = False; FakeScene.calls.append(("hide",))
    def remove(self): FakeScene.calls.append(("remove",))

hs = types.ModuleType("freecad.DFM.gui.results.highlight_scene")
hs.HighlightScene = FakeScene
hs.merge_colors = lambda pairs: dict(pairs)
hs.hex_to_rgb = lambda h: (0, 0, 0)
hs.display_deflection = lambda *a: 0.1
sys.modules["freecad.DFM.gui.results.highlight_scene"] = hs

from unittest import mock as _mock
pivy = types.ModuleType("pivy"); pivy.coin = _mock.MagicMock(); sys.modules["pivy"] = pivy; sys.modules["pivy.coin"] = pivy.coin
