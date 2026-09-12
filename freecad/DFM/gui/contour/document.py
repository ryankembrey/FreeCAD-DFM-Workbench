# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


import math

from pivy import coin
from PySide6 import QtCore, QtGui, QtWidgets

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore


_BAND_STEPS = {
    "Smooth": 0.0,
    "1 unit bands": 1.0,
    "2 unit bands": 2.0,
    "5 unit bands": 5.0,
    "10 unit bands": 10.0,
}

_MEASURE_TITLES = {
    "draft": ("Draft Analysis", ":/icons/dfm_draft_contour.svg"),
    "thickness": ("Thickness Analysis", ":/icons/dfm_draft_contour.svg"),
}

LABEL_FONT_POINT_SIZE = 9.5
LABEL_PADDING_X = 7.0
LABEL_PADDING_Y = 3.0
LABEL_CORNER_RADIUS = 4.0
LABEL_MARGIN = 2.0

# A plain crosshair, echoing the "+" marker glyph probes actually use in the
# 3D view, so the tree icon reads as "a probe point" rather than the default
# generic scripted-object icon. Defined inline as XPM so it doesn't need a
# new file added to the .qrc / DFM_rc.py resource pipeline.
_PROBE_ICON_XPM = """/* XPM */
static char * dfm_probe_xpm[] = {
"16 16 2 1",
"  c None",
"+ c #2ECC71",
"       ++       ",
"       ++       ",
"       ++       ",
"       ++       ",
"       ++       ",
"       ++       ",
"       ++       ",
"++++++++++++++++",
"++++++++++++++++",
"       ++       ",
"       ++       ",
"       ++       ",
"       ++       ",
"       ++       ",
"       ++       ",
"       ++       "};
"""


def _band_step(name):
    return _BAND_STEPS.get(name, 0.0)


class ContourAnalysisFeature:
    """Proxy holding the parameters and computed field of a saved analysis."""

    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyLinkGlobal", "Source", "DFM", "Analyzed object")
        obj.addProperty("App::PropertyString", "Measure", "DFM", "Measure id")
        obj.addProperty("App::PropertyVector", "PullDirection", "DFM", "Pull direction")
        obj.addProperty("App::PropertyString", "PullReference", "DFM", "Pull reference name")
        obj.addProperty("App::PropertyString", "Resolution", "DFM", "Resolution preset")
        obj.addProperty("App::PropertyFloat", "ElementSize", "DFM", "Mesh element size (mm)")
        obj.addProperty("App::PropertyString", "ColorMap", "DFM", "Color map name")
        obj.addProperty("App::PropertyFloat", "RangeLow", "DFM", "Color range low")
        obj.addProperty("App::PropertyFloat", "RangeHigh", "DFM", "Color range high")
        obj.addProperty("App::PropertyString", "Bands", "DFM", "Banding mode")
        obj.addProperty("App::PropertyBool", "Smooth", "DFM", "Smooth (blended) shading")
        obj.addProperty("App::PropertyPythonObject", "Options", "DFM", "Measure options")
        obj.addProperty("App::PropertyPythonObject", "FieldData", "DFM", "Computed field")
        obj.Options = {}
        obj.FieldData = None

    def store(self, obj, params, field):
        obj.Source = params.get("source")
        obj.Measure = params.get("measure", "")
        pull = params.get("pull_direction")
        if pull is not None:
            obj.PullDirection = App.Vector(*pull)
        obj.PullReference = params.get("pull_reference", "")
        obj.Resolution = params.get("resolution", "")
        obj.ElementSize = float(params.get("element_size") or 0.0)
        obj.ColorMap = params.get("colormap", "")
        obj.RangeLow = float(params.get("range_low", 0.0))
        obj.RangeHigh = float(params.get("range_high", 0.0))
        obj.Bands = params.get("bands", "Smooth")
        obj.Smooth = bool(params.get("smooth", False))
        obj.Options = dict(params.get("options", {}))
        if field is not None:
            obj.FieldData = field

    def execute(self, obj):
        # Nothing to draw persistently; the contour is shown by the task panel
        # (preview) when the object is double-clicked.
        pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def _probe_children(analysis_obj):
    """Every ContourProbeFeature whose Parent points at analysis_obj.

    Probes link to their parent rather than the parent holding a list of
    probes, so deleting one probe never leaves a dangling reference on the
    analysis object for anything to clean up, and deleting the analysis just
    means finding and removing whichever probes still point at it.
    """
    doc = getattr(analysis_obj, "Document", None)
    if doc is None or analysis_obj is None:
        return []
    return [o for o in doc.Objects if getattr(o, "Parent", None) == analysis_obj]


