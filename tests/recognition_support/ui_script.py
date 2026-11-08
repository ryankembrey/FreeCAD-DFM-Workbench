# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The Process Library, rule feedback editor and progress panel, offscreen
with FreeCAD stand-ins. Run by tests/test_ui.py in a fresh interpreter.

* Rule cards: a severity combo is exactly as wide as a value field.
* Library tree: processes start collapsed; selecting a material opens its
  process; what the user opened stays open when the tree is rebuilt;
  override counts are numbers (the badge), with the words in the tool tip.
* Feedback editor: the chips are the rule's own placeholders, and OK
  accepts those and rejects others.
* Progress panel: every rule line has the same height whatever it shows,
  percentages without bars, and no core counts.
"""

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import freecad_stubs  # noqa: E402,F401

import FreeCADGui as Gui  # noqa: E402
from PySide6 import QtCore, QtWidgets  # noqa: E402
from PySide6.QtUiTools import QUiLoader  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
from freecad.DFM.gui.utils import resources  # noqa: E402,F401  (":/ui/...")

main = QtWidgets.QMainWindow()
Gui.getMainWindow = lambda: main


class _Uic:
    @staticmethod
    def loadUi(path, parent=None):
        f = QtCore.QFile(path)
        f.open(QtCore.QIODevice.OpenModeFlag.ReadOnly)
        try:
            return QUiLoader().load(f, parent)
        finally:
            f.close()


Gui.PySideUic = _Uic

from freecad.DFM.core.processes.process import RuleFeedback  # noqa: E402
from freecad.DFM.core.rules import Criticality, Rulebook as R  # noqa: E402
from freecad.DFM.gui import process_library as PL  # noqa: E402
from freecad.DFM.gui import rule_cards as RC  # noqa: E402
from freecad.DFM.gui import task_progress as TP  # noqa: E402

ROLE = QtCore.Qt.ItemDataRole.UserRole

# -- Rule cards: the severity combo is a value field's width --------------------------------------
lib = PL.ProcessLibrary()
lib.resize(1200, 850)
lib.show()
app.processEvents()
combos = [c for c in lib.findChildren(RC.BinaryCard) if c.isVisible()]
fields = [f for f in lib.findChildren(RC.UnitInput) if f.isVisible()]
assert combos and fields, "cards shown"
for card in combos:
    assert card._combo.width() == RC._FIELD_WIDTH, card._combo.width()
assert all(f.width() == RC._FIELD_WIDTH for f in fields)
# Right edges line up with the At most fields' (same x in the window).
right = lambda w: w.mapTo(lib, QtCore.QPoint(w.width(), 0)).x()  # noqa: E731
limit_edges = {right(c._limit_field) for c in lib.findChildren(RC.TargetAndLimitCard) if c.isVisible()}
combo_edges = {right(c._combo) for c in combos}
assert len(limit_edges) == 1 and combo_edges == limit_edges, (limit_edges, combo_edges)

# -- Library tree --------------------------------------------------------------------------------
tree = lib.form.twProcess
nodes = {}
it = QtWidgets.QTreeWidgetItemIterator(tree)
while it.value():
    item = it.value()
    data = item.data(0, ROLE)
    if data and data.get("type") == "process":
        nodes[data["name"]] = item
    it += 1
selected = tree.currentItem().data(0, ROLE)
assert selected["type"] == "material", selected
for name, node in nodes.items():
    assert node.isExpanded() == (name == selected["process"]), (name, node.isExpanded())

lib.tree_view.select_material("Plastic Injection Molding", "PP")
assert nodes["Plastic Injection Molding"].isExpanded(), "selecting a material opens its process"
pp = tree.currentItem()
count = pp.data(0, PL._COUNT_ROLE)
assert isinstance(count, int) and count > 0, count
assert f"{count} rules differ from the Default material" in pp.toolTip(0), pp.toolTip(0)

nodes["Resin 3D Printing (SLA)"].setExpanded(True)
nodes["Plastic Injection Molding"].setExpanded(False)
lib.tree_view.populate(lib.model.get_categorized_processes())  # rebuilt, as after a save
it = QtWidgets.QTreeWidgetItemIterator(tree)
while it.value():
    data = it.value().data(0, ROLE)
    if data and data.get("type") == "process" and data["name"] in ("Resin 3D Printing (SLA)",
                                                                    "Plastic Injection Molding"):
        assert it.value().isExpanded() == (data["name"] == "Resin 3D Printing (SLA)"), data["name"]
    it += 1
lib.close()

# -- Feedback editor: the rule's own placeholders -----------------------------------------------------


def dialog(rule, warning):
    values = PL.RuleValueContext(None, None, True, "Default")
    return PL.EditFeedbackDialog("Process", rule, RuleFeedback(warning, ""), Criticality.MEDIUM, values)


dlg = dialog(R.MAX_CUP_AREA, "Cup {feature} {sealed_by}")
chips = [c.text() for c in dlg.findChildren(QtWidgets.QToolButton) if c.objectName() == "ParamChip"]
assert chips == ["{measured}", "{target}", "{limit}", "{feature}", "{size}", "{sealed_by}"], chips
assert all(c.toolTip() for c in dlg.findChildren(QtWidgets.QToolButton) if c.objectName() == "ParamChip")
dlg.accept()
assert dlg.result() == QtWidgets.QDialog.DialogCode.Accepted, "its own placeholders are accepted"

warned = []
QtWidgets.QMessageBox.warning = lambda *a, **k: warned.append(a[2])
dlg = dialog(R.MIN_WALL_THICKNESS, "Wall {feature}")
dlg.accept()
assert dlg.result() != QtWidgets.QDialog.DialogCode.Accepted and warned, "{feature} isn't filled in here"
assert "{measured}, {target}, {limit}" in warned[-1], warned[-1]

dlg = dialog(R.PIN_ROOT_FILLET, "x")
chips = [c.text() for c in dlg.findChildren(QtWidgets.QToolButton) if c.objectName() == "ParamChip"]
assert chips == ["{feature}", "{size}"], chips  # a binary feature rule has no value
dlg = dialog(R.MAX_RIB_THICKNESS_RATIO, "x")
chips = [c.text() for c in dlg.findChildren(QtWidgets.QToolButton) if c.objectName() == "ParamChip"]
assert "{nominal}" in chips, chips

# -- Progress panel -------------------------------------------------------------------------------------
real = QtCore.QTimer.singleShot
QtCore.QTimer.singleShot = lambda *a, **k: None  # don't start an analysis


class _Obj:
    Label = "Part"


class _Request:
    target_object = _Obj()
    process_name = "Plastic Injection Molding"
    material_name = "ABS"


panel = TP.TaskProgress(_Request())
QtCore.QTimer.singleShot = real
rules = [R.MIN_WALL_THICKNESS, R.MAX_WALL_THICKNESS, R.MIN_DRAFT_ANGLE, R.NO_UNDERCUTS]
panel.running = True
panel._on_event("plan", {"total_steps": 400, "rules": [
    {"rule": r, "analyzer": a, "steps": 100} for r, a in zip(rules, ("A", "B", "C", "D"))]})
now = time.monotonic()
panel._on_event("workers", {"workers": 7, "analyzers": {
    "A": {"total": 10, "done": 10, "started": now - 2, "finished": now - 1},
    "B": {"total": 10, "done": 4, "started": now - 2, "busy": 3}}})
panel._on_event("check", {"rule": R.MIN_WALL_THICKNESS, "findings": 2, "seconds": 0.1})
panel.form.resize(360, 400)
panel.form.show()
app.processEvents()
heights = {w.height() for row in panel._rows for w in (row.mark, row.name, row.detail)}
assert len(heights) == 1, heights
details = [row.detail.text() for row in panel._rows]
assert details[1] == "40%", details
assert not any("core" in text.lower() for text in details + [panel.phase_label.text()]), details
assert not panel.form.findChildren(QtWidgets.QProgressBar)[1:], "one bar: the overall one"
panel.form.close()
print("ui: ok")
