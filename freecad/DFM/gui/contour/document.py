# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


from enum import Enum
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


def _band_names():
    return list(_BAND_STEPS.keys())


def _colormap_names():
    from ...app.contour.colormap import COLORMAPS

    return list(COLORMAPS.keys())


def _resolution_names():
    from ...app.contour.meshing import RESOLUTION_DIVISORS

    return list(RESOLUTION_DIVISORS.keys()) + ["Custom"]


def _set_enum(obj, prop, value, choices):
    """Safely set an enumeration property to `value` if it's one of
    `choices`; enumeration assignment raises on an unknown/empty string, so
    unknown values are ignored (the property keeps its current selection)."""
    if value and value in choices:
        try:
            setattr(obj, prop, value)
        except Exception:
            pass


LABEL_FONT_POINT_SIZE = 10.0
LABEL_PADDING_X = 4.0
LABEL_PADDING_Y = 3.0
LABEL_CORNER_RADIUS = 3.0
LABEL_MARGIN = 1.0

_PROBE_CROSS_BASE_COLOR = (1.0, 1.0, 1.0)
_PROBE_BADGE_BASE_COLOR = (20 / 255.0, 20 / 255.0, 22 / 255.0)
_PROBE_HOVER_DEFAULT = (1.0, 0.6, 0.0)  # FreeCAD colorHighlight default
_PROBE_SELECT_DEFAULT = (0.1, 0.8, 0.1)  # FreeCAD colorSelection default


class ProbeState(Enum):
    NONE = 0
    HOVER = 1
    SELECTED = 2


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
        obj.addProperty("App::PropertyEnumeration", "Resolution", "DFM", "Resolution preset")
        obj.addProperty("App::PropertyFloat", "ElementSize", "DFM", "Mesh element size (mm)")
        obj.addProperty("App::PropertyEnumeration", "ColorMap", "DFM", "Color map name")
        obj.addProperty("App::PropertyFloat", "RangeLow", "DFM", "Color range low")
        obj.addProperty("App::PropertyFloat", "RangeHigh", "DFM", "Color range high")
        obj.addProperty("App::PropertyEnumeration", "Bands", "DFM", "Banding mode")
        obj.addProperty("App::PropertyBool", "Smooth", "DFM", "Smooth (blended) shading")
        obj.addProperty("App::PropertyPythonObject", "Options", "DFM", "Measure options")
        obj.addProperty("App::PropertyPythonObject", "FieldData", "DFM", "Computed field")
        obj.ColorMap = _colormap_names()
        obj.Bands = _band_names()
        obj.Resolution = _resolution_names()
        obj.Options = {}
        obj.FieldData = None
        self._init_done = True

    def store(self, obj, params, field):
        self._storing = True
        try:
            obj.Source = params.get("source")
            obj.Measure = params.get("measure", "")
            pull = params.get("pull_direction")
            if pull is not None:
                obj.PullDirection = App.Vector(*pull)
            obj.PullReference = params.get("pull_reference", "")
            _set_enum(obj, "Resolution", params.get("resolution", ""), _resolution_names())
            obj.ElementSize = float(params.get("element_size") or 0.0)
            _set_enum(obj, "ColorMap", params.get("colormap", ""), _colormap_names())
            obj.RangeLow = float(params.get("range_low", 0.0))
            obj.RangeHigh = float(params.get("range_high", 0.0))
            _set_enum(obj, "Bands", params.get("bands", "Smooth"), _band_names())
            obj.Smooth = bool(params.get("smooth", False))
            obj.Options = dict(params.get("options", {}))
            if field is not None:
                obj.FieldData = field
        finally:
            self._storing = False

    def execute(self, obj):
        pass

    def onChanged(self, obj, prop):
        if prop not in ("ColorMap", "Bands", "Smooth", "RangeLow", "RangeHigh"):
            return
        if getattr(self, "_storing", False):
            return
        panel = getattr(self, "_live_panel", None)
        if panel is not None and hasattr(panel, "apply_property_change"):
            try:
                panel.apply_property_change(prop)
            except Exception:
                pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def _probe_children(analysis_obj):
    doc = getattr(analysis_obj, "Document", None)
    if doc is None or analysis_obj is None:
        return []
    return [
        o
        for o in doc.Objects
        if getattr(o, "Parent", None) == analysis_obj and not _is_legend_object(o)
    ]


