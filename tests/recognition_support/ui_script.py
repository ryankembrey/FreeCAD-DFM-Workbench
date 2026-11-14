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
* Results header: in a narrow panel the model gets a line of its own instead
  of shrinking to "E…"; in a wide one everything stays on one line.
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
# -- Results header: the model name is never cut to "E…" ------------------------------------------
from freecad.DFM.gui.results.header import ResultsHeader  # noqa: E402

header = ResultsHeader()
combo = QtWidgets.QComboBox()
combo.addItems(["ABS", "Long-Fiber Reinforced Plastics"])
header.set_material_combo(combo)
header.set_model("Enclosure")
header.set_process("Plastic Injection Molding")
header.set_verdict("Failed (162 errors, 175 warnings)", "#e24b4a")


def settle(width):
    header.resize(width, header.heightForWidth(width) if header.hasHeightForWidth() else 120)
    header.show()
    for _ in range(5):
        app.processEvents()
    header.layout().activate()
    app.processEvents()


settle(440)  # a task panel's width
assert header._model.text() == "Enclosure", f"model name cut to {header._model.text()!r}"
assert header.model_on_own_line(), "a narrow header should give the model its own line"
assert header._process.text() == "Plastic Injection Molding", header._process.text()
assert header._model.mapTo(header, QtCore.QPoint(0, 0)).y() < header._process.mapTo(header, QtCore.QPoint(0, 0)).y()

settle(1000)
assert not header.model_on_own_line(), "a wide header should keep one line"
assert header._model.text() == "Enclosure", header._model.text()
y_model = header._model.mapTo(header, QtCore.QPoint(0, 0)).y()
y_process = header._process.mapTo(header, QtCore.QPoint(0, 0)).y()
assert abs(y_model - y_process) <= 2, (y_model, y_process)

need = header._meta_width()
for width in (need - 1, need, need + 1, need - 1):  # no flipping at the boundary
    settle(width)
    assert header.model_on_own_line() == (width < need), (width, need, header.model_on_own_line())

settle(440)  # and back: the model returns to its own line, in full
assert header.model_on_own_line() and header._model.text() == "Enclosure"
header.close()

# Short names fit on one line in a task panel, even when the material list has
# long names (the switcher only needs room for the current one).
short = ResultsHeader()
materials = QtWidgets.QComboBox()
materials.addItems(["PLA", "PETG", "Nylon (PA12) Carbon Fiber Reinforced"])
short.set_material_combo(materials)
short.set_model("Holder")
short.set_process("FDM")
header = short
settle(440)
assert not short.model_on_own_line(), (short._meta_width(), short.width())
assert short._model.text() == "Holder", short._model.text()
short.close()

print("ui: ok")
