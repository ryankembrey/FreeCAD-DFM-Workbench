# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""History hints end to end: a PartDesign-like history (Pad, Pocket,
threaded Hole, Fillet) built with OCP, turned into hints by
app/recognition_hints.py, then used by recognition. Run by
tests/test_recognition_workbench.py in a fresh interpreter."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import freecad_stubs  # noqa: E402,F401
import fake_part  # noqa: E402
from fake_part import Shape  # noqa: E402

from freecad.DFM.app.recognition_hints import hints_for  # noqa: E402
from freecad.DFM.core.recognition import FeatureKind, recognize  # noqa: E402
from freecad.DFM.core.recognition.testing import shapes as B  # noqa: E402


class Q:
    def __init__(self, v):
        self.Value = v


class Feat:
    def __init__(self, typ, label, shape, base, **props):
        self.TypeId, self.Label, self.Shape, self.BaseFeature = typ, label, Shape(shape), base
        for k, v in props.items():
            setattr(self, k, v)


pad_s = B.box(0, 0, 0, 100, 60, 10)
pocket_s = B.cut(pad_s, B.box(20, 15, 4, 50, 45, 11))
hole_s = B.cut(pocket_s, B.drill((75, 30, 10), (0, 0, -1), 6, 10))
fil_s = B.fillet(hole_s, 1.0, B.edges_where(hole_s, lambda m, e: abs(m[2] - 10) < 1e-6 and abs(m[1] - 60) < 1e-6))
pad = Feat("PartDesign::Pad", "Pad", pad_s, None, Length=Q(10))
pocket = Feat("PartDesign::Pocket", "Pocket", pocket_s, pad, Length=Q(6))
hole = Feat("PartDesign::Hole", "Hole", hole_s, pocket, Diameter=Q(6), DepthType="ThroughAll", Threaded=True,
            ThreadSize="M6", ThreadType="ISOMetricProfile", ModelThread=False, HoleCutType="None")
fil = Feat("PartDesign::Fillet", "Fillet", fil_s, hole, Radius=Q(1.0))


class Body:
    TypeId = "PartDesign::Body"
    Label = "Body"
    Group = [pad, pocket, hole, fil]
    Shape = Shape(fil_s)

    def getGlobalPlacement(self):
        return None


hints = hints_for(Body())
sources = sorted(h.source for h in hints)
assert sources == ["PartDesign::Fillet 'Fillet'", "PartDesign::Hole 'Hole'", "PartDesign::Pocket 'Pocket'"], sources
r = recognize(fil_s, hints=hints, strict=True)
kinds = sorted(f.kind.value for f in r.features)
assert kinds == ["fillet", "hole", "pocket"], kinds
h = r.of_kind(FeatureKind.HOLE)[0]
assert h.params["threaded"] is True and h.params["thread_size"] == "M6" and h.subtype == "threaded", h.params
for f in r.features:
    assert any("confirmed by PartDesign" in e for e in f.evidence), f.evidence
print("ok")