class ContourAnalysisViewProvider:
    """View provider for a saved analysis. It draws nothing itself; double-click
    opens the task panel, which shows the contour and its probes as a preview."""

    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def claimChildren(self):
        """Shows each probe nested under its analysis in the tree."""
        return _probe_children(getattr(self, "Object", None))

    def getDisplayModes(self, vobj):
        return ["Contour"]

    def getDefaultDisplayMode(self):
        return "Contour"

    def setDisplayMode(self, mode):
        return mode

    def doubleClicked(self, vobj):
        open_panel_for(vobj.Object)
        return True

    def setEdit(self, vobj, mode=0):
        open_panel_for(vobj.Object)
        return True

    def onDelete(self, vobj, subelements):
        # Probes link to us, not the other way round, so deleting us first
        # would just orphan them; remove them first instead.
        doc = vobj.Object.Document
        for child in _probe_children(vobj.Object):
            try:
                doc.removeObject(child.Name)
            except Exception:
                pass
        return True

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def create_or_update_analysis(obj, params, field):
    """Create a new analysis object, or update an existing one. It stores the
    analysis; the contour itself is shown by the task panel on double-click."""
    doc = App.ActiveDocument
    if doc is None:
        raise RuntimeError("No active document to save into.")
    if obj is None:
        title = _MEASURE_TITLES.get(params.get("measure", ""), ("Analysis", ""))[0]
        name = title.replace(" ", "")
        obj = doc.addObject("App::FeaturePython", name)
        ContourAnalysisFeature(obj)
        obj.Label = title
        obj.Proxy.store(obj, params, field)
        if App.GuiUp and obj.ViewObject is not None:
            ContourAnalysisViewProvider(obj.ViewObject)
    else:
        obj.Proxy.store(obj, params, field)
    obj.touch()
    doc.recompute()
    return obj


def open_panel_for(obj):
    """Reopen the task panel bound to a saved analysis object."""
    from .panel import ContourTaskPanel
    from ...app.contour.measures import DraftMeasure, ThicknessMeasure

    measure_id = getattr(obj, "Measure", "draft")
    measure = ThicknessMeasure() if measure_id == "thickness" else DraftMeasure()
    title, icon = _MEASURE_TITLES.get(measure_id, ("Analysis", ":/icons/dfm_draft_contour.svg"))
    Gui.Control.showDialog(ContourTaskPanel(measure, title, icon, analysis_obj=obj))


# ---------------------------------------------------------------------------
# Probes
#
# Each probe is a real child document object (not a raw Coin overlay), so the
# tree, F2 rename, Delete, and tree<->3D selection sync all come from FreeCAD
# itself rather than from custom bookkeeping. The marker is wrapped in an
# SoFCSelection node, which is what gives it FreeCAD's own pre-select/select
# highlight colors automatically; the floating text badge is a plain painted
# image alongside it and isn't recolored by selection, just repainted when
# DisplayText (its viewport text, distinct from the tree's Label) changes.
# ---------------------------------------------------------------------------


