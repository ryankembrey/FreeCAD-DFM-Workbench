# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The results view of the model: one Coin node, drawn for legibility.

While findings are highlighted the model is hidden and this node draws it
instead, so findings can be styled per face and per edge without touching
the document:

* Flagged faces are opaque and shaded in their severity color, so the
  part's shape still reads when most of it is flagged.
* The rest of the model is a neutral light gray with thin dark edges, so a
  red or amber part color can't be mistaken for a finding.
* A strong outline marks where a flagged region ends. Edges between two
  faces of the same region stay thin, so a heavily flagged part doesn't
  turn into a web of outlines.
* Flagged edges are thick, with a dark halo, so they read on any face,
  including one flagged in the same color.
* A finding hidden behind an unflagged part of the model shows through it
  as a dashed outline, like hidden lines on a drawing, over a faint tint
  that keeps the shading of the surface in front. Findings behind other
  findings don't show through, and when a large share of the part is
  flagged nothing does: the question then isn't what's hidden.

The faces and edges are meshed once, when results open, a few times finer
than FreeCAD's own display so curves look round. Changing what is highlighted only rearranges which face
and edge nodes sit in which group, so clicking through findings is quick.

Draw order (it is what makes "hidden behind gray" work)::

    switch "DFM_ResultsScene"
      separator (unpickable, two-sided lighting)
        faces:  coordinates, polygon offset
                1 rest of the model
                2 hidden findings' tint: depth test GREATER, no depth
                  write, blended in order, so it only lands where the
                  rest of the model is in front
                3 flagged faces
        lines:  4 rest edges, 5 inner edges, 6 region outlines,
                7 flagged edges (halo, then color)
                8 hidden outlines and edges: dashed, depth test GREATER
