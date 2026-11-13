# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Names for differences: "Boss Ø10", "Pocket 16 × 10", "Hole Ø8 → Ø10".

A difference is named after the recognised feature that owns most of the
faces it touches. Without one, it gets a plain name from its kind.
"""

from __future__ import annotations

from typing import Iterable, Optional

from OCP.TopoDS import TopoDS_Shape

from . import geometry as G

_KIND_NAMES = {
    "hole": "Hole", "boss": "Boss", "rib": "Rib", "slot": "Slot", "step": "Step", "pocket": "Pocket",
    "cutout": "Cutout", "fillet": "Fillet", "chamfer": "Chamfer", "void": "Hollow", "snap_fit": "Snap fit",
    "living_hinge": "Living hinge", "vent": "Vent", "lip": "Lip", "text": "Text",
    "depression": "Recess", "protrusion": "Raised area",
}


def fmt_mm(value: float) -> str:
    """A length the way a drawing shows it: 8, 7.5, 0.25."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def fmt_volume(mm3: float, signed: bool = False, unit: bool = True) -> str:
    """"1.21 cm³" ("+1.21 cm³" signed; "1.21" without the unit, as in "99.02 → 97.80 cm³")."""
    cm3 = mm3 / 1000.0
    sign = ("+" if cm3 > 0 else "−" if cm3 < 0 else "") if signed else ""
    value = abs(cm3)
    digits = 2 if value < 100 else 1
    return f"{sign}{value:.{digits}f}" + (" cm³" if unit else "")


def fmt_dims(box: G.Box) -> str:
    sx, sy, sz = box.size
    return " × ".join(fmt_mm(v) for v in (sx, sy, sz)) + " mm"


class FaceFeatures:
    """Which recognised feature each face belongs to, and the shape's faces."""

    def __init__(self, shape: TopoDS_Shape, result=None):
        self.shape = shape
        self._faces = G.face_map_of(shape)
        self._areas: dict[int, float] = {}
        self.features = {}
        self.owner: dict[int, object] = {}
        if result is not None:
            for feature in result.features:
                kind = getattr(feature.kind, "value", str(feature.kind))
                if kind not in _KIND_NAMES:
                    continue  # draft, undercut, overhang...: not a thing to name
                self.features[feature.id] = feature
                for f in feature.faces:
                    # The more specific feature wins a shared face (a hole in a boss).
                    if f not in self.owner or _specific(feature) > _specific(self.owner[f]):
                        self.owner[f] = feature

    def face(self, index: int):
        return self._faces.FindKey(index)

    def area(self, index: int) -> float:
        if index not in self._areas:
            self._areas[index] = G.face_area(self.face(index))
        return self._areas[index]

    def key_faces(self, areas: dict[int, float], fraction: float = 0.2) -> dict[int, float]:
        """The faces a difference covers a good share of (not, say, the big top face)."""
        return {i: a for i, a in areas.items() if i and a >= fraction * max(self.area(i), 1e-12)}

    def main_feature(self, faces: Iterable[int], weights: Optional[dict[int, float]] = None):
        totals: dict[int, float] = {}
        for f in faces:
            feature = self.owner.get(f)
            if feature is not None:
                totals[feature.id] = totals.get(feature.id, 0.0) + (weights or {}).get(f, self.area(f))
        if not totals:
            return None
        return self.features[max(totals, key=totals.get)]

    def centre_of(self, faces: Iterable[int]) -> Optional[G.Vec]:
        total, acc = 0.0, (0.0, 0.0, 0.0)
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps

        for f in faces:
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(self.face(f), props)
            a = abs(props.Mass())
            c = props.CentreOfMass()
            acc = G.vadd(acc, G.vmul((c.X(), c.Y(), c.Z()), a))
            total += a
        return G.vmul(acc, 1.0 / total) if total > 0 else None

    def point_on(self, faces: Iterable[int]) -> Optional[G.Vec]:
        """A point on the largest of *faces*, near its middle (for a badge)."""
        faces = list(faces)
        if not faces:
            return None
        biggest = max(faces, key=self.area)
        target = self.centre_of([biggest])
        pts = G.sample_points(self.face(biggest))
        if not pts:
            return target
        return min(pts, key=lambda p: G.vlen(G.vsub(p, target)))


def _specific(feature) -> int:
    kind = getattr(feature.kind, "value", str(feature.kind))
    return 0 if kind in ("protrusion", "depression") else 1


def feature_kind(feature) -> str:
    return getattr(feature.kind, "value", str(feature.kind))


def feature_size(feature) -> Optional[float]:
    """The one number a feature is known by (a hole's diameter), or None."""
    p = feature.params
    kind = feature_kind(feature)
    key = {"hole": "diameter", "boss": "outer_diameter", "fillet": "radius", "slot": "width",
           "rib": "thickness"}.get(kind)
    value = p.get(key) if key else None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def feature_name(feature, faces: Optional[FaceFeatures] = None) -> str:
    kind = feature_kind(feature)
    base = _KIND_NAMES.get(kind, kind.replace("_", " ").capitalize())
    p = feature.params
    size = feature_size(feature)
    if kind in ("hole", "boss") and size:
        name = f"{base} Ø{fmt_mm(size)}"
        if kind == "hole" and p.get("threaded"):
            name += " (threaded)"
        return name
    if kind == "fillet" and size:
        return f"{base} R{fmt_mm(size)}"
    if kind == "slot" and size:
        return f"{base} {fmt_mm(size)} wide"
    if kind in ("pocket", "cutout", "step", "depression") and faces is not None:
        box = G.bounding_box(G.compound(faces.face(f) for f in feature.faces), optimal=False)
        dims = sorted((v for v in box.size), reverse=True)[:2]
        if dims and dims[-1] > 1e-6:
            return f"{base} {fmt_mm(dims[0])} × {fmt_mm(dims[1])}"
    return base


def same_feature(a, b, size_tol: float = 1e-3) -> bool:
    """Same kind and size (two holes of the same diameter, say)."""
    if a is None or b is None or feature_kind(a) != feature_kind(b):
        return False
    sa, sb = feature_size(a), feature_size(b)
    if sa is None or sb is None:
        return True
    return abs(sa - sb) <= max(size_tol, 1e-3 * max(sa, sb))