class ContourProbeFeature:
    """A single probed point and value, saved as a child of its analysis
    (see ContourAnalysisViewProvider.claimChildren).

    Label (the tree name, "Probe 3 - 10mm") and DisplayText (what's painted
    on the floating badge in the 3D view, defaulting to "10 mm") are
    deliberately separate properties: renaming the tree item is bookkeeping,
    but the viewport text is something the user is expected to overwrite
    with a free-form note ("rib base"), and FormattedValue keeps the exact
    measured reading available for Copy Value regardless of what the
    viewport text has been changed to.
    """

    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyLink", "Parent", "DFM", "Owning analysis")
        obj.addProperty("App::PropertyVector", "Position", "DFM", "Probed point")
        obj.addProperty("App::PropertyFloat", "Value", "DFM", "Measured value")
        obj.addProperty("App::PropertyString", "Unit", "DFM", "Measurement unit")
        obj.addProperty(
            "App::PropertyString", "FormattedValue", "DFM", "Value as shown when probed"
        )
        obj.addProperty(
            "App::PropertyString",
            "DisplayText",
            "DFM",
            "Text shown on the probe in the 3D view",
        )

    def execute(self, obj):
        pass

    def onChanged(self, obj, prop):
        # Editing DisplayText (property panel, or the viewport's inline
        # editor, which also just sets this) should repaint the floating
        # badge to match. Both are display-only, so the view provider does
        # the actual work.
        if prop not in ("DisplayText", "Position"):
            return
        vp = getattr(obj, "ViewObject", None)
        proxy = getattr(vp, "Proxy", None) if vp is not None else None
        if proxy is not None and hasattr(proxy, "refresh"):
            try:
                proxy.refresh()
            except Exception:
                pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def default_probe_label(index, value, unit):
    """'Probe 3 - 10mm' style default for the tree label, used when a probe
    is first placed. This is Label, not DisplayText -- renaming this only
    changes what shows in the tree."""
    return f"Probe {index} - {value:g}{unit}"


def default_display_text(value, unit):
    """'10 mm' style default for the floating viewport badge (DisplayText).
    Editing this later, from the property panel or by double-clicking the
    probe in the 3D view, only changes what's painted there, never the
    tree label."""
    return f"{value:g} {unit}"


def _device_pixel_ratio() -> float:
    try:
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen:
            return max(1.0, float(screen.devicePixelRatio()))
    except Exception:
        pass
    return 1.0


def _paint_badge_text(text: str, dpr: float):
    """Render just the badge text (white, bold, centered) onto a fully
    transparent image. The rounded background is separate plain geometry
    now (see ContourProbeViewProvider), so FreeCAD's native selection
    highlight can recolor it the same way it recolors the '+' marker --
    a texture would have multiplied that highlight into near-invisibility,
    which was the whole reason the badge never changed color on hover.
    Returns (QImage, logical_w, logical_h)."""
    font = QtGui.QFont()
    font.setPointSizeF(LABEL_FONT_POINT_SIZE)
    font.setBold(True)

    metrics = QtGui.QFontMetricsF(font)
    badge_w = metrics.horizontalAdvance(text) + (LABEL_PADDING_X * 2.0)
    badge_h = metrics.height() + (LABEL_PADDING_Y * 2.0)

    image = QtGui.QImage(
        int(math.ceil(badge_w * dpr)),
        int(math.ceil(badge_h * dpr)),
        QtGui.QImage.Format.Format_RGBA8888,
    )
    image.setDevicePixelRatio(dpr)
    image.fill(QtCore.Qt.GlobalColor.transparent)

    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
    painter.setPen(QtGui.QColor(255, 255, 255))
    painter.setFont(font)
    painter.drawText(
        QtCore.QRectF(0.0, 0.0, badge_w, badge_h),
        QtCore.Qt.AlignmentFlag.AlignCenter,
        text,
    )
    painter.end()

    return image, badge_w, badge_h


def _rounded_rect_loop(width, height, radius, right, up, segments=4):
    """A closed ring of 3D points tracing a rounded rectangle in the plane
    spanned by the `right` and `up` unit vectors, centered on the origin.
    Used as the vertex loop of the badge's plain-colored background
    SoFaceSet, so the background is a real filled shape FreeCAD's selection
    highlight can recolor natively. `segments` is the number of steps per
    rounded corner."""
    hw = width * 0.5
    hh = height * 0.5
    r = max(0.0, min(radius, hw, hh))

    def pt(x, y):
        return (
            right[0] * x + up[0] * y,
            right[1] * x + up[1] * y,
            right[2] * x + up[2] * y,
        )

    # Corner centers, and the start/sweep angles that trace them CCW.
    corners = [
        ((hw - r, hh - r), 0.0),            # top-right
        ((-hw + r, hh - r), math.pi / 2),   # top-left
        ((-hw + r, -hh + r), math.pi),      # bottom-left
        ((hw - r, -hh + r), 3 * math.pi / 2),  # bottom-right
    ]
    loop = []
    for (cx, cy), a0 in corners:
        for s in range(segments + 1):
            a = a0 + (math.pi / 2) * (s / segments)
            loop.append(pt(cx + r * math.cos(a), cy + r * math.sin(a)))
    return loop


