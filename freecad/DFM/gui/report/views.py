# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Saved analyses and checks a report can include.

A document can hold saved contours (Draft, Thickness, Undercut, Curvature
Analysis), saved Symmetry Checks and Compare Bodies, and saved Zebra
Stripes. Any of them can be added to a PDF report, each as a section of its
own: a picture, its color scale or key, and for the checks the verdict and
a table of differences.

``ViewSection`` is what the layout needs, and knows nothing of FreeCAD, so
the layout can be previewed and tested without it. ``describe`` makes one
from a saved object; the section's ``render`` draws it in the 3D view for
the moment its picture is taken (see ``ViewportCapture.capture``):

* A contour is drawn from the field it saved, with its saved colors.
* Zebra Stripes are worked out again from the part, with the saved settings.
* A Symmetry Check or a comparison is run again, through its own panel
  (kept out of sight), so the report shows what the panel would. A check
  saved before the part changed is checked as the part is now, and the
  report says so.
"""

from dataclasses import dataclass, field
from typing import Optional

#: Colors for a verdict word, as on the cover.
PASS_HEX = "#639922"
FAIL_HEX = "#E24B4A"
NOTE_HEX = "#D4900A"


@dataclass
class ColorScale:
    """A contour's legend, drawn on its picture as it is in the 3D view."""

    colormap: str
    low: float  # the range the colors span
    high: float
    unit: str
    label: str
    bands: float = 0.0
    #: Pass/fail: the range is flagged red and the rest is gray.
    flag: bool = False
    #: The whole bar, which can be wider than the range (as in the 3D view).
    dom_lo: Optional[float] = None
    dom_hi: Optional[float] = None
    vertical: bool = True
    no_value_label: str = ""


@dataclass
class ViewSection:
    key: str  # the image's key, "view:<object name>"
    name: str  # the saved object's name
    title: str  # its label
    tool: str  # "Thickness Analysis", "Symmetry Check", ...
    model: str  # what it looks at
    facts: list[str] = field(default_factory=list)
    verdict: Optional[tuple[str, str]] = None  # (word, color)
    detail: str = ""
    note: str = ""
    scale: Optional[ColorScale] = None
    #: A key: (label, color, share of the part or None).
    categories: list[tuple[str, str, Optional[float]]] = field(default_factory=list)
    #: Probes placed on a contour: (name, value shown).
    probes: list[tuple[str, str]] = field(default_factory=list)
    #: A table: (title, width share, "left" or "right") per column, then the rows.
    columns: list[tuple[str, float, str]] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    caption: str = ""
    aspect: float = 4 / 3
    #: A short result for the cover's list when there is no verdict, such as "0.8 to 6.5 mm".
    summary: str = ""
    #: Draws it in the 3D view (GUI only; None in tests and previews).
    render: object = None