class ContourAnalysisViewProvider:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def claimChildren(self):
        """Shows each probe and the legend nested under the analysis."""
        children = list(_probe_children(getattr(self, "Object", None)))
        legend = legend_child(getattr(self, "Object", None))
        if legend is not None:
            children.append(legend)
        return children

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
        doc = vobj.Object.Document
        children = list(_probe_children(vobj.Object))
        legend = legend_child(vobj.Object)
        if legend is not None:
            children.append(legend)
        for child in children:
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
    from .panel import ContourTaskPanel
    from ...app.contour.measures import DraftMeasure, ThicknessMeasure

    measure_id = getattr(obj, "Measure", "draft")
    measure = ThicknessMeasure() if measure_id == "thickness" else DraftMeasure()
    title, icon = _MEASURE_TITLES.get(measure_id, ("Analysis", ":/icons/dfm_draft_contour.svg"))
    Gui.Control.showDialog(ContourTaskPanel(measure, title, icon, analysis_obj=obj))


_LEGEND_ORIENTATIONS = ["Horizontal", "Vertical"]


class ContourLegendFeature:
    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyLink", "Parent", "DFM", "Owning analysis")
        obj.addProperty("App::PropertyEnumeration", "Orientation", "DFM", "Legend orientation")
        obj.addProperty(
            "App::PropertyBool",
            "AutoTextColor",
            "DFM",
            "Pick legend text color automatically from the viewport background",
        )
        obj.addProperty(
            "App::PropertyColor", "TextColor", "DFM", "Legend text color (when not automatic)"
        )
        obj.Orientation = list(_LEGEND_ORIENTATIONS)
        obj.AutoTextColor = True
        obj.TextColor = auto_legend_text_rgb()
        self._init_done = True

    def execute(self, obj):
        pass

    def onChanged(self, obj, prop):
        if (
            prop == "TextColor"
            and getattr(self, "_init_done", False)
            and not getattr(self, "_setting_auto_flag", False)
        ):
            try:
                if getattr(obj, "AutoTextColor", False):
                    self._setting_auto_flag = True
                    obj.AutoTextColor = False
                    self._setting_auto_flag = False
            except Exception:
                self._setting_auto_flag = False

        if prop not in ("Orientation", "TextColor", "AutoTextColor"):
            return
        panel = self._live_panel(obj)
        if panel is None:
            return
        try:
            if prop == "Orientation" and hasattr(panel, "apply_legend_orientation"):
                panel.apply_legend_orientation(obj.Orientation == "Vertical")
            elif prop in ("TextColor", "AutoTextColor") and hasattr(
                panel, "apply_legend_text_color"
            ):
                panel.apply_legend_text_color()
        except Exception:
            pass

    @staticmethod
    def _live_panel(obj):
        parent = getattr(obj, "Parent", None)
        proxy = getattr(parent, "Proxy", None) if parent is not None else None
        return getattr(proxy, "_live_panel", None) if proxy is not None else None

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class ContourLegendViewProvider:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object
        try:
            vobj.addDisplayMode(coin.SoSeparator(), "Legend")
        except Exception:
            pass

    def getDisplayModes(self, vobj):
        return ["Legend"]

    def getDefaultDisplayMode(self):
        return "Legend"

    def setDisplayMode(self, mode):
        return mode

    def onChanged(self, vobj, prop):
        if prop == "Visibility":
            panel = ContourLegendFeature._live_panel(vobj.Object)
            if panel is not None and hasattr(panel, "apply_legend_visible"):
                try:
                    panel.apply_legend_visible(bool(vobj.Visibility))
                except Exception:
                    pass

    def onDelete(self, vobj, subelements):
        panel = ContourLegendFeature._live_panel(vobj.Object)
        if panel is not None and hasattr(panel, "on_legend_deleted"):
            try:
                panel.on_legend_deleted()
            except Exception:
                pass
        return True

    def getIcon(self):
        return _LEGEND_ICON_XPM

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def legend_child(analysis_obj):
    """The ContourLegendFeature child of analysis_obj, or None."""
    doc = getattr(analysis_obj, "Document", None)
    if doc is None or analysis_obj is None:
        return None
    for o in doc.Objects:
        if getattr(o, "Parent", None) == analysis_obj and _is_legend_object(o):
            return o
    return None