def _project_to_screen(view, point):
    """3D world point -> (x, y) pixel coordinates in the view widget, or None
    if the camera or viewport size isn't available.

    Goes through the view volume's combined view+projection matrix and a
    manual perspective divide (SbMatrix.multVecMatrix, one of the most
    fundamental, always-bound methods in Coin -- used throughout FreeCAD's
    own Python code for exactly this kind of point transform), rather than
    SbViewVolume.projectToScreen. That convenience method was silently
    failing on every single call (confirmed via debug output), the same
    class of problem as coin.SoFCSelection not being a real static
    attribute: something about how pivy exposes it doesn't match the
    calling convention assumed here previously.

    pivy's multVecMatrix returns the transformed point directly rather
    than taking an output parameter to mutate (confirmed via the actual
    TypeError this raised when called the other way) -- the C++ signature
    takes an out-param by reference, but the SWIG binding turns that into
    a return value instead, which is a common pattern for exactly this
    kind of in/out parameter.

    getMatrix() lands in normalized device coordinates in the range
    -1..+1 (not projectToScreen's documented 0..1), so that's remapped to
    0..1 before flipping Y to match Qt's top-left origin (Coin/OpenGL use
    bottom-left).
    """
    try:
        camera = view.getCameraNode()
        size = view.getSize()
        if not camera or not size or size[0] <= 0 or size[1] <= 0:
            return None
        aspect = float(size[0]) / float(size[1])
        volume = camera.getViewVolume(aspect)
        matrix = volume.getMatrix()
        dst = matrix.multVecMatrix(coin.SbVec3f(point.x, point.y, point.z))
        nx, ny, _nz = dst.getValue()
        nx01 = (nx + 1.0) * 0.5
        ny01 = (ny + 1.0) * 0.5
        return nx01 * size[0], (1.0 - ny01) * size[1]
    except Exception:
        return None


def _create_fc_selection_node():
    """Instantiates FreeCAD's SoFCSelection node.

    It's a Coin node type FreeCAD's Gui module registers at runtime, not one
    pivy ships a static Python class for -- coin.SoFCSelection does not
    exist as an attribute, even though the type is fully usable once found.
    Looking it up by name through Coin's type registry and instantiating
    from there is the standard way to reach it. Returns None if the type
    can't be found, so callers can fall back to a plain node instead of
    crashing on a build where this doesn't hold.
    """
    try:
        so_type = coin.SoType.fromName("SoFCSelection")
        if so_type.isBad():
            return None
        return so_type.createInstance()
    except Exception:
        return None