def _hex(rgb) -> str:
    r, g, b = (max(0, min(255, int(round(c * 255)))) for c in tuple(rgb)[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def _fmt(value: float, unit: str) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text} {unit}".strip()


# ---------------------------------------------------------------------------
# Finding them
# ---------------------------------------------------------------------------


def kind_of(obj) -> Optional[str]:
    """"contour", "zebra", "symmetry" or "compare" for a saved view, else None."""
    tool = getattr(obj, "Tool", "")
    if tool == "zebra" and hasattr(obj, "Count"):
        return "zebra"
    if tool in ("symmetry", "compare") and hasattr(obj, "Settings"):
        return tool
    if hasattr(obj, "FieldData") and hasattr(obj, "Measure") and getattr(obj, "FieldData", None):
        return "contour"
    return None


def saved_views(doc) -> list:
    """The saved views in *doc*, in the order of the tree."""
    if doc is None:
        return []
    return [o for o in doc.Objects if kind_of(o) is not None and getattr(o, "Source", None) is not None]


def describe(obj) -> ViewSection:
    """The report section for the saved view *obj*. Checks are run here."""
    kind = kind_of(obj)
    if kind == "contour":
        return _contour(obj)
    if kind == "zebra":
        return _zebra(obj)
    if kind in ("symmetry", "compare"):
        return _inspection(obj, kind)
    raise ValueError(f"{getattr(obj, 'Label', obj)} is not a saved analysis or check")


# ---------------------------------------------------------------------------
# Contours
# ---------------------------------------------------------------------------


def _contour(obj) -> ViewSection:
    import math

    from ...app.contour.colormap import HighlightSpec
    from ...app.contour.measures import measure_for_id
    from ..contour.document import _MEASURE_TITLES, _band_step, _probe_children, legend_child
    from ..contour.regions import area_shares, triangle_areas

    measure = measure_for_id(obj.Measure)
    tool = _MEASURE_TITLES.get(obj.Measure, ("Contour", ""))[0]
    field_data = obj.FieldData
    opts = dict(obj.Options or {})
    values = [math.inf if v >= 1e299 else float(v) for v in field_data["values"]]
    dmin, dmax = float(field_data.get("dmin", 0.0)), float(field_data.get("dmax", 1.0))
    categorical = bool(measure.categories_for(opts))
    unit = measure.unit
    source = obj.Source
    section = ViewSection(key=f"view:{obj.Name}", name=obj.Name, title=obj.Label, tool=tool,
                          model=source.Label, aspect=4 / 3)
    facts = [tool, f"Model {source.Label}"]
    if measure.needs_pull_direction:
        facts.append(f"Pull direction {obj.PullReference or 'set in the panel'}")
    if obj.Resolution:
        facts.append(f"Mesh {obj.Resolution.lower()}")
    section.facts = [" · ".join(facts)]

    legend = legend_child(obj)
    legend_shown = True
    try:
        legend_shown = legend is None or bool(legend.ViewObject.Visibility)
    except Exception:
        pass
    vertical = legend is None or getattr(legend, "Orientation", "Vertical") != "Horizontal"
    probes = _probe_children(obj)
    section.probes = [(p.Label, p.DisplayText or p.FormattedValue or _fmt(p.Value, p.Unit)) for p in probes]

    areas = triangle_areas(field_data["vertices"], field_data["triangles"])
    if categorical:
        shares = area_shares(values, areas)
        section.categories = [(c.label, _hex(c.rgb), shares.get(c.code, 0.0))
                              for c in measure.categories_for(opts)]
        section.caption = "Each face colored by the way it releases from the mold"
        highlight = HighlightSpec(active=False)
        low, high, colormap, band = dmin, dmax, measure.default_colormap, 0.0
    else:
        dom_lo, dom_hi = measure.data_domain(dmin, dmax, opts)
        low = max(dom_lo, min(float(obj.RangeLow), dom_hi))
        high = max(dom_lo, min(float(obj.RangeHigh), dom_hi))
        low, high = min(low, high), max(low, high)
        flag = bool(obj.HighlightActive)
        colormap, band = obj.ColorMap or measure.default_colormap, _band_step(obj.Bands)
        highlight = HighlightSpec(active=flag, lo=low, hi=high)
        has_no_value = any(not math.isfinite(v) for v in values)
        if legend_shown:
            section.scale = ColorScale(colormap, low, high, unit, measure.label, band, flag, dom_lo, dom_hi,
                                       vertical, measure.no_value_label if has_no_value else "")
        finite = [(v, a) for v, a in zip(values, areas) if math.isfinite(v)]
        total = sum(areas) or 1.0
        lowest = min((v for v, _ in finite), default=dmin)
        highest = max((v for v, _ in finite), default=dmax)
        rows = [["Lowest value", _fmt(lowest, unit)], ["Highest value", _fmt(highest, unit)]]
        section.summary = f"{_fmt(lowest, '')} to {_fmt(highest, unit)}"
        if flag:
            share = sum(a for v, a in finite if low <= v <= high) / total
            rows.append([f"Area from {_fmt(low, unit)} to {_fmt(high, unit)}", f"{100 * share:.1f} %"])
            section.caption = f"{measure.label} from {_fmt(low, unit)} to {_fmt(high, unit)} in red"
        else:
            below = sum(a for v, a in finite if v < low) / total
            above = sum(a for v, a in finite if v > high) / total
            rows.append([f"Area below {_fmt(low, unit)}", f"{100 * below:.1f} %"])
            rows.append([f"Area above {_fmt(high, unit)}", f"{100 * above:.1f} %"])
            section.caption = f"{measure.label}, {_fmt(low, unit)} to {_fmt(high, unit)}"
        no_value = sum(a for v, a in zip(values, areas) if not math.isfinite(v)) / total
        if no_value > 0.0005 and measure.no_value_label:
            rows.append([measure.no_value_label, f"{100 * no_value:.1f} %"])
        section.columns = [("Measured", 0.6, "left"), ("", 0.4, "right")]
        section.rows = rows

    smooth = bool(obj.Smooth)
    if categorical:
        gap = 0.5
    else:
        dom_lo, dom_hi = measure.data_domain(dmin, dmax, opts)
        gap = 0.5 * (dom_hi - dom_lo) if smooth and dom_hi > dom_lo else None
    if categorical and not legend_shown:
        section.categories = []
    section.render = _ContourRender(obj, field_data, values, low, high, colormap, band, smooth, gap,
                                    highlight, probes)
    return section


class _ContourRender:
    """The saved contour, with its probes, as the panel shows it."""

    def __init__(self, obj, field_data, values, low, high, colormap, band, smooth, gap, highlight, probes):
        self.obj = obj
        self.args = (field_data["vertices"], field_data["triangles"], values, field_data["normals"],
                     low, high, colormap, band, smooth, gap, highlight)
        self.probes = list(probes)
        self._probes_were: list = []
        self._node = None

    def shape(self):
        return self.obj.Source.Shape

    def show(self, view) -> None:
        from ..contour.scene import ContourNode

        self._node = ContourNode(self.obj.Source)
        self._node.build(*self.args)
        self._node.attach()
        self._probes_were = []
        for probe in self.probes:  # hidden while the panel is closed
            try:
                self._probes_were.append((probe, bool(probe.ViewObject.Visibility)))
                probe.ViewObject.Visibility = True
            except Exception:
                pass

    def refresh(self) -> None:
        """Size the probes' labels for the camera as it is now."""
        for probe in self.probes:
            try:
                probe.ViewObject.Proxy.refresh()
            except Exception:
                pass

    def hide(self) -> None:
        for probe, was in self._probes_were:
            try:
                probe.ViewObject.Visibility = was
            except Exception:
                pass
        self._probes_were = []
        if self._node is not None:
            self._node.remove()
            self._node = None

    def dispose(self) -> None:
        self.hide()


# ---------------------------------------------------------------------------
# Zebra Stripes
# ---------------------------------------------------------------------------

_READING = ("Where two faces meet, stripes that jump show a crease (G0), stripes that line up "
            "but bend sharply show a tangent-only join (G1), and stripes that run smoothly "
            "across show a curvature-continuous join (G2).")


def _zebra(obj) -> ViewSection:
    from ..contour.zebra_document import settings_of

    s = settings_of(obj)
    source = obj.Source
    if s["mode"] == "fixed":
        how = f"Fixed to the part, along {s['direction_ref']}"
    else:
        how = f"Following the view, {'horizontal' if s['horizontal'] else 'vertical'} stripes"
    faces = s["faces"]
    where = f"{len(faces)} face{'s' if len(faces) != 1 else ''} of {source.Label}" if faces else source.Label
    section = ViewSection(key=f"view:{obj.Name}", name=obj.Name, title=obj.Label, tool="Zebra Stripes",
                          model=source.Label, aspect=4 / 3)
    section.facts = [" · ".join(["Zebra Stripes", where, how, f"{s['count']} stripes"])]
    section.note = _READING
    section.summary = "Reflection stripes"
    section.caption = "Reflection stripes, " + ("fixed to the part" if s["mode"] == "fixed"
                                                else "as seen from this view")
    section.render = _ZebraRender(obj, s)
    return section


class _ZebraRender:
    def __init__(self, obj, settings):
        self.obj = obj
        self.s = settings
        self._overlay = None

    def shape(self):
        return self.obj.Source.Shape

    def show(self, view) -> None:
        from ...app.contour import zebra as Z
        from ...core.utils.conversion import freecad_to_ocp
        from ..contour.zebra_scene import ZebraOverlay

        s = self.s
        mesh = Z.tessellate(freecad_to_ocp(self.obj.Source.Shape), s["quality"], s["faces"] or None)
        overlay = ZebraOverlay(mesh)
        overlay.set_style(count=s["count"], width_share=s["width"] / 100.0, colors=s["colors"])
        overlay.set_edges_visible(s["edges"])
        overlay.mode = s["mode"]
        overlay.horizontal = s["horizontal"]
        overlay.direction = tuple(s["direction"])
        overlay.attach(view, self.obj.Source)
        self._overlay = overlay

    def refresh(self) -> None:
        """Work the stripes out again for the camera as it is now (the
        offscreen render doesn't wait for the camera sensor)."""
        if self._overlay is not None:
            self._overlay._last_camera = None
            self._overlay.update()

    def hide(self) -> None:
        if self._overlay is not None:
            self._overlay.remove()
            self._overlay = None

    def dispose(self) -> None:
        self.hide()


# ---------------------------------------------------------------------------
# Symmetry Check and Compare Bodies
# ---------------------------------------------------------------------------


def _inspection(obj, kind: str) -> ViewSection:
    from ...core.inspection.naming import fmt_mm, fmt_volume
    from ..inspection import document as D

    if kind == "symmetry":
        from ..inspection.symmetry_panel import SymmetryPanel as Panel
    else:
        from ..inspection.compare_panel import ComparePanel as Panel

    out_of_date = D.is_out_of_date(obj)
    panel = Panel(saved=obj)
    render = _InspectionRender(panel)
    try:
        panel._closed = False
        panel.run()  # draws it in the view: hidden again straight away
    finally:
        render.hide()
    result = panel.result
    source = obj.Source
    tool = "Symmetry Check" if kind == "symmetry" else "Compare Bodies"
    section = ViewSection(key=f"view:{obj.Name}", name=obj.Name, title=obj.Label, tool=tool,
                          model=source.Label, aspect=4 / 3, render=render)
    if result is None:
        section.verdict = ("Couldn’t check", FAIL_HEX)
        section.detail = panel.banner.detail if hasattr(panel, "banner") else ""
        section.facts = [tool]
        return section

    state, word, detail = panel.verdict()
    section.verdict = (word, PASS_HEX if state == "pass" else FAIL_HEX)
    section.detail = detail
    if kind == "symmetry":
        what = "Mirror symmetry" if result.kind != "rotational" else f"{result.copies}-fold rotational symmetry"
        facts = ["Symmetry Check", f"Model {source.Label}", what,
                 f"Tolerance ±{fmt_mm(result.tolerance)} mm"]
        if result.note:
            facts.append(result.note.rstrip("."))
    else:
        reference = getattr(obj, "Reference", None)
        facts = ["Compare Bodies", f"A {reference.Label if reference else '?'}", f"B {source.Label}",
                 f"Tolerance ±{fmt_mm(result.tolerance)} mm"]
        if result.fit_rms is not None:
            facts.append(f"Best fit within {result.fit_rms:.3f} mm RMS")
    section.facts = [" · ".join(facts)]
    if out_of_date:
        section.note = ("The model has changed since this check was saved. It was checked again "
                        "for this report.")

    shares = panel.shares() or {}
    colors = {}
    for cat in panel.categories():
        colors[cat.code] = _hex(cat.rgb)
        share = shares.get(cat.code)
        section.categories.append((cat.label, _hex(cat.rgb), share))

    from .layout import Dot

    section.columns = [("#", 0.06, "left"), ("Difference", 0.30, "left"), ("Detail", 0.38, "left"),
                       ("Volume", 0.13, "right"), ("Off by", 0.13, "right")]
    for d in result.differences:
        color = "#8b919b" if d.ignored else colors.get(panel.category_of(d), NOTE_HEX)
        signed = kind == "compare"
        section.rows.append([
            str(d.number), Dot(d.name, color), "Ignored" if d.ignored else d.detail,
            fmt_volume(d.volume, signed=True) if signed else fmt_volume(abs(d.volume)),
            f"{d.deviation:.2f} mm" if d.deviation else "",
        ])
    section.caption = ("Differences in color" if result.differences else
                       ("Symmetric" if kind == "symmetry" else "No differences"))
    return section


class _InspectionRender:
    """Draws a check through its own panel, never shown as a task dialog."""

    def __init__(self, panel):
        self.panel = panel

    def shape(self):
        obj = self.panel.shown_object()
        return obj.Shape if obj is not None else None

    def show(self, view) -> None:
        p = self.panel
        p._closed = False
        if p.in_map():
            p._show_map()
        else:
            p.redraw()
        p._remove_legend()  # the key is drawn on the page instead

    def refresh(self) -> None:
        pass

    def hide(self) -> None:
        p = self.panel
        try:
            p._remove_view()
            if hasattr(p, "_restore_a"):
                p._restore_a()
        finally:
            p._closed = True  # also stops the run the panel queued for itself

    def dispose(self) -> None:
        import FreeCADGui as Gui  # type: ignore

        p = self.panel
        self.hide()
        try:
            if getattr(p.saved.Proxy, "_live_panel", None) is p:
                p.saved.Proxy._live_panel = None
        except Exception:
            pass
        try:
            Gui.Selection.removeObserver(p)
        except Exception:
            pass
        try:
            p.form.deleteLater()
        except Exception:
            pass
