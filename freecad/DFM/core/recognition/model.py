# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The recognition results: plain data only.

Nothing here holds an OCP object. Faces and edges are referred to by their
1-based numbers in ``core.utils.geometry.FaceIndex`` / ``EdgeIndex``
(``TopExp.MapShapes`` order), which are FreeCAD's ``Face<n>`` / ``Edge<n>``.
So a result pickles cheaply to worker processes, round-trips through JSON
(caches, golden files) and can be compared across runs.

Units are millimetres and degrees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional


class FeatureKind(str, Enum):
    """What a feature is. String-valued, so it reads well in JSON."""

    HOLE = "hole"
    BOSS = "boss"
    RIB = "rib"
    SLOT = "slot"
    STEP = "step"
    POCKET = "pocket"
    CUTOUT = "cutout"
    FILLET = "fillet"
    CHAMFER = "chamfer"
    #: A closed cavity inside the solid (or one vented only by small holes).
    VOID = "void"
    #: A cantilever beam with a hook at its tip (moulded snap-fit).
    SNAP_FIT = "snap_fit"
    #: A thin flexible web joining two thicker sections.
    LIVING_HINGE = "living_hinge"
    #: A row or grid of like openings through one wall (vents, louvres, grilles).
    VENT = "vent"
    #: A thin wall standing round the rim of a housing, to locate its mating half.
    LIP = "lip"
    #: Embossed or engraved lettering or a logo: many small shallow shapes on one face.
    TEXT = "text"
    # Direction-dependent (annotate_directional only).
    DRAFT = "draft"
    UNDERCUT = "undercut"
    #: Faces facing down past the printer's overhang angle (print direction).
    OVERHANG = "overhang"
    #: A downward-facing face spanning between supports (print direction).
    BRIDGE = "bridge"
    # Residual regions no specific recognizer claimed.
    DEPRESSION = "depression"
    PROTRUSION = "protrusion"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


#: Order features are listed in, and which recognizer wins a contested face.
KIND_PRIORITY: tuple[FeatureKind, ...] = (
    FeatureKind.FILLET,
    FeatureKind.CHAMFER,
    FeatureKind.HOLE,
    FeatureKind.BOSS,
    FeatureKind.SNAP_FIT,
    FeatureKind.RIB,
    FeatureKind.LIVING_HINGE,
    FeatureKind.SLOT,
    FeatureKind.STEP,
    FeatureKind.POCKET,
    FeatureKind.CUTOUT,
    FeatureKind.VENT,
    FeatureKind.LIP,
    FeatureKind.TEXT,
    FeatureKind.VOID,
    FeatureKind.PROTRUSION,
    FeatureKind.DEPRESSION,
    FeatureKind.DRAFT,
    FeatureKind.UNDERCUT,
    FeatureKind.OVERHANG,
    FeatureKind.BRIDGE,
)