def _is_legend_object(o):
    proxy = getattr(o, "Proxy", None)
    return proxy is not None and proxy.__class__.__name__ == "ContourLegendFeature"


def ensure_legend_object(analysis_obj, horizontal):
    """Create the legend tree object under analysis_obj if absent, or return
    the existing one. Orientation is initialised from `horizontal`."""
    if analysis_obj is None:
        return None
    existing = legend_child(analysis_obj)
    if existing is not None:
        return existing
    doc = analysis_obj.Document
    obj = doc.addObject("App::FeaturePython", "ContourLegend")
    ContourLegendFeature(obj)
    obj.Parent = analysis_obj
    obj.Label = "Legend"
    obj.Orientation = "Horizontal" if horizontal else "Vertical"
    if App.GuiUp and obj.ViewObject is not None:
        ContourLegendViewProvider(obj.ViewObject)
    return obj


_LEGEND_ICON_XPM = """/* XPM */
static char * dfm_legend_xpm[] = {
"16 16 3 1",
"  c None",
". c #2E86C1",
"+ c #F4D03F",
"                ",
" ..........+++  ",
" ..........+++  ",
"                ",
" ..........+++  ",
" ..........+++  ",
"                ",
" ..........+++  ",
" ..........+++  ",
"                ",
" ..........+++  ",
" ..........+++  ",
"                ",
" ..........+++  ",
" ..........+++  ",
"                "};
"""


class ContourProbeFeature:
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
    return f"Probe {index} - {value:.1f}{unit}"


def default_display_text(value, unit):
    return f"{value:.1f} {unit}"


def _device_pixel_ratio() -> float:
    try:
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen:
            return max(1.0, float(screen.devicePixelRatio()))
    except Exception:
        pass
    return 1.0


def _pref_color(entry: str, default_rgb: tuple) -> tuple:
    try:
        param = App.ParamGet("User parameter:BaseApp/Preferences/View")
        packed = param.GetUnsigned(entry, 0)
        if packed:
            r = ((packed >> 24) & 0xFF) / 255.0
            g = ((packed >> 16) & 0xFF) / 255.0
            b = ((packed >> 8) & 0xFF) / 255.0
            return (r, g, b)
    except Exception:
        pass
    return default_rgb


def _probe_state_color(state: int, base_rgb) -> tuple:
    if state == ProbeState.SELECTED:
        return _pref_color("SelectionColor", _PROBE_SELECT_DEFAULT)
    if state == ProbeState.HOVER:
        return _pref_color("HighlightColor", _PROBE_HOVER_DEFAULT)
    return base_rgb


def _relative_luminance(rgb) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def viewport_background_luminance() -> float:
    try:
        p = App.ParamGet("User parameter:BaseApp/Preferences/View")
    except Exception:
        return 0.5

    def col(entry, default):
        return _pref_color(entry, default)

    try:
        simple = p.GetBool("Simple", False)
        gradient = p.GetBool("Gradient", True)
        use_mid = p.GetBool("UseBackgroundColorMid", False)
    except Exception:
        simple, gradient, use_mid = False, True, False

    if simple or not gradient:
        # Solid background lives in BackgroundColor (col1).
        return _relative_luminance(col("BackgroundColor", (0.9, 0.9, 0.95)))

    # Gradient: average the active stops. Top=BackgroundColor2, bottom=3, mid=4.
    top = col("BackgroundColor2", (0.2, 0.3, 0.5))
    bottom = col("BackgroundColor3", (0.6, 0.6, 0.7))
    stops = [top, bottom]
    if use_mid:
        stops.append(col("BackgroundColor4", (0.4, 0.45, 0.6)))
    return sum(_relative_luminance(s) for s in stops) / len(stops)


