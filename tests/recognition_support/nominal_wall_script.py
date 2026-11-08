# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The setup panel's Nominal wall row, driven headless (offscreen Qt,
FreeCAD stand-ins): Auto / Manual, what is satisfied, measuring now,
keeping a typed value across a new model, and a snapshot and restore.
Run by tests/test_nominal_wall.py in a fresh interpreter.

Set DFM_SCREENSHOT_DIR to also save pictures of the row in each state."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import freecad_stubs  # noqa: E402,F401
from PySide6 import QtWidgets  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from freecad.DFM.app import recognition_service  # noqa: E402
from freecad.DFM.core.models import ProcessRequirement  # noqa: E402
from freecad.DFM.core.recognition import Feature, FeatureKind, RecognitionResult  # noqa: E402
from freecad.DFM.core.recognition.testing import shapes as B  # noqa: E402
from freecad.DFM.core.utils.part_features import FEATURE_CACHE, recognize_cached  # noqa: E402
from freecad.DFM.gui.task_setup import (  # noqa: E402
    REQUIREMENT_HANDLER_TYPES,
    NominalWallRequirement,
    _REQUIREMENT_LABELS,
)

REQ = ProcessRequirement.NOMINAL_WALL_THICKNESS
SHOTS = os.environ.get("DFM_SCREENSHOT_DIR")

assert REQUIREMENT_HANDLER_TYPES[REQ] is NominalWallRequirement
order = list(REQUIREMENT_HANDLER_TYPES)
assert order.index(REQ) > order.index(ProcessRequirement.SIDE_ACTIONS), order
assert _REQUIREMENT_LABELS[REQ] == "Nominal wall"


class Doc:
    Name = "Doc"


class Part:
    def __init__(self, name, shape):
        self.Name = self.Label = name
        self.Document = Doc()
        self.ocp = shape


calls = []


def fake_features_for_object(obj, options=None, **kwargs):
    calls.append(obj.Name)
    result = recognize_cached(obj.ocp, progress=kwargs.get("progress"))
    if obj.Name == "Ball":  # every face a fillet: no plain wall to measure
        result = RecognitionResult(
            features=(Feature(1, FeatureKind.FILLET, "convex", tuple(range(1, result.face_count + 1))),),
            face_count=result.face_count, edge_count=result.edge_count,
            fingerprint=result.fingerprint, engine_version=result.engine_version, options={})
    return obj.ocp, result


recognition_service.features_for_object = fake_features_for_object

box = Part("Box", B.shell(B.box(0, 0, 0, 80, 50, 30), 2.5, lambda c: abs(c[2] - 30) < 1e-6))
ball = Part("Ball", B.box(0, 0, 0, 20, 20, 20))

panel = QtWidgets.QWidget()
panel.resize(360, 40)
layout = QtWidgets.QVBoxLayout(panel)
h = NominalWallRequirement(REQ)
h.target_changed(box)
changes = []
h.on_changed = lambda: changes.append(dict(h.value))
h.build_row(layout, on_pick=None)
panel.show()
app.processEvents()


def shot(name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        panel.grab().save(os.path.join(SHOTS, f"nominal_row_{name}.png"))


# Auto: always satisfied, read-only, measured when the analysis runs.
assert h.label == "Nominal wall"
assert h.value == {"mode": "auto"} and h.is_satisfied()
assert h.spin.isReadOnly() and h.spin.text() == "measured at run", h.spin.text()
assert h.measure_btn.isVisible() and h.measure_btn.isEnabled()
assert not h.switch.isChecked()
shot("auto")

# Measure now: recognised through the shared cache, shown read-only.
FEATURE_CACHE.clear()
assert abs(h.measure_now() - 2.5) < 0.05
assert calls == ["Box"] and len(FEATURE_CACHE) == 1
assert h.spin.isReadOnly() and abs(h.spin.value() - 2.5) < 0.05, h.spin.text()
shot("measured")

# Manual starts from what was measured, and is editable.
h.switch.setChecked(True)
assert h.value == {"mode": "manual", "value": 2.5}, h.value
assert not h.spin.isReadOnly() and not h.measure_btn.isVisible()
assert h.is_satisfied() and changes[-1] == h.value
h.spin.setValue(3.1)
assert h.value == {"mode": "manual", "value": 3.1}, h.value
shot("manual")

# A new model keeps a typed value (typed on purpose) and forgets the measurement.
h.target_changed(ball)
assert h.value == {"mode": "manual", "value": 3.1} and h.measured is None

# Back in Auto, a part with no clear wall says so.
h.switch.setChecked(False)
assert h.value == {"mode": "auto"}
assert h.measure_now() is None and h.measure_failed
assert h.spin.text() == "not found" and "Manual" in h.spin.toolTip()
shot("not_found")

# The first model's mode and typed value come back with it (this session).
h.target_changed(box)
assert h.value == {"mode": "manual", "value": 3.1}, h.value

# Snapshot and restore (as after an Abort): the panel copies ``value``.
state = dict(h.value)
h.clear()
assert h.value == {"mode": "auto"}
h.value = state
h.detach()
for i in reversed(range(layout.count())):
    w = layout.itemAt(i).widget()
    if w is not None:
        w.setParent(None)
h.build_row(layout, on_pick=None)
app.processEvents()
assert h.switch.isChecked() and abs(h.spin.value() - 3.1) < 1e-9 and not h.spin.isReadOnly()

# Manual needs a positive value; the spin box can't go below 0.1.
h.value = {"mode": "manual", "value": 0.0}
assert not h.is_satisfied()
assert h.spin.minimum() >= 0.1 or not h.manual

print("nominal wall row: ok")
