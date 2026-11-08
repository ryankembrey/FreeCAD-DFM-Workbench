# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Feature hints from a FreeCAD model's history.

A PartDesign Body remembers how it was made: holes with their diameters
and threads (even cosmetic ones, which the geometry doesn't show), fillets
with their radii, pockets, pads, chamfers. This turns that history into
``core.recognition.Hint`` objects: plain data with points on the faces each
feature made, so the pure-OCP recognition matches them to its own faces.

How a feature's faces are found: the faces of the feature's result that
don't lie on its base feature's result are the ones it made; a few points
on each (from a coarse tessellation, so they are on the face) are kept if
they still lie on the final shape (later features can remove them).
Part workbench fillets and chamfers (Part::Fillet, Part::Chamfer) work the
same way through their Base.
"""

from __future__ import annotations

from typing import Optional

import FreeCAD as App  # type: ignore
import Part  # type: ignore

from ..core.recognition import FeatureKind, Hint

_TOL = 1e-3
_POINTS_PER_FACE = 3

_DEPRESSIONS = (FeatureKind.POCKET, FeatureKind.SLOT, FeatureKind.STEP, FeatureKind.CUTOUT,
                FeatureKind.HOLE, FeatureKind.DEPRESSION)
_PROTRUSIONS = (FeatureKind.BOSS, FeatureKind.RIB, FeatureKind.PROTRUSION)


def _value(x, default=None):
    try:
        return float(x.Value) if hasattr(x, "Value") else float(x)
    except Exception:
        return default


def _face_points(face) -> list[tuple[float, float, float]]:
    try:
        verts, tris = face.tessellate(max(0.1, face.BoundBox.DiagonalLength / 20.0))
    except Exception:
        return []
    out = []
    step = max(1, len(tris) // _POINTS_PER_FACE)
    for a, b, c in tris[::step][:_POINTS_PER_FACE]:
        p = (verts[a] + verts[b] + verts[c]) / 3.0
        try:
            # Project onto the face (a triangle's centre is off a curved face)
            u, v = face.Surface.parameter(p)
            p = face.Surface.value(u, v)
        except Exception:
            pass
        out.append((p.x, p.y, p.z))
    return out


def _skin(shape):
    """The faces of a shape as a compound: distances to it are distances to
    its surface (to a solid, a point inside would be at distance 0)."""
    try:
        return Part.Compound(shape.Faces)
    except Exception:
        return shape


def _distance(shape, point) -> float:
    try:
        return shape.distToShape(Part.Vertex(App.Vector(*point)))[0]
    except Exception:
        return float("inf")


def _new_face_points(result, base) -> list[tuple[float, float, float]]:
    """Points on faces of *result* that don't lie on *base*."""
    pts = []
    skin = _skin(base) if base is not None and not base.isNull() else None
    for face in result.Faces:
        for p in _face_points(face):
            if skin is None or _distance(skin, p) > _TOL:
                pts.append(p)
    return pts


def _hint_for(feature) -> Optional[tuple[tuple, dict, str]]:
    """(kinds, params, subtype) for a history feature, or None."""
    t = feature.TypeId
    if t == "PartDesign::Hole":
        params: dict = {"diameter": _value(feature.Diameter)}
        if getattr(feature, "DepthType", "") == "ThroughAll":
            params["through"] = True
        elif _value(getattr(feature, "Depth", None)) is not None:
            params["design_depth"] = _value(feature.Depth)
        if getattr(feature, "Threaded", False):
            params["threaded"] = True
            params["thread_size"] = str(getattr(feature, "ThreadSize", ""))
            params["thread_type"] = str(getattr(feature, "ThreadType", ""))
            params["thread_modeled"] = bool(getattr(feature, "ModelThread", False))
        cut = str(getattr(feature, "HoleCutType", "None"))
        subtype = ""
        if "Counterbore" in cut:
            subtype = "counterbored"
            params["design_cb_diameter"] = _value(getattr(feature, "HoleCutDiameter", None))
            params["design_cb_depth"] = _value(getattr(feature, "HoleCutDepth", None))
        elif "Countersink" in cut:
            subtype = "countersunk"
            params["design_cs_diameter"] = _value(getattr(feature, "HoleCutDiameter", None))
            params["design_cs_angle"] = _value(getattr(feature, "HoleCutCountersinkAngle", None))
        return (FeatureKind.HOLE,), {k: v for k, v in params.items() if v is not None}, subtype
    if t in ("PartDesign::Fillet", "Part::Fillet"):
        r = _value(getattr(feature, "Radius", None))
        return (FeatureKind.FILLET,), ({"design_radius": r} if r else {}), ""
    if t in ("PartDesign::Chamfer", "Part::Chamfer"):
        size = _value(getattr(feature, "Size", None))
        return (FeatureKind.CHAMFER,), ({"design_size": size} if size else {}), ""
    if t in ("PartDesign::Pocket", "PartDesign::Groove", "PartDesign::SubtractiveLoft",
             "PartDesign::SubtractivePipe", "PartDesign::SubtractiveHelix"):
        d = _value(getattr(feature, "Length", None))
        return _DEPRESSIONS, ({"design_depth": d} if d else {}), ""
    if t in ("PartDesign::Pad", "PartDesign::Revolution", "PartDesign::AdditiveLoft",
             "PartDesign::AdditivePipe"):
        h = _value(getattr(feature, "Length", None))
        return _PROTRUSIONS, ({"design_height": h} if h else {}), ""
    if t == "PartDesign::AdditiveCylinder":
        return (FeatureKind.BOSS,), {"design_diameter": 2 * (_value(feature.Radius) or 0.0)}, ""
    if t == "PartDesign::SubtractiveCylinder":
        return (FeatureKind.HOLE,), {"design_diameter": 2 * (_value(feature.Radius) or 0.0)}, ""
    return None


def _history(obj) -> list:
    """The features that made *obj*, oldest first."""
    if obj is None:
        return []
    if obj.TypeId == "PartDesign::Body":
        return [f for f in obj.Group if f.TypeId.startswith("PartDesign::")]
    if obj.TypeId.startswith("PartDesign::"):
        body = None
        try:
            body = obj.getParentGeoFeatureGroup()
        except Exception:
            pass
        return _history(body) if body is not None else [obj]
    if obj.TypeId in ("Part::Fillet", "Part::Chamfer"):
        out = [obj]
        base = getattr(obj, "Base", None)
        return _history(base) + out if base is not None else out
    return []


def _base_shape(feature):
    for name in ("BaseFeature", "Base"):
        base = getattr(feature, name, None)
        if base is not None and hasattr(base, "Shape"):
            return base.Shape
    return None


def hints_for(obj, final_shape=None) -> list[Hint]:
    """Hints from the history of *obj* (a Body, a PartDesign feature, or a
    Part fillet/chamfer), for recognising *final_shape* (default obj.Shape)."""
    final = final_shape if final_shape is not None else getattr(obj, "Shape", None)
    if final is None or final.isNull():
        return []
    hints = []
    final = _skin(final)
    placement = None
    try:
        placement = obj.getGlobalPlacement()
    except Exception:
        pass
    for feature in _history(obj):
        try:
            spec = _hint_for(feature)
            if spec is None or not hasattr(feature, "Shape") or feature.Shape.isNull():
                continue
            kinds, params, subtype = spec
            base = _base_shape(feature)
            if base is None and set(kinds) & set(_PROTRUSIONS):
                continue  # the body's first solid is the stock, not a feature on it
            points = _new_face_points(feature.Shape, base)
            on = [p for p in points if _distance(final, p) <= _TOL * 10]
            if not on and placement is not None and points:
                # feature shapes in body coordinates, final shape placed
                moved = [tuple(placement.multVec(App.Vector(*p))) for p in points]
                on = [p for p in moved if _distance(final, p) <= _TOL * 10]
            if not on:
                continue
            hints.append(Hint(kinds=tuple(kinds), points=tuple(on), params=params, subtype=subtype,
                              source=f"{feature.TypeId} '{feature.Label}'", weight=0.9))
        except Exception as exc:  # history is a bonus: never fail recognition for it
            App.Console.PrintLog(f"DFM: no hint from {getattr(feature, 'Label', feature)}: {exc}\n")
    return hints