class ContourProbeViewProvider:
    """Renders a probe as a small marker plus a floating text badge. The
    marker is wrapped in SoFCSelection so hovering/selecting it, from the
    tree or the 3D view, uses FreeCAD's own pre-select/select colors
    automatically; there's no custom color handling to keep in sync here."""

    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

        # VERY IMPORTANT: We use SoAnnotation instead of SoSeparator!
        # SoAnnotation forces the rendering engine to place this object into a
        # delayed queue, rendering it *after* all other opaque geometry (like the mesh).
        # This absolutely guarantees the crosshair is drawn on top and is never hidden!
        root = coin.SoAnnotation()

        lm = coin.SoLightModel()
        lm.model.setValue(coin.SoLightModel.BASE_COLOR)
        root.addChild(lm)

        db = coin.SoDepthBuffer()
        db.test.setValue(False)
        root.addChild(db)

        # Base material: Placed OUTSIDE SoFCSelection.
        # This provides the default white color, but allows FreeCAD's selection
        # logic (which injects its own material inside SoFCSelection) to override it!
        base_mat = coin.SoMaterial()
        base_mat.diffuseColor.setValue(1.0, 1.0, 1.0)
        root.addChild(base_mat)

        self.selection = _create_fc_selection_node()
        if self.selection is not None:
            try:
                self.selection.documentName.setValue(vobj.Object.Document.Name)
                self.selection.objectName.setValue(vobj.Object.Name)
                self.selection.subElementName.setValue("")
            except Exception:
                self.selection = None
        if self.selection is None:
            App.Console.PrintWarning(
                "DFM contour: SoFCSelection unavailable; probes will still work "
                "but won't get FreeCAD's native selection highlight.\n"
            )
            self.selection = coin.SoSeparator()
        root.addChild(self.selection)

        # Base translation applies to EVERYTHING inside the selection
        self.base_trans = coin.SoTransform()
        self.selection.addChild(self.base_trans)

        # 1. The visible '+' Crosshair (Real 3D lines, guarantees rendering and highlighting)
        self.cross_coords = coin.SoCoordinate3()
        self.selection.addChild(self.cross_coords)

        cross_draw = coin.SoDrawStyle()
        cross_draw.lineWidth.setValue(3.0)
        self.selection.addChild(cross_draw)

        cross_lines = coin.SoLineSet()
        cross_lines.numVertices.setValues(0, 2, [2, 2])
        self.selection.addChild(cross_lines)

        # 2. Pick Sphere (Invisible area that makes hovering and clicking easy)
        pick_sep = coin.SoSeparator()

        pick_draw = coin.SoDrawStyle()
        pick_draw.style.setValue(coin.SoDrawStyle.INVISIBLE)
        pick_sep.addChild(pick_draw)

        pick_style = coin.SoPickStyle()
        pick_style.style.setValue(coin.SoPickStyle.SHAPE)
        pick_sep.addChild(pick_style)

        self.pick_sphere = coin.SoSphere()
        pick_sep.addChild(self.pick_sphere)
        self.selection.addChild(pick_sep)

        # 3. Floating Text Badge, built as two layers so FreeCAD's native
        #    selection highlight colors it the same way it colors the '+':
        #
        #    (a) a plain-colored rounded background face -- NO texture, so the
        #        highlight's material override actually takes effect on it
        #        (a texture would multiply the override into near-invisibility,
        #        which is exactly why the old single-textured badge never
        #        changed color on hover/select);
        #    (b) the text as a transparent-background texture drawn on top,
        #        shifted a hair toward the camera so it sits over the
        #        background. The text is white and never needs to change on
        #        highlight, so it stays a texture.
        label_sep = coin.SoSeparator()
        self.label_trans = coin.SoTransform()
        label_sep.addChild(self.label_trans)

        # (a) Background: default off-white base color, recolored natively by
        #     SoFCSelection on preselect/select. Sits inside self.selection.
        bg_mat = coin.SoMaterial()
        bg_mat.diffuseColor.setValue(0.15, 0.15, 0.15)
        bg_mat.emissiveColor.setValue(0.15, 0.15, 0.15)
        label_sep.addChild(bg_mat)

        self.badge_bg_coords = coin.SoCoordinate3()
        label_sep.addChild(self.badge_bg_coords)
        self.badge_bg_face = coin.SoFaceSet()
        label_sep.addChild(self.badge_bg_face)

        # (b) Text overlay: a separate sub-separator so its texture and
        #     material don't leak onto the background face above.
        text_sep = coin.SoSeparator()
        self.badge_text_trans = coin.SoTransform()
        text_sep.addChild(self.badge_text_trans)

        text_mat = coin.SoMaterial()
        text_mat.diffuseColor.setValue(1.0, 1.0, 1.0)
        text_mat.emissiveColor.setValue(1.0, 1.0, 1.0)
        text_sep.addChild(text_mat)

        # Don't let the text quad participate in picking -- the background
        # face and pick sphere already cover the probe's clickable area, and
        # a transparent texture shouldn't intercept rays.
        text_pick = coin.SoPickStyle()
        text_pick.style.setValue(coin.SoPickStyle.UNPICKABLE)
        text_sep.addChild(text_pick)

        self.badge_texture = coin.SoTexture2()
        # Modulate so the texture's alpha cuts out the text shape; the white
        # material shows through where the glyphs are.
        self.badge_texture.model.setValue(coin.SoTexture2.MODULATE)
        text_sep.addChild(self.badge_texture)

        badge_texcoords = coin.SoTextureCoordinate2()
        badge_texcoords.point.setValues(0, 4, [(0, 0), (1, 0), (1, 1), (0, 1)])
        text_sep.addChild(badge_texcoords)

        self.badge_text_coords = coin.SoCoordinate3()
        text_sep.addChild(self.badge_text_coords)
        badge_text_face = coin.SoFaceSet()
        badge_text_face.numVertices.setValue(4)
        text_sep.addChild(badge_text_face)

        label_sep.addChild(text_sep)
        self.selection.addChild(label_sep)

        self.camera_sensor = None
        self.active_camera = None
        self.world_per_px = 0.0
        self.viewport_height_px = 0

        vobj.addDisplayMode(root, "Standard")
        try:
            vobj.DisplayMode = "Standard"
        except Exception:
            pass
        self.refresh()

    def getIcon(self):
        return _PROBE_ICON_XPM

    def getDisplayModes(self, vobj):
        return ["Standard"]

    def getDefaultDisplayMode(self):
        return "Standard"

    def setDisplayMode(self, mode):
        return mode

    def updateData(self, obj, prop):
        if prop == "Position":
            self.refresh()

    def refresh(self):
        """Repaint the text texture and reposition everything from the
        current Position/DisplayText. No selection state here anymore --
        FreeCAD's native SoFCSelection highlight recolors the background
        face directly. Runs only on edit/reposition, so repainting the
        text image each time is cheap enough not to cache."""
        obj = self.Object
        pos = getattr(obj, "Position", App.Vector(0, 0, 0))
        self.base_trans.translation.setValue(pos.x, pos.y, pos.z)
        try:
            dpr = _device_pixel_ratio()
            image, logical_w, logical_h = _paint_badge_text(
                getattr(obj, "DisplayText", "") or "", dpr
            )
            flipped = image.mirrored(False, True).convertToFormat(
                QtGui.QImage.Format.Format_RGBA8888
            )
            buffer = bytes(flipped.constBits())[: flipped.sizeInBytes()]

            self.badge_texture.image.setValue(
                coin.SbVec2s(flipped.width(), flipped.height()), 4, buffer
            )
            self.logical_w = logical_w
            self.logical_h = logical_h
        except Exception:
            pass
        self._update_scale()

    def _view(self):
        gui_doc = Gui.ActiveDocument
        if gui_doc is None:
            return None
        return gui_doc.ActiveView

    def _attach_camera_sensor(self):
        view = self._view()
        if view is None:
            return
        camera = view.getCameraNode()
        if not camera:
            return
        if self.camera_sensor is None:
            self.camera_sensor = coin.SoNodeSensor(self._camera_changed, None)
        if self.active_camera != camera:
            if self.active_camera is not None:
                self.camera_sensor.detach()
            self.active_camera = camera
            self.camera_sensor.attach(self.active_camera)
        try:
            size = view.getSize()
            if size and len(size) >= 2 and size[1] > 0:
                self.viewport_height_px = int(size[1])
        except Exception:
            pass

    def _camera_changed(self, userdata, sensor):
        self._update_scale()

    def _update_scale(self):
        self._attach_camera_sensor()
        if not self.active_camera:
            return
        pos = getattr(self.Object, "Position", App.Vector(0, 0, 0))
        h = 0.0
        if self.active_camera.isOfType(coin.SoOrthographicCamera.getClassTypeId()):
            h = self.active_camera.height.getValue()
        elif self.active_camera.isOfType(coin.SoPerspectiveCamera.getClassTypeId()):
            cam_pos = self.active_camera.position.getValue()
            dx = cam_pos[0] - pos.x
            dy = cam_pos[1] - pos.y
            dz = cam_pos[2] - pos.z
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            angle = self.active_camera.heightAngle.getValue()
            h = 2.0 * dist * math.tan(angle / 2.0)

        if self.viewport_height_px > 0:
            self.world_per_px = h / float(self.viewport_height_px)

        wpx = self.world_per_px
        if wpx <= 0:
            return

        try:
            # Face the camera vectors
            cam_rot = self.active_camera.orientation.getValue()
            right = cam_rot.multVec(coin.SbVec3f(1, 0, 0)).getValue()
            up = cam_rot.multVec(coin.SbVec3f(0, 1, 0)).getValue()
            towards_cam = cam_rot.multVec(coin.SbVec3f(0, 0, 1)).getValue()

            # The probe assembly sits at its true position. It's drawn on
            # top of the mesh by SoAnnotation (delayed render pass) with
            # depth-testing disabled, and picked on top by the same virtue,
            # so no toward-camera shift is needed. An earlier version pulled
            # it toward the camera to try to win depth-based ray picks; that
            # displaced the geometry from its real location and is exactly
            # what the current SoRayPickAction-based detection makes both
            # unnecessary and undesirable (a shifted probe would pick and
            # sit at the wrong depth).
            self.base_trans.translation.setValue(pos.x, pos.y, pos.z)

            # Draw a prominent + icon matching standard FreeCAD probes (8px radius lines)
            # Calculated purely in world space so we don't have to use bug-prone SoRotation
            cs = 8.0 * wpx
            if hasattr(self, "cross_coords"):
                self.cross_coords.point.setValues(
                    0,
                    4,
                    [
                        (-right[0] * cs, -right[1] * cs, -right[2] * cs),
                        (right[0] * cs, right[1] * cs, right[2] * cs),
                        (-up[0] * cs, -up[1] * cs, -up[2] * cs),
                        (up[0] * cs, up[1] * cs, up[2] * cs),
                    ],
                )

            # Dynamic pick sphere sized perfectly to a 24px invisible target area
            if hasattr(self, "pick_sphere"):
                self.pick_sphere.radius.setValue(max(1e-4, 20.0 * wpx))

            # Keep the badge cleanly offset top-right of the anchor
            offset = 12.0 * wpx
            if hasattr(self, "label_trans"):
                self.label_trans.translation.setValue(
                    (right[0] + up[0]) * offset,
                    (right[1] + up[1]) * offset,
                    (right[2] + up[2]) * offset,
                )

            if hasattr(self, "badge_bg_coords") and getattr(self, "logical_w", 0) > 0:
                lw = self.logical_w * wpx  # text width in world units
                lh = self.logical_h * wpx  # text height in world units
                pad = LABEL_PADDING_X * wpx  # background padding around text

                # Text quad: SoImage(HALF, LEFT)-style alignment -- starts at
                # the anchor (X=0), vertically centered.
                t0 = (-up[0] * lh * 0.5, -up[1] * lh * 0.5, -up[2] * lh * 0.5)
                t1 = (t0[0] + right[0] * lw, t0[1] + right[1] * lw, t0[2] + right[2] * lw)
                t2 = (t1[0] + up[0] * lh, t1[1] + up[1] * lh, t1[2] + up[2] * lh)
                t3 = (t2[0] - right[0] * lw, t2[1] - right[1] * lw, t2[2] - right[2] * lw)
                if hasattr(self, "badge_text_coords"):
                    self.badge_text_coords.point.setValues(0, 4, [t0, t1, t2, t3])

                # Background rounded rect: text size plus padding, centered on
                # the text's own center so it sits symmetrically behind it.
                bg_w = lw + 2.0 * pad
                bg_h = lh + 2.0 * pad
                center = (
                    right[0] * (lw * 0.5) + up[0] * 0.0,
                    right[1] * (lw * 0.5),
                    right[2] * (lw * 0.5),
                )
                radius = LABEL_CORNER_RADIUS * wpx
                loop = _rounded_rect_loop(bg_w, bg_h, radius, right, up)
                loop = [
                    (p[0] + center[0], p[1] + center[1], p[2] + center[2]) for p in loop
                ]
                self.badge_bg_coords.point.setValues(0, len(loop), loop)
                self.badge_bg_face.numVertices.setValue(len(loop))

                # Nudge the text a touch toward the camera so it renders over
                # the background face (both are in the same annotation with
                # depth test off, so ordering by a small offset keeps the text
                # crisply on top).
                if hasattr(self, "badge_text_trans"):
                    nudge = 0.01 * wpx
                    self.badge_text_trans.translation.setValue(
                        towards_cam[0] * nudge,
                        towards_cam[1] * nudge,
                        towards_cam[2] * nudge,
                    )

        except Exception:
            pass

    def doubleClicked(self, vobj):
        _start_inline_rename(vobj)
        return True

    def setupContextMenu(self, vobj, menu):
        obj = vobj.Object
        act_delete = menu.addAction("Delete Probe")
        act_delete.triggered.connect(lambda: _delete_probe(obj))

        act_delete_all = menu.addAction("Delete All Probes")
        act_delete_all.triggered.connect(lambda: _delete_all_probes(obj))

        menu.addSeparator()

        act_copy = menu.addAction("Copy Value")
        act_copy.triggered.connect(lambda: _copy_probe_value(obj))

    def onDelete(self, vobj, subelements):
        if self.camera_sensor:
            self.camera_sensor.detach()
            self.camera_sensor = None
        return True

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def _delete_probe(obj):
    doc = obj.Document
    try:
        doc.removeObject(obj.Name)
    except Exception as exc:
        App.Console.PrintError(f"DFM contour: could not delete probe. {exc}\n")