def auto_legend_text_rgb() -> tuple:
    """Black text on a light viewport background, white on a dark one."""
    return (0.0, 0.0, 0.0) if viewport_background_luminance() > 0.5 else (1.0, 1.0, 1.0)


def legend_text_rgb(analysis_obj) -> tuple:
    legend = legend_child(analysis_obj)
    try:
        if legend is None or getattr(legend, "AutoTextColor", True):
            return auto_legend_text_rgb()
        c = legend.TextColor
        return (float(c[0]), float(c[1]), float(c[2]))
    except Exception:
        return auto_legend_text_rgb()


def _paint_badge_text(text: str, dpr: float):
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
    hw = width * 0.5
    hh = height * 0.5
    r = max(0.0, min(radius, hw, hh))

    def pt(x, y):
        return (
            right[0] * x + up[0] * y,
            right[1] * x + up[1] * y,
            right[2] * x + up[2] * y,
        )

    corners = [
        ((hw - r, hh - r), 0.0),
        ((-hw + r, hh - r), math.pi / 2),
        ((-hw + r, -hh + r), math.pi),
        ((hw - r, -hh + r), 3 * math.pi / 2),
    ]
    loop = []
    for (cx, cy), a0 in corners:
        for s in range(segments + 1):
            a = a0 + (math.pi / 2) * (s / segments)
            loop.append(pt(cx + r * math.cos(a), cy + r * math.sin(a)))
    return loop


def _project_to_screen(view, point):
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
    try:
        so_type = coin.SoType.fromName("SoFCSelection")
        if so_type.isBad():
            return None
        return so_type.createInstance()
    except Exception:
        return None