def _plain(value: Any) -> Any:
    """Make *value* JSON-friendly (tuples become lists, enums their value)."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, float):
        return round(value, 9)
    return value


def _tuplify(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_tuplify(v) for v in value)
    return value


@dataclass(frozen=True)
class Frame:
    """A placement in model coordinates: origin, main axis, optional reference
    direction (e.g. a rib's length direction)."""

    origin: tuple[float, float, float]
    axis: tuple[float, float, float]
    ref: Optional[tuple[float, float, float]] = None

    def to_dict(self) -> dict:
        return {"origin": _plain(self.origin), "axis": _plain(self.axis),
                "ref": _plain(self.ref) if self.ref else None}

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> Optional["Frame"]:
        if not d:
            return None
        return cls(tuple(d["origin"]), tuple(d["axis"]), tuple(d["ref"]) if d.get("ref") else None)


@dataclass(frozen=True)
class Feature:
    """One recognised feature.

    * ``faces``: the faces the feature is made of. Each face is owned by at
      most one feature.
    * ``support_faces``: faces it sits on or opens into (a boss's base, a
      hole's entry face). Not owned; often owned by another feature.
    * ``params``: see README.md for the parameters of each kind.
    * ``confidence``: 0..1. ``evidence`` says why, in words.
    """

    id: int
    kind: FeatureKind
    subtype: str
    faces: tuple[int, ...]
    support_faces: tuple[int, ...] = ()
    edges: tuple[int, ...] = ()
    params: Mapping[str, Any] = field(default_factory=dict)
    frame: Optional[Frame] = None
    confidence: float = 1.0
    evidence: tuple[str, ...] = ()
    parent: Optional[int] = None
    interacts_with: tuple[int, ...] = ()

    def __hash__(self) -> int:
        return hash((self.id, self.kind, self.faces))

    @property
    def face_labels(self) -> tuple[str, ...]:
        """FreeCAD sub-element names, e.g. ("Face3", "Face4")."""
        return tuple(f"Face{i}" for i in self.faces)

    @property
    def label(self) -> str:
        sub = f" ({self.subtype.replace('_', ' ')})" if self.subtype else ""
        return f"{self.kind.value.replace('_', ' ').capitalize()} {self.id}{sub}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "subtype": self.subtype,
            "faces": list(self.faces),
            "support_faces": list(self.support_faces),
            "edges": list(self.edges),
            "params": _plain(dict(self.params)),
            "frame": self.frame.to_dict() if self.frame else None,
            "confidence": round(float(self.confidence), 4),
            "evidence": list(self.evidence),
            "parent": self.parent,
            "interacts_with": list(self.interacts_with),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Feature":
        return cls(
            id=int(d["id"]),
            kind=FeatureKind(d["kind"]),
            subtype=d.get("subtype", ""),
            faces=tuple(d.get("faces", ())),
            support_faces=tuple(d.get("support_faces", ())),
            edges=tuple(d.get("edges", ())),
            params={k: _tuplify(v) for k, v in d.get("params", {}).items()},
            frame=Frame.from_dict(d.get("frame")),
            confidence=float(d.get("confidence", 1.0)),
            evidence=tuple(d.get("evidence", ())),
            parent=d.get("parent"),
            interacts_with=tuple(d.get("interacts_with", ())),
        )


@dataclass(frozen=True)
class FaceSignature:
    """What a face looks like, so goldens survive renumbering between
    OpenCASCADE versions: surface kind, area and centroid."""

    index: int
    kind: str
    area: float
    centroid: tuple[float, float, float]

    def to_dict(self) -> dict:
        return {"index": self.index, "kind": self.kind, "area": round(self.area, 6),
                "centroid": [round(c, 6) for c in self.centroid]}

    @classmethod
    def from_dict(cls, d: dict) -> "FaceSignature":
        return cls(int(d["index"]), d["kind"], float(d["area"]), tuple(d["centroid"]))


@dataclass
class RecognitionResult:
    """Everything recognize() found for one shape."""

    features: tuple[Feature, ...]
    face_count: int
    edge_count: int
    fingerprint: str
    engine_version: str
    options: dict
    #: Edge number -> "convex" / "concave" / "smooth" / "seam" / "variable" (the
    #: AAG edge attribute, for overlays and debugging).
    edge_convexity: dict[int, str] = field(default_factory=dict)
    #: Face number -> signature (kind, area, centroid).
    face_signatures: dict[int, FaceSignature] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    #: How the part as a whole could be modelled: extrusion, 2.5d,
    #: revolve or mixed, with its direction (see form.py).
    form: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._by_id = {f.id: f for f in self.features}
        self._owner: dict[int, int] = {}
        self._users: dict[int, list[int]] = {}
        for f in self.features:
            for face in f.faces:
                self._owner.setdefault(face, f.id)
                self._users.setdefault(face, []).append(f.id)
            for face in f.support_faces:
                self._users.setdefault(face, []).append(f.id)

    # -- Queries ---------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.features)

    def __iter__(self):
        return iter(self.features)

    def by_id(self, feature_id: int) -> Optional[Feature]:
        return self._by_id.get(feature_id)

    def of_kind(self, *kinds: FeatureKind) -> tuple[Feature, ...]:
        return tuple(f for f in self.features if f.kind in kinds)

    def owner_of(self, face: int) -> Optional[Feature]:
        """The feature made of face number *face*, or None."""
        fid = self._owner.get(face)
        return self._by_id[fid] if fid is not None else None

    def on_face(self, face: int) -> tuple[Feature, ...]:
        """Features that own face *face* or use it as a support face."""
        return tuple(self._by_id[i] for i in dict.fromkeys(self._users.get(face, ())))

    def confident(self, minimum: float) -> tuple[Feature, ...]:
        return tuple(f for f in self.features if f.confidence >= minimum)

    def unowned_faces(self) -> tuple[int, ...]:
        return tuple(i for i in range(1, self.face_count + 1) if i not in self._owner)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.features:
            counts[f.kind.value] = counts.get(f.kind.value, 0) + 1
        return counts

    # -- Serialisation -----------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "engine_version": self.engine_version,
            "fingerprint": self.fingerprint,
            "face_count": self.face_count,
            "edge_count": self.edge_count,
            "options": _plain(self.options),
            "features": [f.to_dict() for f in self.features],
            "edge_convexity": {str(k): v for k, v in self.edge_convexity.items()},
            "face_signatures": [s.to_dict() for s in self.face_signatures.values()],
            "timings": _plain(self.timings),
            "warnings": list(self.warnings),
            "form": _plain(self.form),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RecognitionResult":
        sigs = [FaceSignature.from_dict(s) for s in d.get("face_signatures", [])]
        return cls(
            features=tuple(Feature.from_dict(f) for f in d.get("features", [])),
            face_count=int(d.get("face_count", 0)),
            edge_count=int(d.get("edge_count", 0)),
            fingerprint=d.get("fingerprint", ""),
            engine_version=d.get("engine_version", ""),
            options=dict(d.get("options", {})),
            edge_convexity={int(k): v for k, v in d.get("edge_convexity", {}).items()},
            face_signatures={s.index: s for s in sigs},
            timings=dict(d.get("timings", {})),
            warnings=tuple(d.get("warnings", ())),
            form=dict(d.get("form", {})),
        )

    def __getstate__(self):
        return self.to_dict()

    def __setstate__(self, state):
        other = RecognitionResult.from_dict(state)
        self.__dict__.update(other.__dict__)


@dataclass
class DirectionalReport:
    """What annotate_directional() adds for one pull / print direction.

    * ``features``: DRAFT and UNDERCUT features (ids follow on from the
      recognition result's).
    * ``annotations``: feature id -> extra parameters for that direction
      (draft of its walls, "horizontal" for FDM holes, ...).
    * ``face_draft``: face number -> draft angle in degrees (signed).
    """

    direction: Optional[tuple[float, float, float]]
    print_direction: Optional[tuple[float, float, float]]
    features: tuple[Feature, ...] = ()
    annotations: dict[int, dict] = field(default_factory=dict)
    face_draft: dict[int, float] = field(default_factory=dict)

    def feature_params(self, feature_id: int) -> dict:
        return dict(self.annotations.get(feature_id, {}))

    def to_dict(self) -> dict:
        return {
            "direction": _plain(self.direction),
            "print_direction": _plain(self.print_direction),
            "features": [f.to_dict() for f in self.features],
            "annotations": {str(k): _plain(v) for k, v in self.annotations.items()},
            "face_draft": {str(k): round(v, 6) for k, v in self.face_draft.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DirectionalReport":
        return cls(
            direction=tuple(d["direction"]) if d.get("direction") else None,
            print_direction=tuple(d["print_direction"]) if d.get("print_direction") else None,
            features=tuple(Feature.from_dict(f) for f in d.get("features", [])),
            annotations={int(k): v for k, v in d.get("annotations", {}).items()},
            face_draft={int(k): float(v) for k, v in d.get("face_draft", {}).items()},
        )


def sort_key(feature: Feature) -> tuple:
    """Deterministic listing order: kind priority, then first face."""
    return (KIND_PRIORITY.index(feature.kind), min(feature.faces) if feature.faces else 0)


def renumber(features: Iterable[Feature], start: int = 1) -> tuple[Feature, ...]:
    """Sort *features* and give them ids start, start+1, ... keeping the
    parent / interacts_with links pointing at the right features."""
    ordered = sorted(features, key=sort_key)
    mapping = {f.id: start + i for i, f in enumerate(ordered)}
    out = []
    for f in ordered:
        out.append(Feature(
            id=mapping[f.id], kind=f.kind, subtype=f.subtype, faces=f.faces,
            support_faces=f.support_faces, edges=f.edges, params=f.params, frame=f.frame,
            confidence=f.confidence, evidence=f.evidence,
            parent=mapping.get(f.parent) if f.parent is not None else None,
            interacts_with=tuple(sorted(mapping[i] for i in f.interacts_with if i in mapping)),
        ))
    return tuple(out)