def _delete_all_probes(obj):
    doc = obj.Document
    parent = getattr(obj, "Parent", None)
    siblings = _probe_children(parent) if parent is not None else [obj]
    for sibling in siblings:
        try:
            doc.removeObject(sibling.Name)
        except Exception:
            pass


def _copy_probe_value(obj):
    text = getattr(obj, "FormattedValue", "") or f"{obj.Value:g}{obj.Unit}"
    QtWidgets.QApplication.clipboard().setText(text)


class _InlineRenameEdit(QtWidgets.QLineEdit):
    """A QLineEdit that reports Escape separately, instead of treating it as
    an ordinary key, so the caller can cancel rather than commit."""

    def __init__(self, parent, on_cancel):
        super().__init__(parent)
        self._on_cancel = on_cancel

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(event)


def _start_inline_rename(vobj):
    """Floating line edit over the probe's screen position, so its viewport
    text (DisplayText) can be edited without switching to the tree or the
    property panel. Commits on Enter or focus loss, Escape cancels. This
    never touches Label -- the tree name is a separate, unrelated property."""
    obj = vobj.Object
    gui_doc = Gui.ActiveDocument
    if gui_doc is None or gui_doc.ActiveView is None:
        return
    view = gui_doc.ActiveView

    mw = Gui.getMainWindow()
    mdi = mw.findChild(QtWidgets.QMdiArea)
    if mdi is None:
        return
    sub = mdi.activeSubWindow() or mdi.currentSubWindow()
    parent_widget = sub.widget() if sub is not None else None
    if parent_widget is None:
        return

    state = {"done": False}
    current_text = getattr(obj, "DisplayText", "") or ""

    def commit():
        if state["done"]:
            return
        state["done"] = True
        text = editor.text().strip()
        if text and text != obj.DisplayText:
            obj.DisplayText = text
        editor.deleteLater()

    def cancel():
        if state["done"]:
            return
        state["done"] = True
        editor.deleteLater()

    editor = _InlineRenameEdit(parent_widget, cancel)
    editor.setText(current_text)
    editor.selectAll()
    editor.setMinimumWidth(140)
    editor.editingFinished.connect(commit)

    screen_pt = _project_to_screen(view, getattr(obj, "Position", App.Vector(0, 0, 0)))
    if screen_pt is not None:
        x, y = screen_pt
        editor.move(max(0, int(x) - 8), max(0, int(y) - 12))

    editor.show()
    editor.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