class ContourProbeViewProvider:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

        root = coin.SoAnnotation()

        lm = coin.SoLightModel()
        lm.model.setValue(coin.SoLightModel.BASE_COLOR)
        root.addChild(lm)

        db = coin.SoDepthBuffer()
        db.test.setValue(False)
        root.addChild(db)

        base_mat = coin.SoMaterial()
        base_mat.diffuseColor.setValue(1.0, 1.0, 1.0)
        root.addChild(base_mat)

        self.base_trans = coin.SoTransform()
        root.addChild(self.base_trans)

        self.selection = _create_fc_selection_node()
        if self.selection is not None:
            try:
                self.selection.documentName.setValue(vobj.Object.Document.Name)
                self.selection.objectName.setValue(vobj.Object.Name)
                self.selection.subElementName.setValue("")
            except Exception:
                self.selection = None
        if self.selection is None:
            self.selection = coin.SoSeparator()
        root.addChild(self.selection)

        self.cross_mat = coin.SoMaterial()
        self.cross_mat.diffuseColor.setValue(*_PROBE_CROSS_BASE_COLOR)
        self.cross_mat.emissiveColor.setValue(*_PROBE_CROSS_BASE_COLOR)
        self.selection.addChild(self.cross_mat)

        self.cross_coords = coin.SoCoordinate3()
        self.selection.addChild(self.cross_coords)

        cross_draw = coin.SoDrawStyle()
        cross_draw.lineWidth.setValue(3.0)
        self.selection.addChild(cross_draw)

        cross_lines = coin.SoLineSet()
        cross_lines.numVertices.setValues(0, 2, [2, 2])
        self.selection.addChild(cross_lines)

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

        bg_sep = coin.SoSeparator()
        self.label_trans = coin.SoTransform()
        bg_sep.addChild(self.label_trans)

        self.badge_bg_mat = coin.SoMaterial()
        self.badge_bg_mat.diffuseColor.setValue(*_PROBE_BADGE_BASE_COLOR)
        self.badge_bg_mat.emissiveColor.setValue(*_PROBE_BADGE_BASE_COLOR)
        bg_sep.addChild(self.badge_bg_mat)

        self.badge_bg_coords = coin.SoCoordinate3()
        bg_sep.addChild(self.badge_bg_coords)
        self.badge_bg_face = coin.SoFaceSet()
        bg_sep.addChild(self.badge_bg_face)
        self.selection.addChild(bg_sep)

        text_anno = coin.SoAnnotation()

        self.text_label_trans = coin.SoTransform()
        text_anno.addChild(self.text_label_trans)

        text_pick = coin.SoPickStyle()
        text_pick.style.setValue(coin.SoPickStyle.UNPICKABLE)
        text_anno.addChild(text_pick)

        text_mat = coin.SoMaterial()
        text_mat.diffuseColor.setValue(1.0, 1.0, 1.0)
        text_mat.emissiveColor.setValue(1.0, 1.0, 1.0)
        text_anno.addChild(text_mat)

        self.badge_texture = coin.SoTexture2()
        self.badge_texture.model.setValue(coin.SoTexture2.MODULATE)
        text_anno.addChild(self.badge_texture)

        badge_texcoords = coin.SoTextureCoordinate2()
        badge_texcoords.point.setValues(0, 4, [(0, 0), (1, 0), (1, 1), (0, 1)])
        text_anno.addChild(badge_texcoords)

        self.badge_text_coords = coin.SoCoordinate3()
        text_anno.addChild(self.badge_text_coords)
        badge_text_face = coin.SoFaceSet()
        badge_text_face.numVertices.setValue(4)
        text_anno.addChild(badge_text_face)

        root.addChild(text_anno)

        self.camera_sensor = None
        self.active_camera = None
        self.world_per_px = 0.0
        self.viewport_height_px = 0
        self._highlight_state = ProbeState.NONE

        vobj.addDisplayMode(root, "Standard")
        try:
            vobj.DisplayMode = "Standard"
        except Exception:
            pass
        self.refresh()

    def set_highlight_state(self, state):
        if state == self._highlight_state:
            return
        self._highlight_state = state
        for mat, base in (
            (getattr(self, "cross_mat", None), _PROBE_CROSS_BASE_COLOR),
            (getattr(self, "badge_bg_mat", None), _PROBE_BADGE_BASE_COLOR),
        ):
            if mat is None:
                continue
            color = _probe_state_color(state, base)
            try:
                mat.diffuseColor.setValue(*color)
                mat.emissiveColor.setValue(*color)
            except Exception:
                pass

    def _repaint_text(self):
        obj = self.Object
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
        if not self._object_alive():
            return
        obj = self.Object
        pos = getattr(obj, "Position", App.Vector(0, 0, 0))
        self.base_trans.translation.setValue(pos.x, pos.y, pos.z)
        self._repaint_text()
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

    def _object_alive(self):
        try:
            _ = self.Object.Name  # raises ReferenceError if deleted
            return True
        except Exception:
            if getattr(self, "camera_sensor", None) is not None:
                try:
                    self.camera_sensor.detach()
                except Exception:
                    pass
                self.camera_sensor = None
            self.active_camera = None
            return False

    def _camera_changed(self, userdata, sensor):
        if not self._object_alive():
            return
        self._update_scale()

    def _update_scale(self):
        if not self._object_alive():
            return
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
            cam_rot = self.active_camera.orientation.getValue()
            right = cam_rot.multVec(coin.SbVec3f(1, 0, 0)).getValue()
            up = cam_rot.multVec(coin.SbVec3f(0, 1, 0)).getValue()

            self.base_trans.translation.setValue(pos.x, pos.y, pos.z)

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

            if hasattr(self, "pick_sphere"):
                self.pick_sphere.radius.setValue(max(1e-4, 20.0 * wpx))

            offset = 16.0 * wpx
            badge_offset = (
                (right[0] + up[0]) * offset,
                (right[1] + up[1]) * offset,
                (right[2] + up[2]) * offset,
            )
            if hasattr(self, "label_trans"):
                self.label_trans.translation.setValue(*badge_offset)
            if hasattr(self, "text_label_trans"):
                self.text_label_trans.translation.setValue(*badge_offset)

            if hasattr(self, "badge_text_coords") and getattr(self, "logical_w", 0) > 0:
                lw = self.logical_w * wpx
                lh = self.logical_h * wpx
                t0 = (-up[0] * lh * 0.5, -up[1] * lh * 0.5, -up[2] * lh * 0.5)
                t1 = (t0[0] + right[0] * lw, t0[1] + right[1] * lw, t0[2] + right[2] * lw)
                t2 = (t1[0] + up[0] * lh, t1[1] + up[1] * lh, t1[2] + up[2] * lh)
                t3 = (t2[0] - right[0] * lw, t2[1] - right[1] * lw, t2[2] - right[2] * lw)
                self.badge_text_coords.point.setValues(0, 4, [t0, t1, t2, t3])

                pad = LABEL_PADDING_X * wpx
                bg_w = lw + 2.0 * pad
                bg_h = lh + 2.0 * pad
                cx = right[0] * (lw * 0.5), right[1] * (lw * 0.5), right[2] * (lw * 0.5)
                radius = LABEL_CORNER_RADIUS * wpx
                loop = _rounded_rect_loop(bg_w, bg_h, radius, right, up)
                loop = [(p[0] + cx[0], p[1] + cx[1], p[2] + cx[2]) for p in loop]
                self.badge_bg_coords.point.setValues(0, len(loop), loop)
                self.badge_bg_face.numVertices.setValue(len(loop))

        except Exception:
            pass

    def doubleClicked(self, vobj):
        _start_inline_rename(vobj)
        return True

    def setupContextMenu(self, vobj, menu):
        populate_probe_menu(menu, vobj.Object)

    def onDelete(self, vobj, subelements):
        if self.camera_sensor:
            self.camera_sensor.detach()
            self.camera_sensor = None
        return True

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def probe_highlight_proxy(doc_name, obj_name):
    try:
        doc = App.getDocument(doc_name)
        obj = doc.getObject(obj_name)
        if obj is None:
            return None
        vp = obj.ViewObject
        proxy = getattr(vp, "Proxy", None)
        if proxy is not None and hasattr(proxy, "set_highlight_state"):
            return proxy
    except Exception:
        pass
    return None


