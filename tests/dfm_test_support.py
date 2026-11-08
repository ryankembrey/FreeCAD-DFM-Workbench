# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Shared by the feature-rule tests: FreeCAD stand-ins and test parts.

The checks import ``core.processes.process``, which imports FreeCAD. Where
FreeCAD isn't installed, ``ensure_freecad()`` puts the same minimal
stand-ins in place that tests/test_core_imports.py uses; with FreeCAD
installed it does nothing.

The test parts are 2.5 mm shelled boxes (open at the top, inner floor at
z = 2.5) with bosses, ribs and holes added, built with OCP.
"""

from __future__ import annotations

import math
import os
import sys
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def ensure_freecad() -> None:
    try:
        import FreeCAD  # noqa: F401
        return
    except ImportError:
        pass

    class _Any:
        def __getattr__(self, name):
            if name.startswith("Get"):
                return lambda key=None, default=None: default
            return lambda *a, **k: None

    app = types.ModuleType("FreeCAD")
    app.Console = _Any()
    app.GuiUp = False
    app.ParamGet = lambda path: _Any()
    app.getUserAppDataDir = lambda: "/tmp/"
    sys.modules["FreeCAD"] = app
    part = types.ModuleType("Part")
    part.Shape = object
    sys.modules["Part"] = part


ensure_freecad()

from freecad.DFM.core.recognition.testing import shapes as B  # noqa: E402

T = 2.5  # the nominal wall of the test parts
DOWN = (0, 0, -1)
UP = (0, 0, 1)


def shelled_box(t: float = T, x: float = 80, y: float = 50, z: float = 30):
    """An open-topped box with walls *t* thick: the base part."""
    return B.shell(B.box(0, 0, 0, x, y, z), t, lambda c: abs(c[2] - z) < 1e-6)


def boss(od: float, height: float, at=(40, 25), hole: float = 0.0, hole_depth=None,
         draft: float = 0.0, hole_draft: float = 0.0, t: float = T):
    """A shelled box with one boss on its floor (outer Ø *od*), cored with a
    hole Ø *hole* (at its mid depth when drafted) from the top when *hole* > 0."""
    x, y = at
    if draft:
        top_r = od / 2 - height * math.tan(math.radians(draft))
        body = B.cone((x, y, t), od / 2, top_r, height)
    else:
        body = B.cylinder((x, y, t), od / 2, height)
    s = B.fuse(shelled_box(t), body)
    if hole > 0:
        depth = height if hole_depth is None else hole_depth
        top = t + height
        if hole_draft:
            k = math.tan(math.radians(hole_draft))
            # Ø hole at mid depth, like the recogniser reports a drafted bore
            r_top, r_bottom = hole / 2 + k * depth / 2, hole / 2 - k * depth / 2
            s = B.cut(s, B.cone((x, y, top - depth), r_bottom, r_top, depth))
        else:
            s = B.cut(s, B.drill((x, y, top), DOWN, hole, depth))
    return s


def rib(thickness: float, height: float, y0: float = 20, x0: float = 15, length: float = 50,
        draft: float = 0.0, t: float = T, base=None):
    """A shelled box with a rib along X standing on its floor."""
    s = base if base is not None else shelled_box(t)
    if draft:
        d = height * math.tan(math.radians(draft))
        sec = [(x0, y0, t), (x0, y0 + thickness, t), (x0, y0 + thickness - d, t + height), (x0, y0 + d, t + height)]
        body = B.prism(sec, (length, 0, 0))
    else:
        body = B.box(x0, y0, t, x0 + length, y0 + thickness, t + height)
    return B.fuse(s, body)


def plate_with_holes(holes, size=(100, 60, 10)):
    """A plate with drilled holes: [(x, y, diameter, depth or None for through)]."""
    x, y, z = size
    s = B.box(0, 0, 0, x, y, z)
    for hx, hy, d, depth in holes:
        s = B.cut(s, B.drill((hx, hy, z), DOWN, d, depth if depth is not None else z + 1))
    return s


def part_features(shape, nominal=None, pull=(0, 0, 1), sides=(), prefs=None, hints=None):
    """Run FEATURE_ANALYZER on *shape*: its PartFeatures.

    *nominal*: None measures it (Auto); a number enters it (Manual).
    """
    from OCP.gp import gp_Dir

    from freecad.DFM.core.analyzers.feature_analyzer import FeatureAnalyzer
    from freecad.DFM.core.utils.geometry import EdgeIndex, FaceIndex
    from freecad.DFM.core.utils.nominal_wall import make_requirement

    kwargs = {
        "prefs": prefs or {},
        "PULL_DIRECTION": gp_Dir(*pull) if pull else None,
        "SIDE_ACTIONS": [gp_Dir(*s) for s in sides],
        "NOMINAL_WALL_THICKNESS": make_requirement("manual", nominal) if nominal else make_requirement(),
    }
    if hints is not None:
        kwargs["recognition_hints"] = hints
    return FeatureAnalyzer().execute(shape, FaceIndex(shape), EdgeIndex(shape), **kwargs)


def run_rule(rule, pf, shape=None, target=None, limit=None, severity=None, material=None, **kwargs):
    """Check *rule* on *pf* with the moulding process's feedback and Default
    thresholds (or *target* / *limit* / *severity*): its findings."""
    from freecad.DFM.core.processes.process import RuleLimit
    from freecad.DFM.core.registries.checks_registry import get_check_class
    from freecad.DFM.core.utils.geometry import FaceIndex

    process = moulding_process()
    config = process.materials["Default"].rule_limits.get(rule) or RuleLimit()
    if material is not None and rule in process.materials[material].rule_limits:
        override = process.materials[material].rule_limits[rule]
        config = RuleLimit(target=override.target or config.target, limit=override.limit or config.limit,
                           binary_severity=override.binary_severity or config.binary_severity)
    config = RuleLimit(
        target=str(target) if target is not None else config.target,
        limit=str(limit) if limit is not None else config.limit,
        binary_severity=severity or config.binary_severity,
    )
    if shape is not None:
        kwargs.setdefault("face_index", FaceIndex(shape))
    if material is not None:
        kwargs.setdefault("_target_material", process.materials[material])
    check = get_check_class(rule)()
    return check.run_check(pf, config, rule, feedback=process.rule_feedback.get(rule), **kwargs)


_PROCESS = None


def moulding_process():
    """The built-in injection moulding process, as Process.from_yaml reads it."""
    global _PROCESS
    if _PROCESS is None:
        import yaml

        from freecad.DFM.core.processes.process import Process

        path = os.path.join(ROOT, "freecad", "DFM", "core", "processes", "plastic_injection_molding.yaml")
        with open(path, encoding="utf-8") as f:
            _PROCESS = Process.from_yaml(yaml.safe_load(f))
    return _PROCESS


def faces_of_kind(pf, kind):
    from freecad.DFM.core.recognition import FeatureKind

    return [f for f in pf.result.features if f.kind == FeatureKind(kind)]
