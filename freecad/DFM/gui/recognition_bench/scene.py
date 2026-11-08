# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The bench's view of the model, drawn by the results view's HighlightScene.

Like the results panel, the model is hidden while the bench shows it and
the scene draws it instead. The rest of the model is neutral gray. Each face
is drawn in one colour, so there is no "selected" or "support" shade:

* **Focus** is what the tree has selected: a feature, a whole kind, the
  faces in no feature, or a few faces (a support list, one face, the faces
  picked for a missed feature). It is drawn in full colour. A feature's
  colour is its kind's colour.
* **Context** is everything else that was found. When "Colour all
  features" is on, it is drawn in a pale version of its kind's colour, so
  the focus stands out and the rest still reads. When it is off, it is
  left gray.

HighlightScene outlines the focus where it meets other faces. Where the
focus is hidden behind other faces, it shows through as a dashed outline.
Edges can also be coloured by the graph's convexity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from ...core.recognition import FeatureKind

KIND_COLORS = {
    FeatureKind.HOLE: "#1F77B4",
    FeatureKind.BOSS: "#FF7F0E",
    FeatureKind.RIB: "#2CA02C",
    FeatureKind.SLOT: "#9467BD",
    FeatureKind.STEP: "#8C564B",
    FeatureKind.POCKET: "#E377C2",
    FeatureKind.CUTOUT: "#BCBD22",
    FeatureKind.FILLET: "#17BECF",
    FeatureKind.CHAMFER: "#F5C400",
    FeatureKind.DRAFT: "#AEC7E8",
    FeatureKind.UNDERCUT: "#D62728",
    FeatureKind.VOID: "#393B79",
    FeatureKind.SNAP_FIT: "#E6550D",
    FeatureKind.LIVING_HINGE: "#31A354",
    FeatureKind.VENT: "#6BAED6",
    FeatureKind.LIP: "#756BB1",
    FeatureKind.TEXT: "#636363",
    FeatureKind.OVERHANG: "#FD8D3C",
    FeatureKind.BRIDGE: "#9E9AC8",
    FeatureKind.DEPRESSION: "#C49C94",
    FeatureKind.PROTRUSION: "#C5B0D5",
}
#: Faces in no feature: the warning amber of the results panel.
UNOWNED_COLOR = "#D4900A"
#: Faces shown on their own (a support list, a face): the panel's accent.
FACES_COLOR = "#3D8FD8"
#: Faces picked in the 3D view for a missed feature.
PICKED_COLOR = "#D633FF"
#: How much of the kind colour the context keeps (the rest is the gray).
CONTEXT_STRENGTH = 0.38
_REST = (0.80, 0.81, 0.82)

CONVEXITY_COLORS = {
    "convex": "#D62728",
    "concave": "#1F77B4",
    "smooth": "#2CA02C",
    "variable": "#FF7F0E",
}


def kind_color(kind) -> str:
    return KIND_COLORS.get(kind, "#999999")


def pale(color: str, strength: float = CONTEXT_STRENGTH) -> str:
    """*color* mixed into the scene's neutral gray."""
    h = color.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    mix = [strength * c + (1.0 - strength) * r for c, r in zip(rgb, _REST)]
    return "#" + "".join(f"{round(c * 255):02X}" for c in mix)


@dataclass
class Focus:
    """What the tree has selected."""

    kind: str = "none"             # none | feature | kind | unowned | faces
    feature: Optional[int] = None
    feature_kind: Optional[FeatureKind] = None
    faces: tuple = ()
    color: str = FACES_COLOR

    @classmethod
    def of_feature(cls, fid: int) -> "Focus":
        return cls("feature", feature=fid)

    @classmethod
    def of_kind(cls, kind: FeatureKind) -> "Focus":
        return cls("kind", feature_kind=kind)

    @classmethod
    def of_faces(cls, faces: Iterable[int], color: str = FACES_COLOR) -> "Focus":
        return cls("faces", faces=tuple(sorted(set(faces))), color=color)


@dataclass
class Style:
    colour_all: bool = True
    show_unowned: bool = True
    convexity: bool = False
    picked: list = field(default_factory=list)


def face_colors(features, unowned: Iterable[int], focus: Focus, style: Style) -> dict[int, str]:
    """{0-based face index: colour}, context first, focus over it."""
    out: dict[int, str] = {}
    focused: dict[int, str] = {}
    for f in features:
        color = kind_color(f.kind)
        hit = (focus.kind == "feature" and f.id == focus.feature) or \
              (focus.kind == "kind" and f.kind == focus.feature_kind)
        for face in f.faces:
            if hit:
                focused[face - 1] = color
            elif style.colour_all and face - 1 not in out:
                out[face - 1] = pale(color) if focus.kind != "none" else color
    unowned = list(unowned)
    if focus.kind == "unowned":
        for face in unowned:
            focused[face - 1] = UNOWNED_COLOR
    elif style.show_unowned:
        for face in unowned:
            out[face - 1] = pale(UNOWNED_COLOR) if focus.kind != "none" else UNOWNED_COLOR
    if focus.kind == "faces":
        for face in focus.faces:
            focused[face - 1] = focus.color
    out.update(focused)
    for face in style.picked:
        out[face - 1] = PICKED_COLOR
    return out


def edge_colors(edge_convexity: dict[int, str]) -> dict[int, str]:
    return {e - 1: CONVEXITY_COLORS[c] for e, c in edge_convexity.items() if c in CONVEXITY_COLORS}


class BenchScene:
    """The bench's view of the model. Safe to use without a 3D view (it
    then does nothing), so the panel works headless in tests."""

    def __init__(self):
        self._scene = None
        self._target = None
        self._target_visible = True

    @property
    def active(self) -> bool:
        return self._scene is not None and bool(getattr(self._scene, "visible", False))

    def attach(self, target_object, rebuild: bool = False) -> None:
        """Draw *target_object*. *rebuild* re-meshes it: after a recompute
        the faces may have changed."""
        if target_object is self._target and self._scene is not None and not rebuild:
            return
        self.remove()
        self._target = target_object
        if target_object is None:
            return
        try:
            self._target_visible = bool(target_object.ViewObject.Visibility)
        except Exception:
            self._target_visible = True
        try:
            from ..results.highlight_scene import HighlightScene
            self._scene = HighlightScene(target_object)
        except Exception:
            self._scene = None

    def _set_target_visible(self, visible: bool) -> None:
        try:
            self._target.ViewObject.Visibility = visible
        except Exception:
            pass

    def draw(self, faces: dict[int, str], edges: dict[int, str]) -> None:
        if self._scene is None:
            return
        try:
            self._scene.set_highlight(faces, edges)
            self._scene.show()
            if self._scene.visible:
                self._set_target_visible(False)
        except Exception:
            pass

    def plain(self) -> None:
        """Show the model itself (pickable with FreeCAD's selection)."""
        if self._scene is not None:
            try:
                self._scene.hide()
            except Exception:
                pass
        if self._target is not None:
            self._set_target_visible(True)

    def remove(self) -> None:
        if self._scene is not None:
            try:
                self._scene.remove()
            except Exception:
                pass
        if self._target is not None:
            self._set_target_visible(self._target_visible)
        self._scene = None
        self._target = None