def _is_probe_object(o):
    proxy = getattr(o, "Proxy", None)
    return proxy is not None and proxy.__class__.__name__ == "ContourProbeFeature"


def _selected_probe_objects(clicked_obj):
    try:
        selected = [o for o in Gui.Selection.getSelection() if _is_probe_object(o)]
    except Exception:
        selected = []
    if clicked_obj in selected and len(selected) > 1:
        return selected
    return [clicked_obj]


def populate_probe_menu(menu, obj):
    targets = _selected_probe_objects(obj)
    multi = len(targets) > 1
    if multi:
        act_delete = menu.addAction(f"Delete Probes ({len(targets)})")
        act_delete.triggered.connect(lambda _checked=False, t=targets: _delete_probes(t))
    else:
        act_delete = menu.addAction("Delete Probe")
        act_delete.triggered.connect(lambda _checked=False, o=obj: _delete_probe(o))

    act_delete_all = menu.addAction("Delete All Probes")
    act_delete_all.triggered.connect(lambda _checked=False, o=obj: _delete_all_probes(o))

    if not multi:
        menu.addSeparator()
        act_copy = menu.addAction("Copy Value")
        act_copy.triggered.connect(lambda _checked=False, o=obj: _copy_probe_value(o))


def _delete_probes(objs):
    objs = [o for o in objs if o is not None]
    if not objs:
        return
    doc = objs[0].Document
    for obj in objs:
        try:
            obj.Document.removeObject(obj.Name)
        except Exception as exc:
            App.Console.PrintError(f"DFM contour: could not delete probe. {exc}\n")
    try:
        doc.recompute()
    except Exception:
        pass


def _delete_probe(obj):
    doc = obj.Document
    try:
        doc.removeObject(obj.Name)
        doc.recompute()
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
    text = getattr(obj, "FormattedValue", "") or f"{obj.Value:.1f}{obj.Unit}"
    QtWidgets.QApplication.clipboard().setText(text)


class _InlineRenameEdit(QtWidgets.QLineEdit):
    def __init__(self, parent, on_cancel):
        super().__init__(parent)
        self._on_cancel = on_cancel

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(event)


def _start_inline_rename(vobj):
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