"""

from typing import Optional

from pivy import coin

import FreeCAD as App  # type: ignore

from ...app.prefs import dfm_params
from ..utils.view import get_active_view

RGB = tuple[float, float, float]

ROOT_NAME = "DFM_ResultsScene"

# The rest of the model: neutral, so it never looks like a finding.
REST_COLOR: RGB = (0.80, 0.81, 0.82)
REST_EDGE_COLOR: RGB = (0.28, 0.29, 0.31)

# Errors draw over warnings, which draw over everything else.
_PRIORITY = {"#E24B4A": 0, "#D4900A": 1}

# Hidden findings only show while the flagged faces are at most this share
# of the part (or this many, on small parts).
XRAY_MAX_SHARE = 0.25
XRAY_MIN_FACES = 20

# Mesh this much finer than FreeCAD's own display, so curved edges and
# small holes look round. OpenCASCADE's 0.5 rad angle limit then sets the
# segment count on small holes (about 13 per circle).
MESH_REFINE = 4.0

_SWITCH_NONE = -1
_SWITCH_ALL = -3
_DASHED = 0xF0F0


def hex_to_rgb(hex_color: str) -> RGB:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _scale(rgb: RGB, k: float) -> RGB:
    return (rgb[0] * k, rgb[1] * k, rgb[2] * k)


def _rank(color: str) -> int:
    return _PRIORITY.get(color.upper(), 9)


def merge_colors(pairs) -> dict[int, str]:
    """{index: color} from (index, color) pairs; the most severe color wins."""
    out: dict[int, str] = {}
    for index, color in pairs:
        old = out.get(index)
        if old is None or _rank(color) < _rank(old):
            out[index] = color
    return out


def display_deflection(target_object, shape) -> float:
    """The mesh deflection FreeCAD uses to display *target_object*, in mm.

    It follows the object's Deviation, so a part shown coarsely or finely
    in FreeCAD is meshed in proportion here.
    """
    try:
        deviation = float(target_object.ViewObject.Deviation)
    except Exception:
        deviation = 0.5
    bb = shape.BoundBox
    size = (bb.XMax - bb.XMin) + (bb.YMax - bb.YMin) + (bb.ZMax - bb.ZMin)
    return max(size / 300.0 * deviation, 1e-4)


def _prefs() -> dict:
    p = dfm_params()
    return {
        "edge_width": max(p.GetInt("ResultsOverlayEdgeWidth", 3), 1),
        "xray": p.GetBool("ResultsXRay", True),
        "xray_strength": min(max(p.GetInt("ResultsXRayOpacity", 35), 0), 100) / 100.0,
        "rest_transparency": min(max(p.GetInt("ResultsRestTransparency", 0), 0), 90) / 100.0,
    }


class HighlightScene:
    """Draws *target_object* with findings highlighted, in place of the object."""

    def __init__(self, target_object):
        self.target_object = target_object
        self._view = None
        self._parent = None
        self._switch = None
        self._built = False
        self._attached = False
        self._face_nodes: list = []
        self._edge_nodes: list = []
        self._face_edges: list[list[int]] = []
        self._edge_faces: list[list[int]] = []
        self._faces: dict[int, str] = {}
        self._edges: dict[int, str] = {}
        # Display style; set_style() overrides these (the report uses it).
        self._rest_color: RGB = REST_COLOR
        self._rest_transparency: Optional[float] = None
        self._xray: Optional[bool] = None

    # -- Public ------------------------------------------------------------

    @property
    def visible(self) -> bool:
        return self._attached and self._switch is not None and \
            self._switch.whichChild.getValue() == _SWITCH_ALL

    def set_highlight(self, faces: dict[int, str], edges: dict[int, str]) -> None:
        """Show the model with these faces and edges (by 0-based index) in these colors."""
        self._faces = dict(faces)
        self._edges = dict(edges)
        if not self._ensure_built():
            return
        self._regroup()

    def set_style(self, rest_color: Optional[RGB] = None, rest_transparency: Optional[float] = None,
                  xray: Optional[bool] = None) -> None:
        """Override the look; None goes back to the default (preferences)."""
        self._rest_color = tuple(rest_color) if rest_color else REST_COLOR  # type: ignore[assignment]
        self._rest_transparency = rest_transparency
        self._xray = xray
        if self._built:
            self._regroup()

    def show(self) -> None:
        if not self._ensure_built():
            return
        self._attach()
        self._switch.whichChild = _SWITCH_ALL

    def hide(self) -> None:
        if self._built and self._switch is not None:
            self._switch.whichChild = _SWITCH_NONE

    def remove(self) -> None:
        """Take the node out of the 3D view and drop the geometry."""
        if self._attached and self._parent is not None:
            try:
                self._parent.removeChild(self._switch)
            except Exception:
                pass
        self._attached = False
        self._view = None
        self._parent = None
        self._built = False
        self._switch = None
        self._face_nodes = []
        self._edge_nodes = []

    # -- Build -------------------------------------------------------------

    def _ensure_built(self) -> bool:
        if self._built:
            return True
        try:
            self._build()
            self._built = True
        except Exception as exc:
            App.Console.PrintError(f"DFM: could not build the results view: {exc}\n")
            self._built = False
        return self._built

    def _attach(self) -> None:
        if self._attached:
            return
        view = get_active_view()
        if view is None:
            return
        parent = self._model_parent(view)
        parent.addChild(self._switch)
        self._view = view
        self._parent = parent
        self._attached = True

    def _model_parent(self, view):
        """The scene node that holds the model's own node, to add ours beside it.

        Two reasons. FreeCAD's saveImage() renders only the document's part
        of the scene, not the whole viewer, so a node added to the viewer's
        root is on screen but missing from report images. And inside a
        container (an App::Part, say) this node carries the container's
        placement, which the model's shape doesn't include.
        """
        root = view.getSceneGraph()
        try:
            target = self.target_object.ViewObject.RootNode
        except Exception:
            target = None
        if target is not None:
            group_type = coin.SoGroup.getClassTypeId()
            queue = [(root, 0)]
            while queue:
                node, depth = queue.pop(0)
                try:
                    if not node.isOfType(group_type):
                        continue
                    group = coin.cast(node, "SoGroup")
                    if group.findChild(target) >= 0:
                        return group
                    if depth < 8:
                        queue.extend((group.getChild(i), depth + 1)
                                     for i in range(group.getNumChildren()))
                except Exception:
                    continue
        App.Console.PrintWarning(
            "DFM: could not find the model in the scene; report images may not show highlights.\n")
        return root

    def _build(self) -> None:
        shape = self.target_object.Shape
        faces = shape.Faces    # each access builds a new list: read once
        edges = shape.Edges
        tol = display_deflection(self.target_object, shape) / MESH_REFINE

        # Faces: one shared coordinate list, one indexed face set per face.
        points: list = []
        self._face_nodes = []
        for face in faces:
            node = coin.SoIndexedFaceSet()
            try:
                verts, tris = face.tessellate(tol)
            except Exception:
                verts, tris = [], []
            base = len(points)
            points.extend((v.x, v.y, v.z) for v in verts)
            index: list[int] = []
            for a, b, c in tris:
                index.extend((base + a, base + b, base + c, -1))
            if index:
                node.coordIndex.setValues(0, len(index), index)
            self._face_nodes.append(node)

        # Edges: the same, as line sets.
        line_points: list = []
        self._edge_nodes = []
        for edge in edges:
            node = coin.SoIndexedLineSet()
            try:
                pts = edge.discretize(Deflection=tol)
            except Exception:
                pts = []
            if len(pts) >= 2:
                base = len(line_points)
                line_points.extend((p.x, p.y, p.z) for p in pts)
                index = list(range(base, base + len(pts))) + [-1]
                node.coordIndex.setValues(0, len(index), index)
            self._edge_nodes.append(node)

        # Which edges bound each face, and which faces meet at each edge.
        by_hash: dict[int, list[int]] = {}
        for i, edge in enumerate(edges):
            by_hash.setdefault(edge.hashCode(), []).append(i)
        self._face_edges = []
        self._edge_faces = [[] for _ in edges]
        for f, face in enumerate(faces):
            found: list[int] = []
            for edge in face.Edges:
                for i in by_hash.get(edge.hashCode(), ()):
                    if i not in found and edges[i].isSame(edge):
                        found.append(i)
                        self._edge_faces[i].append(f)
            self._face_edges.append(found)

        # -- Scene graph ---------------------------------------------------
        self._switch = coin.SoSwitch()
        self._switch.setName(coin.SbName(ROOT_NAME))
        self._switch.whichChild = _SWITCH_NONE
        root = coin.SoSeparator()
        self._switch.addChild(root)

        pick = coin.SoPickStyle()
        pick.style = coin.SoPickStyle.UNPICKABLE
        root.addChild(pick)

        hints = coin.SoShapeHints()
        # Unknown ordering turns on two-sided lighting, so a face lights the
        # same whichever way its triangles wind.
        hints.vertexOrdering = coin.SoShapeHints.UNKNOWN_ORDERING
        hints.shapeType = coin.SoShapeHints.UNKNOWN_SHAPE_TYPE
        hints.creaseAngle = 0.8  # smooth within a face; faces never share vertices
        root.addChild(hints)

        self._face_coords = coin.SoCoordinate3()
        if points:
            self._face_coords.point.setValues(0, len(points), points)
        self._line_coords = coin.SoCoordinate3()
        if line_points:
            self._line_coords.point.setValues(0, len(line_points), line_points)

        depth_cls = getattr(coin, "SoDepthBuffer", None)
        self._xray_supported = depth_cls is not None

        # Blending in order, not delayed to the end of the frame like other
        # transparent shapes: the tint must be drawn before the flagged faces.
        blend_cls = getattr(coin, "SoTransparencyType", None)
        self._tint_supported = self._xray_supported and blend_cls is not None

        def hidden_only(sep, blend=False):
            """Draw *sep*'s children only where something is already in front."""
            depth = depth_cls()
            depth.test = True
            depth.write = False
            depth.function = depth_cls.GREATER
            sep.addChild(depth)
            flat = coin.SoLightModel()
            flat.model = coin.SoLightModel.BASE_COLOR
            sep.addChild(flat)
            if blend:
                kind = blend_cls()
                kind.value = blend_cls.BLEND
                sep.addChild(kind)

        # 1-3: faces.
        faces_sep = coin.SoSeparator()
        faces_sep.addChild(self._face_coords)
        offset = coin.SoPolygonOffset()  # push faces back so edges draw on top
        offset.factor = 1.0
        offset.units = 1.0
        faces_sep.addChild(offset)
        self._rest_faces = coin.SoSeparator()
        faces_sep.addChild(self._rest_faces)
        self._xray_faces = coin.SoSeparator()
        self._xray_faces_switch = coin.SoSwitch()
        if self._tint_supported:
            outer = coin.SoSeparator()
            hidden_only(outer, blend=True)
            outer.addChild(self._xray_faces)
            self._xray_faces_switch.addChild(outer)
        faces_sep.addChild(self._xray_faces_switch)
        self._flagged_faces = coin.SoSeparator()
        faces_sep.addChild(self._flagged_faces)
        root.addChild(faces_sep)

        # 4-8: lines.
        lines_sep = coin.SoSeparator()
        flat = coin.SoLightModel()
        flat.model = coin.SoLightModel.BASE_COLOR
        lines_sep.addChild(flat)
        lines_sep.addChild(self._line_coords)
        self._rest_lines = coin.SoSeparator()
        self._inner_lines = coin.SoSeparator()
        self._outline_lines = coin.SoSeparator()
        self._flagged_lines = coin.SoSeparator()
        for sep in (self._rest_lines, self._inner_lines, self._outline_lines, self._flagged_lines):
            lines_sep.addChild(sep)
        self._xray_lines = coin.SoSeparator()
        self._xray_lines_switch = coin.SoSwitch()
        if self._xray_supported:
            outer = coin.SoSeparator()
            hidden_only(outer)
            outer.addChild(self._xray_lines)
            self._xray_lines_switch.addChild(outer)
        lines_sep.addChild(self._xray_lines_switch)
        root.addChild(lines_sep)

    # -- Styling -----------------------------------------------------------

    @staticmethod
    def _material(rgb: RGB, transparency: float = 0.0, glow: float = 0.0, shiny: bool = False):
        mat = coin.SoMaterial()
        mat.diffuseColor.setValue(*rgb)
        mat.ambientColor.setValue(*_scale(rgb, 0.35))
        mat.emissiveColor.setValue(*_scale(rgb, glow))
        if shiny:
            mat.specularColor.setValue(0.15, 0.15, 0.15)
            mat.shininess.setValue(0.2)
        else:
            mat.specularColor.setValue(0.0, 0.0, 0.0)
        mat.transparency.setValue(transparency)
        return mat

    @staticmethod
    def _line_style(width: float, pattern: Optional[int] = None):
        style = coin.SoDrawStyle()
        style.lineWidth = float(width)
        if pattern is not None:
            style.linePattern = pattern
        return style

    @staticmethod
    def _by_color(index_color: dict[int, str]) -> list[tuple[str, list[int]]]:
        """[(color, [indices])], least severe first, so errors draw last (on top)."""
        groups: dict[str, list[int]] = {}
        for index, color in index_color.items():
            groups.setdefault(color, []).append(index)
        return sorted(groups.items(), key=lambda kv: -_rank(kv[0]))

    def _group(self, parent, color_map: dict[int, str], nodes: list, style) -> None:
        """Add one separator per color to *parent*: style(color) nodes, then the elements."""
        for color, indices in self._by_color(color_map):
            sep = coin.SoSeparator()
            for node in style(color):
                sep.addChild(node)
            for i in indices:
                sep.addChild(nodes[i])
            parent.addChild(sep)

    def _regroup(self) -> None:
        prefs = _prefs()
        width = prefs["edge_width"]
        rest_t = prefs["rest_transparency"] if self._rest_transparency is None else self._rest_transparency
        xray = (prefs["xray"] if self._xray is None else self._xray) and rest_t <= 0.0 \
            and prefs["xray_strength"] > 0 and self._xray_supported
        faces = {i: c for i, c in self._faces.items() if 0 <= i < len(self._face_nodes)}
        edges = {i: c for i, c in self._edges.items() if 0 <= i < len(self._edge_nodes)}

        for sep in (self._rest_faces, self._xray_faces, self._flagged_faces, self._rest_lines,
                    self._inner_lines, self._outline_lines, self._flagged_lines, self._xray_lines):
            sep.removeAllChildren()

        # Faces -----------------------------------------------------------
        self._rest_faces.addChild(self._material(self._rest_color, transparency=rest_t, shiny=True))
        for i, node in enumerate(self._face_nodes):
            if i not in faces:
                self._rest_faces.addChild(node)
        self._group(self._flagged_faces, faces, self._face_nodes,
                    lambda c: [self._material(hex_to_rgb(c), glow=0.08, shiny=True)])

        # Edges -----------------------------------------------------------
        # Edges of flagged faces: a strong outline where the region ends (next
        # to a face of another color, or no face), a thin line inside it.
        outline: dict[int, str] = {}
        inner: dict[int, str] = {}
        for e, around in enumerate(self._edge_faces):
            if e in edges:
                continue  # flagged edges get their own, stronger style
            colors = [faces[f] for f in around if f in faces]
            if not colors:
                continue
            worst = min(colors, key=_rank)
            if len(around) < 2 or any(faces.get(f) != worst for f in around):
                outline[e] = worst
            else:
                inner[e] = worst

        self._rest_lines.addChild(self._material(REST_EDGE_COLOR))
        self._rest_lines.addChild(self._line_style(1.0))
        for i, node in enumerate(self._edge_nodes):
            if i not in outline and i not in inner and i not in edges:
                self._rest_lines.addChild(node)
        self._group(self._inner_lines, inner, self._edge_nodes,
                    lambda c: [self._material(_scale(hex_to_rgb(c), 0.62)), self._line_style(1.0)])
        self._group(self._outline_lines, outline, self._edge_nodes,
                    lambda c: [self._material(_scale(hex_to_rgb(c), 0.5)), self._line_style(2.0)])
        for color, indices in self._by_color(edges):
            rgb = hex_to_rgb(color)
            for style in ([self._material(_scale(rgb, 0.3)), self._line_style(width + 3)],
                          [self._material(rgb), self._line_style(width)]):
                sep = coin.SoSeparator()
                for node in style:
                    sep.addChild(node)
                for i in indices:
                    sep.addChild(self._edge_nodes[i])
                self._flagged_lines.addChild(sep)

        # Hidden findings -------------------------------------------------
        few = len(faces) <= max(XRAY_MIN_FACES, XRAY_MAX_SHARE * len(self._face_nodes))
        show = xray and few and bool(faces or edges)
        tint = show and self._tint_supported
        self._xray_faces_switch.whichChild = _SWITCH_ALL if tint else _SWITCH_NONE
        self._xray_lines_switch.whichChild = _SWITCH_ALL if show else _SWITCH_NONE
        if tint:
            # A faint wash: the strength preference at 35 % gives about 16 %.
            fade = 1.0 - prefs["xray_strength"] * 0.45
            self._group(self._xray_faces, faces, self._face_nodes,
                        lambda c: [self._material(hex_to_rgb(c), transparency=fade)])
        if show:
            self._group(self._xray_lines, outline, self._edge_nodes,
                        lambda c: [self._material(hex_to_rgb(c)), self._line_style(1.5, _DASHED)])
            self._group(self._xray_lines, edges, self._edge_nodes,
                        lambda c: [self._material(hex_to_rgb(c)), self._line_style(max(width - 1, 2), _DASHED)])
