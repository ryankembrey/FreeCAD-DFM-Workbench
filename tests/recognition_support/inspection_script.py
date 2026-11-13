# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The Symmetry Check and Compare Bodies panels, offscreen with FreeCAD
stand-ins and real OCP parts. Run by tests/test_inspection_ui.py in a fresh
interpreter.

* Symmetry: the layout (Model, Settings, Display, buttons; the banner and
  the list only after a check); the plane previews for YZ and not for
  Auto; checking finds the pocket, the boss and the shifted hole, each
  with an info icon in the view and a row in the tree; the key in the 3D
  view steps through them and hides a kind; ignoring one greys it and
  stops it counting, and back; the midplane of two faces, picked one
  after the other; the asymmetry map; a changed setting asks to check
  again; saving and reopening restores everything and checks again.
* Rotational settings show the axis and copies, and hide the plane rows.
* Compare: two bodies fill A and B; comparing finds an added rib, a
  removed boss and a hole made larger, grouped under their headers with
  counts, and the properties; the deviation map; swap; save and reopen.
"""

import math
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import freecad_stubs  # noqa: E402,F401

import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from PySide6 import QtCore, QtWidgets  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
main = QtWidgets.QMainWindow()
Gui.getMainWindow = lambda: main
Gui.ActiveDocument = None

from freecad.DFM.core.inspection import models as M  # noqa: E402
from freecad.DFM.core.recognition import recognize  # noqa: E402
from freecad.DFM.core.recognition.testing import shapes as B  # noqa: E402
from freecad.DFM.gui.inspection import document as D  # noqa: E402
from freecad.DFM.gui.inspection import panel_base as PB  # noqa: E402
from freecad.DFM.gui.inspection import scene as SC  # noqa: E402
from freecad.DFM.gui.inspection import widgets as W  # noqa: E402

import test_inspection as T  # noqa: E402  (the test parts)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


# -- stand-ins -----------------------------------------------------------------------

class FakeShape:
    def __init__(self, occ, tag):
        self.occ, self.tag = occ, tag
        self.Solids = [1]
        self.BoundBox = types.SimpleNamespace(DiagonalLength=150.0)

    def isNull(self):
        return False

    def exportBrepToString(self):
        return self.tag


class FakeDoc:
    Name = "Doc"

    def __init__(self):
        self.Objects = []

    def addObject(self, type_id, name):
        obj = FakeFeature(name, self)
        self.Objects.append(obj)
        return obj

    def recompute(self):
        pass


class FakeFeature:
    def __init__(self, name, doc):
        self.Name, self.Label, self.Document, self.ViewObject = name, name, doc, None

    def addProperty(self, kind, name, *args):
        setattr(self, name, None)

    def setEditorMode(self, *args):
        pass


class FakeObj:
    def __init__(self, label, occ, doc):
        self.Label, self.Name, self.Document = label, label, doc
        self.Shape = FakeShape(occ, label)
        self.ViewObject = types.SimpleNamespace(Visibility=True, Deviation=0.5)


PB.ocp_shape = lambda obj: obj.Shape.occ
PB.recognition_for = lambda shape, obj: recognize(shape)


class FakeScene:
    """Records what the panel draws."""
    last = None

    def __init__(self, target):
        self.target_object = target
        self.calls = {}
        FakeScene.last = self

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls[name] = args
        return record

    def remove(self):
        self.calls["removed"] = True


PB.InspectScene = FakeScene
# A 3D view widget for the key to sit in.
viewport = QtWidgets.QWidget()
viewport.resize(900, 600)
PB.get_view_widget = lambda: viewport
doc = FakeDoc()
App.ActiveDocument = doc


def visible(panel, widget):
    return widget.isVisibleTo(panel.form)


def group_titles(panel):
    return [b.title() for b in panel.form.findChildren(QtWidgets.QGroupBox) if visible(panel, b)]


# -- the scene's own geometry helper ----------------------------------------------------

lines = SC.edge_polylines(B.box(0, 0, 0, 10, 10, 10), 0.1)
check(len(lines) == 12 and all(len(line) >= 2 for line in lines), f"box edges: {len(lines)}")

# -- Symmetry Check ------------------------------------------------------------------------

from freecad.DFM.gui.inspection import symmetry_panel as SP  # noqa: E402
from freecad.DFM.gui.inspection.symmetry_panel import SymmetryPanel  # noqa: E402

part = FakeObj("Bracket", T.asymmetric(), doc)
panel = SymmetryPanel()
check(group_titles(panel) == ["Model", "Settings", "Display"], group_titles(panel))
check(not visible(panel, panel.banner), "no banner before a check")
check(not panel.run_button.isEnabled(), "nothing to check yet")
check(panel.set_model(part), "model accepted")
check(panel.run_button.isEnabled(), "ready")
check(panel.model_row.edit.text() == "Bracket", panel.model_row.edit.text())
check([panel.plane.itemText(i) for i in range(panel.plane.count())] ==
      ["Auto (best fit)", "YZ", "XZ", "XY", "", "Midplane of two faces…", "Pick a face or plane…"],
      [panel.plane.itemText(i) for i in range(panel.plane.count())])
check(visible(panel, panel.through) and visible(panel, panel.offset) and not visible(panel, panel.copies),
      "mirror rows")

# Auto: no preview yet, and the note says when it is chosen.
check(FakeScene.last.calls.get("set_visible") == ("plane", False), f"auto previews nothing: {FakeScene.last.calls}")
check("when you check" in panel.dir_note.text(), panel.dir_note.text())
# YZ previews the plane, with no label.
panel.plane.setCurrentIndex(panel.plane.findData("YZ"))
plane_call = FakeScene.last.calls["set_plane"]
check(len(plane_call) == 3 and plane_call[1] == (1.0, 0.0, 0.0), plane_call)
check(not panel.dir_note.isVisibleTo(panel.form), "no note for a named plane")
panel.plane.setCurrentIndex(0)

panel.run()
r = panel.result
check(r is not None and not r.symmetric, "not symmetric")
check(group_titles(panel) == ["Model", "Settings", "Differences", "Display"], group_titles(panel))
check(visible(panel, panel.banner) and panel.banner.word == "Not symmetric", panel.banner.word)
check(panel.banner.detail.startswith("3 differences · 1.8 % of the part · furthest 10.0 mm"), panel.banner.detail)
check(panel.banner.kind == "warn", panel.banner.kind)
diffs = panel.tree.differences()
check([d.name for d in diffs] == ["Pocket 16 × 10", "Boss Ø10", "Hole Ø8"], [d.name for d in diffs])
rows = panel.tree.row_texts()
check(rows[1] == ("Boss Ø10", "only on the +X side · 0.79 cm³"), rows)
check(not panel.tree.rootIsDecorated(), "flat list")
check(panel.dir_note.text().startswith("Auto chose YZ"), panel.dir_note.text())
drawn = FakeScene.last.calls
check(len(drawn["set_solids"][0]) == 1 and len(drawn["set_ghosts"][0]) == 4, drawn.keys())
check(len(drawn["set_markers"][0]) == 3 and not any(s for _p, s in drawn["set_markers"][0]), drawn["set_markers"])
check(len(drawn["set_plane"]) == 3 and drawn["set_visible"] == ("plane", True), drawn["set_plane"])

# The key in the 3D view.
legend = panel._legend
check(legend is not None and legend.parent() is viewport and legend._title == "Symmetry", "key shown")
check([c.label for c in legend._categories] == ["Only on one side", "Doesn’t line up", "Symmetric"],
      [c.label for c in legend._categories])
shares = panel.shares()
check(abs(sum(shares.values()) - 1.0) < 1e-9 and shares[SP.ONE_SIDE] > shares[SP.MISALIGNED] > 0, shares)

# Select one: card, its marker ringed, and the key's position.
boss = diffs[1]
panel.select(boss)
check(panel.tree.current() is boss and panel.tree.currentIndex().data(W.PRIMARY) == "Boss Ø10", "selected")
markers = FakeScene.last.calls["set_markers"][0]
check([s for _p, s in markers] == [False, True, False], markers)
check(legend._region_pos.get(SP.ONE_SIDE) == (1, 2), legend._region_pos)
# The key's arrows step through the differences of that kind.
legend.regionRequested.emit(SP.ONE_SIDE, 1)
check(panel._selected is diffs[0] and legend._region_pos[SP.ONE_SIDE] == (0, 2), panel._selected)
legend.regionRequested.emit(SP.MISALIGNED, 1)
check(panel._selected is diffs[2], "the shifted hole")
# Hiding a kind in the key hides those differences in the view.
legend.mutedChanged.emit(frozenset({SP.MISALIGNED}))
check(panel._selected is None, "the hidden one is no longer selected")
check(len(FakeScene.last.calls["set_markers"][0]) == 2, FakeScene.last.calls["set_markers"])
legend.mutedChanged.emit(frozenset())

# Ignore one: greyed, no longer counts, no marker; and back.
panel.select(boss)
panel.set_ignored([boss], True)
check(panel.banner.detail.startswith("2 differences") and panel.banner.detail.endswith("· 1 ignored"),
      panel.banner.detail)
check(boss.key in panel._ignored, "remembered")
check(panel.tree.row_texts()[1] == ("Boss Ø10", "ignored · 0.79 cm³"), panel.tree.row_texts())
check(panel.tree.current() is boss, "still selected after the tree is rebuilt")
check(len(FakeScene.last.calls["set_markers"][0]) == 2, "no marker for an ignored difference")
panel.set_ignored([boss], False)
check(panel.banner.detail.startswith("3 differences"), panel.banner.detail)

# The card: the results panel's, with the info icon and the explanation open.
from freecad.DFM.gui.inspection.card import DifferenceCard, explanation, subtitle  # noqa: E402

text = explanation(boss)
check(text.startswith("This boss Ø10 is only on the +X side (0.79 cm³). Its mirror image would be at X "),
      text)
check(text.endswith("or ignore it if the part is meant to differ."), text)
pocket = diffs[0]
check("the part is solid" in explanation(pocket), explanation(pocket))
card = DifferenceCard()
card.set_difference(boss)
check(card._value_label.text() == "Boss Ø10" and card._rule_label.text() == "only on the +X side",
      card._rule_label.text())
check(card._feedback_visible and card._feedback_label.text() == text and card._diagram_btn.isHidden(), "card")
check(card._accent_hex() == W.INFO_HEX, card._accent_hex())
boss.ignored = True
check(subtitle(boss) == "ignored", subtitle(boss))
boss.ignored = False

# The asymmetry map.
panel.mode.setChecked(True)
check(panel._map_cache is not None and panel._legend is None, "map measured; the key gives way to its legend")
values = panel._map_cache[2]
finite = [v for v in values if math.isfinite(v)]
check(finite and abs(max(finite) - 10.0) < 0.05, f"map max {max(finite) if finite else None}")
check(abs(panel.sb_lo.value()) < 1e-9 and abs(panel.sb_hi.value() - max(finite)) < 0.01,
      (panel.sb_lo.value(), panel.sb_hi.value()))
check(visible(panel, panel.cb_cmap) and not visible(panel, panel.show_ghosts), "map widgets")
check(visible(panel, panel.show_plane), "the plane check stays in the map")
panel.mode.setChecked(False)
check(panel._legend is not None, "key back")

# A setting changed: check again.
panel.tolerance.setValue(0.02)
check(panel.banner.word == "Settings changed" and panel.banner.kind == "info", panel.banner.word)
check(panel.run_button.text() == "Check symmetry again", panel.run_button.text())
check("when you check" in panel.dir_note.text(), panel.dir_note.text())
panel.tolerance.setValue(0.01)
panel.run()
check(panel.run_button.text() == "Check symmetry", panel.run_button.text())

# The midplane of two faces, clicked one after the other.
class Face:
    def __init__(self, point, normal):
        self.pn = (point, normal)


SP.point_and_normal = lambda sub: getattr(sub, "pn", None)


def select_faces(*faces):
    Gui.Selection.selection = [types.SimpleNamespace(SubObjects=[f for f, _n in faces],
                                                     SubElementNames=[n for _f, n in faces])]


select_faces()
panel.plane.setCurrentIndex(panel.plane.findData(SP.MIDPLANE))
check(panel.picking == SP.MIDPLANE and panel.pick_plane.isChecked(), "waiting for two faces")
check(panel.dir_note.text() == "Click two parallel flat faces in the 3D view.", panel.dir_note.text())
check(not panel.run_button.isEnabled(), "no checking while picking")
select_faces((Face((-60, 0, 5), (-1, 0, 0)), "Face4"))
panel._process_pick()
check(panel.picking == SP.MIDPLANE and panel.dir_note.text() == "Now click the face opposite Face4.",
      panel.dir_note.text())
select_faces((Face((0, 40, 5), (0, 1, 0)), "Face2"))
panel._process_pick()
check(panel.picking == SP.MIDPLANE and "aren’t parallel" in panel.dir_note.text(), panel.dir_note.text())
select_faces((Face((60, 0, 5), (1, 0, 0)), "Face9"))
panel._process_pick()
check(panel.picking is None and panel._plane_key == SP.MIDPLANE, panel.picking)
check(panel.plane.currentText() == "Midplane" and panel.dir_note.text() == "Between Face4 and Face9",
      (panel.plane.currentText(), panel.dir_note.text()))
check(not visible(panel, panel.through) and visible(panel, panel.offset), "no Through for a midplane")
spec = panel.spec()
check(spec.through == "point" and abs(spec.point[0]) < 1e-9 and spec.direction == (-1.0, 0.0, 0.0), spec)
check(panel.banner.word == "Settings changed", panel.banner.word)
panel.run()
check(len(panel.result.counted) == 3, len(panel.result.counted))
# Cancelling a pick goes back to the midplane; choosing YZ leaves it.
select_faces()
panel.plane.setCurrentIndex(panel.plane.findData(SP.PICKED))
check(panel.picking == SP.PICKED, panel.picking)
panel.end_pick()
check(panel.plane.currentData() == SP.MIDPLANE and panel.plane.currentText() == "Midplane", panel.plane.currentText())
panel.plane.setCurrentIndex(panel.plane.findData("YZ"))
check(panel._plane_key == "YZ" and panel.plane.itemText(panel.plane.findData(SP.MIDPLANE)) == "Midplane",
      "the midplane stays one choice away")
panel.plane.setCurrentIndex(0)
panel.run()

# Save, then open it again.
panel.set_ignored([panel.tree.differences()[1]], True)
saved = panel.save()
check(saved is not None and saved.Label == "Symmetry Check (Bracket)", getattr(saved, "Label", None))
check(saved.Settings["kind"] == "mirror" and saved.Settings["plane"] == "auto", saved.Settings)
check(saved.Settings["midplane"][2] == "Face4 and Face9", saved.Settings["midplane"])
check(len(saved.Ignored) == 1 and saved.Summary.startswith("Not symmetric: 2 differences"), saved.Summary)
panel.accept()
check(Gui.Control.dialog is None, "closed")
check(panel._legend is None, "the key goes with the panel")

again = SymmetryPanel(saved=saved)
check(again.target is part, "model restored")
QtWidgets.QApplication.processEvents()
check(again.result is not None and len(again.result.counted) == 2, "checked again on opening")
check(again.result.differences[1].ignored, "ignored remembered")
again.reject()

saved.Settings = dict(saved.Settings, plane="midplane")
mid = SymmetryPanel(saved=saved)
check(mid.plane.currentText() == "Midplane" and mid.dir_note.text() == "Between Face4 and Face9",
      (mid.plane.currentText(), mid.dir_note.text()))
mid.reject()

# Out of date: the model changed after saving.
part.Shape.tag = "changed"
stale = SymmetryPanel(saved=saved)
QtWidgets.QApplication.processEvents()
check(stale.result is None and stale.banner.word == "Out of date", stale.banner.word)
stale.reject()

# Rotational settings.
rot = SymmetryPanel()
rot.set_model(FakeObj("Flange", T.flange(skip=2), doc))
rot.kind.setCurrentIndex(1)
check(visible(rot, rot.copies) and not visible(rot, rot.offset) and not visible(rot, rot.through),
      "rotational rows")
check(visible(rot, rot.axis) and not visible(rot, rot.plane), "axis combo")
check(rot.lbl_dir.text() == "Axis" and rot.copies.suffix() == "  (every 60°)", rot.copies.suffix())
check(rot.show_plane.text() == "Show axis", rot.show_plane.text())
rot.run()
check(rot.banner.word == "Not symmetric" and rot.tree.differences()[0].detail == "no matching copy 60° round",
      rot.banner.detail)
check(rot._legend._categories[0].label == "No matching copy", rot._legend._categories[0].label)
check("has no matching copy" in explanation(rot.tree.differences()[0], True), "rotational card")
rot.reject()

# A symmetric part.
ok = SymmetryPanel()
ok.set_model(FakeObj("Plate", T.plate(), doc))
ok.run()
check(ok.banner.word == "Symmetric" and ok.banner.detail == "about YZ, within ±0.01 mm", ok.banner.detail)
check(ok.banner.kind == "pass" and not visible(ok, ok.list_box), "no list")
ok.clear()
check(not visible(ok, ok.banner) and ok.result is None and not ok.clear_button.isEnabled(), "cleared")
ok.reject()

# -- Compare Bodies ---------------------------------------------------------------------------

from freecad.DFM.gui.inspection.compare_panel import ComparePanel  # noqa: E402

rev_a = FakeObj("Bracket_RevA", T.plate(), doc)
t = T.TestCompare("test_identical")
t.setUp()
rev_b = FakeObj("Bracket_RevB", t.b, doc)
Gui.Selection.getSelection = classmethod(lambda cls: [rev_a, rev_b]).__get__(Gui.Selection)
cmp = ComparePanel()
check(group_titles(cmp) == ["Bodies", "Settings", "Display"], group_titles(cmp))
check(cmp.a is rev_a and cmp.b is rev_b, "two selected bodies fill A and B")
check(cmp.row_a.edit.text() == "Bracket_RevA" and cmp.row_b.edit.text() == "Bracket_RevB", "names")
check([cmp.alignment.itemText(i) for i in range(3)] == ["As placed", "Centres of mass", "Best fit (slower)"],
      "alignment")
check(cmp.run_button.isEnabled(), "ready")
check(not cmp.set_body("a", rev_b), "A and B must differ")
cmp.run()
r = cmp.result
check(r is not None and not r.identical, "different")
check(cmp.banner.word == "Different" and cmp.banner.detail == "3 changes · B is 1.33 cm³ (1.3 %) lighter",
      cmp.banner.detail)
check(cmp.tree.group_titles() == ["Added in B", "Removed from A", "Changed", "Properties"],
      cmp.tree.group_titles())
model = cmp.tree.model()
counts = [model.item(i).data(W.COUNT) for i in range(4)]
check(counts == [1, 1, 1, None], counts)
texts = dict(cmp.tree.row_texts())
check(texts["Rib"] == "30 × 3 × 12 mm · +1.08 cm³", texts)
check(texts["Volume"] == "99.24 → 97.92 cm³ (−1.33)" and texts["Faces"] == "16 → 17 (+1)" and
      texts["Bounding box"] == "120 × 80 × 26 mm, same", texts)
check(cmp.tree.rootIsDecorated(), "grouped")
check(rev_a.ViewObject.Visibility is False, "A hidden while its ghost shows")
drawn = FakeScene.last.calls
check(len(drawn["set_solids"][0]) == 1 and len(drawn["set_ghosts"][0]) == 3, drawn["set_ghosts"])
check(drawn["show_model"] == ({}, None) and len(drawn["set_markers"][0]) == 3, drawn["show_model"])
check(cmp._legend._title == "Compared with A" and [c.label for c in cmp._legend._categories] ==
      ["Added in B", "Removed from A", "Changed", "Unchanged"], "compare key")
cmp.show_rest.setChecked(False)
check(FakeScene.last.calls["show_model"] == ({}, 0.88), FakeScene.last.calls["show_model"])
added = cmp.tree.differences()[0]
check(explanation(added) == "30 × 3 × 12 mm, +1.08 cm³. A has no material here.", explanation(added))

# Ignore All under a group header.
cmp.set_ignored([added], True)
check(model.item(0).data(W.COUNT) == 0 and model.item(0).data(W.IGNORED_COUNT) == 1, "group counts")
check(cmp.banner.detail.startswith("2 changes") and cmp.banner.detail.endswith("· 1 ignored"), cmp.banner.detail)
cmp.set_ignored([added], False)

cmp.mode.setChecked(True)
check(rev_a.ViewObject.Visibility is False, "A stays hidden: it would cover B's map")
vals = [v for v in cmp._map_cache[2] if math.isfinite(v)]
check(max(vals) > 11.5 and min(vals) < -0.9, (min(vals), max(vals)))
check(abs(cmp.sb_lo.value() + cmp.sb_hi.value()) < 1e-9, "symmetric range")
cmp.mode.setChecked(False)

saved_cmp = cmp.save()
check(saved_cmp.Label == "Comparison (Bracket_RevA → Bracket_RevB)", saved_cmp.Label)
check(saved_cmp.Reference is rev_a and saved_cmp.Source is rev_b, "linked")
cmp.reject()
check(rev_a.ViewObject.Visibility is True, "A restored on close")

reopened = ComparePanel(saved=saved_cmp)
QtWidgets.QApplication.processEvents()
check(reopened.result is not None and len(reopened.result.counted) == 3, "compared again on opening")
reopened.swap.click()
check(reopened.a is rev_b and reopened.b is rev_a and reopened.result is None, "swapped and cleared")
reopened.reject()

same = ComparePanel()
same.set_body("a", FakeObj("One", T.plate(), doc))
same.set_body("b", FakeObj("Two", T.plate(), doc))
same.run()
check(same.banner.word == "Identical" and same.banner.detail == "within ±0.01 mm · same volume and faces",
      same.banner.detail)
check(visible(same, same.list_box) and not visible(same, same.hint), "properties still listed")
same.reject()

print("inspection ui: ok")
