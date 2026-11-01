# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Page layout for the PDF report.

The report is laid out once, in points (1/72 inch), into a list of pages.
Each page is a list of drawing operations, so the same pages can be painted
onto a ``QPdfWriter`` for the file and onto small images for the preview in
the export dialog.

Text is measured at a fixed high resolution and broken into lines here,
never by Qt at paint time, so line breaks and page breaks are identical in
the preview and in the PDF. Viewport images are looked up by key when a
page is painted; until they have been captured a placeholder is drawn in
their place, at the same size.
"""

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from PySide6 import QtCore, QtGui
from PySide6.QtSvg import QSvgRenderer

from ...core.models import Severity
from ...core.rules import Criticality, Rulebook
from ..rule_information._diagrams import DIAGRAMS
from ..rule_information._renderer import themed_svg
from . import logos
from .data import (
    OVERVIEW_ASPECT,
    ReportData,
    RuleSection,
    feedback_messages,
    finding_image_key,
    geometry_label,
    image_jobs,
    limit_phrases,
    passed_sections,
    rule_image_key,
    rule_sections,
    severity_label,
)
from .settings import ReportSettings
from .template import ReportTemplate, fill_text

# Print colors. Reports are always printed dark on white, whatever the theme.
INK = "#1a1c20"
MUTED = "#5d636e"
FAINT = "#8b919b"
LINE = "#d5d9df"
TINT = "#f2f4f7"
PLACEHOLDER = "#e9ecf0"
SEVERITY = {
    Severity.ERROR: "#E24B4A",
    Severity.WARNING: "#D4900A",
    Severity.INFO: "#378ADD",
    Severity.SUCCESS: "#639922",
}
PASS = "#639922"
IGNORED = "#8b919b"
CRITICALITY = {
    Criticality.CRITICAL: "#B3261E",
    Criticality.HIGH: "#E24B4A",
    Criticality.MEDIUM: "#D4900A",
    Criticality.LOW: "#5d636e",
}

DIAGRAM_ASPECT = 340 / 165


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Font:
    size: float
    bold: bool = False
    italic: bool = False


class Typesetter:
    """Measures text in points, independent of the device it is painted on."""

    REF_DPI = 1200

    def __init__(self, family: str = "", scale: float = 1.0):
        self.family = family or QtGui.QGuiApplication.font().family()
        self.scale = scale
        self._ref = QtGui.QImage(8, 8, QtGui.QImage.Format.Format_RGB32)
        dpm = round(self.REF_DPI / 0.0254)
        self._ref.setDotsPerMeterX(dpm)
        self._ref.setDotsPerMeterY(dpm)
        self._px_per_pt = self._ref.logicalDpiY() / 72.0
        self._fonts: dict[Font, QtGui.QFont] = {}
        self._metrics: dict[Font, QtGui.QFontMetricsF] = {}

    def qfont(self, spec: Font) -> QtGui.QFont:
        font = self._fonts.get(spec)
        if font is None:
            font = QtGui.QFont(self.family)
            font.setStyleHint(QtGui.QFont.StyleHint.SansSerif)
            font.setPointSizeF(spec.size * self.scale)
            font.setWeight(QtGui.QFont.Weight.Bold if spec.bold else QtGui.QFont.Weight.Normal)
            font.setItalic(spec.italic)
            font.setHintingPreference(QtGui.QFont.HintingPreference.PreferNoHinting)
            font.setKerning(True)
            self._fonts[spec] = font
        return font

    def _fm(self, spec: Font) -> QtGui.QFontMetricsF:
        fm = self._metrics.get(spec)
        if fm is None:
            fm = QtGui.QFontMetricsF(self.qfont(spec), self._ref)
            self._metrics[spec] = fm
        return fm

    def width(self, text: str, spec: Font) -> float:
        return self._fm(spec).horizontalAdvance(text) / self._px_per_pt

    def ascent(self, spec: Font) -> float:
        return self._fm(spec).ascent() / self._px_per_pt

    def line_height(self, spec: Font) -> float:
        return spec.size * self.scale * 1.32

    def elide(self, text: str, spec: Font, width: float) -> str:
        if self.width(text, spec) <= width:
            return text
        ellipsis = "…"
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.width(text[:mid].rstrip() + ellipsis, spec) <= width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo].rstrip() + ellipsis

    def wrap(self, text: str, spec: Font, width: float) -> list[str]:
        lines: list[str] = []
        for paragraph in (text or "").splitlines() or [""]:
            words = paragraph.split()
            if not words:
                lines.append("")
                continue
            line = ""
            for word in words:
                candidate = f"{line} {word}" if line else word
                if self.width(candidate, spec) <= width:
                    line = candidate
                    continue
                if line:
                    lines.append(line)
                # Break words longer than the line.
                while self.width(word, spec) > width and len(word) > 1:
                    cut = len(word)
                    while cut > 1 and self.width(word[:cut], spec) > width:
                        cut -= 1
                    lines.append(word[:cut])
                    word = word[cut:]
                line = word
            lines.append(line)
        return lines


# ---------------------------------------------------------------------------
# Painting
# ---------------------------------------------------------------------------


class Canvas:
    """Draws in points onto any paint device; ``k`` is device pixels per point.

    Fonts are set in points and Qt converts them with the device's own
    resolution, so the device must report ``72 * k`` dots per inch.
    """

    def __init__(self, painter: QtGui.QPainter, k: float, ts: Typesetter):
        self.p = painter
        self.k = k
        self.ts = ts

    def _r(self, x, y, w, h) -> QtCore.QRectF:
        k = self.k
        return QtCore.QRectF(x * k, y * k, w * k, h * k)

    def text(self, x: float, baseline: float, text: str, spec: Font, color: str = INK,
             align: str = "left") -> None:
        if not text:
            return
        if align != "left":
            w = self.ts.width(text, spec)
            x = x - w if align == "right" else x - w / 2
        self.p.setFont(self.ts.qfont(spec))
        self.p.setPen(QtGui.QColor(color))
        self.p.drawText(QtCore.QPointF(x * self.k, baseline * self.k), text)

    def rect(self, x, y, w, h, fill: Optional[str] = None, stroke: Optional[str] = None,
             radius: float = 0.0, width: float = 0.6) -> None:
        self.p.setBrush(QtGui.QColor(fill) if fill else QtCore.Qt.BrushStyle.NoBrush)
        if stroke:
            pen = QtGui.QPen(QtGui.QColor(stroke), max(width * self.k, 0.5))
            self.p.setPen(pen)
        else:
            self.p.setPen(QtCore.Qt.PenStyle.NoPen)
        r = self._r(x, y, w, h)
        if radius:
            self.p.drawRoundedRect(r, radius * self.k, radius * self.k)
        else:
            self.p.drawRect(r)

    def line(self, x1, y1, x2, y2, color: str = LINE, width: float = 0.6) -> None:
        self.p.setPen(QtGui.QPen(QtGui.QColor(color), max(width * self.k, 0.5)))
        k = self.k
        self.p.drawLine(QtCore.QPointF(x1 * k, y1 * k), QtCore.QPointF(x2 * k, y2 * k))

    def circle(self, cx, cy, r, fill: str) -> None:
        self.p.setPen(QtCore.Qt.PenStyle.NoPen)
        self.p.setBrush(QtGui.QColor(fill))
        k = self.k
        self.p.drawEllipse(QtCore.QPointF(cx * k, cy * k), r * k, r * k)

    def image(self, x, y, w, h, image: QtGui.QImage) -> None:
        """Draw *image* filling the box, cropped to the box's proportions."""
        src = QtCore.QRectF(image.rect())
        box = w / h
        if src.width() / src.height() > box:
            cw = src.height() * box
            src = QtCore.QRectF(src.x() + (src.width() - cw) / 2, src.y(), cw, src.height())
        else:
            ch = src.width() / box
            src = QtCore.QRectF(src.x(), src.y() + (src.height() - ch) / 2, src.width(), ch)
        self.p.drawImage(self._r(x, y, w, h), image, src)

    def svg(self, x, y, w, h, renderer: QSvgRenderer) -> None:
        renderer.render(self.p, self._r(x, y, w, h))

    def logo(self, x, y, h, logo: "logos.Logo", align: str = "left") -> float:
        """Draw *logo* *h* points high; returns its width."""
        w = h * logo.aspect
        if align == "right":
            x -= w
        logo.draw(self.p, self._r(x, y, w, h))
        return w


Op = Callable[[Canvas, dict], None]


@dataclass
class Page:
    label: str
    ops: list[Op] = field(default_factory=list)

    def paint(self, canvas: Canvas, images: dict) -> None:
        for op in self.ops:
            op(canvas, images)


def paint_placeholder(c: Canvas, x, y, w, h, color: Optional[str]) -> None:
    """A stand-in for a viewport image: a box drawn in grey, one face tinted."""
    c.rect(x, y, w, h, fill=PLACEHOLDER, radius=2)
    s = min(w, h) * 0.26
    cx, cy = x + w / 2, y + h / 2 + s * 0.15
    dx, dy = s * 0.95, s * 0.55

    def poly(points, fill, stroke="#b9bfc8"):
        k = c.k
        path = QtGui.QPolygonF([QtCore.QPointF(px * k, py * k) for px, py in points])
        c.p.setPen(QtGui.QPen(QtGui.QColor(stroke), max(0.6 * k, 0.5)))
        c.p.setBrush(QtGui.QColor(fill))
        c.p.drawPolygon(path)

    top = [(cx, cy - dy - s * 0.4), (cx + dx, cy - s * 0.4), (cx, cy + dy - s * 0.4),
           (cx - dx, cy - s * 0.4)]
    poly(top, "#dfe3e8")
    poly([(cx - dx, cy - s * 0.4), (cx, cy + dy - s * 0.4), (cx, cy + dy), (cx - dx, cy)], "#cfd4db")
    poly([(cx + dx, cy - s * 0.4), (cx, cy + dy - s * 0.4), (cx, cy + dy), (cx + dx, cy)], "#c4cad2")
    if color:
        t = 0.5
        inner = [(cx + (px - cx) * t, (cy - s * 0.4) + (py - (cy - s * 0.4)) * t) for px, py in top]
        tint = QtGui.QColor(color)
        tint.setAlphaF(0.85)
        poly(inner, tint.name(QtGui.QColor.NameFormat.HexArgb), color)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def _print_palette() -> QtGui.QPalette:
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#ffffff"))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(INK))
    return palette


_SPLIT_RESULT = re.compile(r"^(.*?)\s*([<>≤≥])\s*(.*)$")


def split_result(overview: str) -> tuple[str, str]:
    """"75.91° > 60.00°" -> ("75.91°", "> 60.00°")."""
    m = _SPLIT_RESULT.match(overview or "")
    if not m:
        return overview or "", ""
    return m.group(1), f"{m.group(2)} {m.group(3)}"


def worst_color(findings) -> Optional[str]:
    active = [f for f in findings if not f.ignore] or list(findings)
    if not active:
        return None
    return SEVERITY.get(max(active, key=lambda f: f.severity.value).severity)


@dataclass
class Column:
    title: str
    width: float  # fraction of the table width
    align: str = "left"


@dataclass
class Pill:
    text: str
    color: str


@dataclass
class Dot:
    text: str
    color: str


class ReportLayout:
    """Lays out a report into pages."""

    def __init__(self, data: ReportData, settings: ReportSettings, template: ReportTemplate,
                 picks: Optional[set[int]] = None, typesetter: Optional[Typesetter] = None):
        self.data = data
        self.settings = settings
        self.t = template
        self.picks = picks
        self.ts = typesetter or Typesetter(template.font_family, template.font_scale)
        self.W, self.H = template.page_points
        self.m = template.margin
        self.left = self.m
        self.right = self.W - self.m
        self.cw = self.right - self.left
        self.top = self.m
        self.bottom = self.H - self.m
        sc = template.font_scale
        self.F_TITLE = Font(21, bold=True)
        self.F_H1 = Font(14, bold=True)
        self.F_H2 = Font(10, bold=True)
        self.F_BODY = Font(9)
        self.F_BOLD = Font(9, bold=True)
        self.F_SMALL = Font(8)
        self.F_SMALL_BOLD = Font(8, bold=True)
        self.F_TINY = Font(7)
        self.F_TINY_BOLD = Font(7, bold=True)
        self.F_STAT = Font(17, bold=True)
        self.gap = 8 * sc
        self.pages: list[Page] = []
        self.y = self.top
        self._diagrams: dict[Rulebook, QSvgRenderer] = {}
        self._extra_rules: dict[int, int] = {}
        self._jobs = {job.key: job for job in image_jobs(data, settings, picks)}

    # -- Flow ----------------------------------------------------------

    @property
    def page(self) -> Page:
        return self.pages[-1]

    def new_page(self, label: str) -> None:
        self.pages.append(Page(label))
        self.y = self.top

    def need(self, h: float, label: Optional[str] = None) -> None:
        """Start a new page unless *h* points still fit on this one."""
        if self.y + h > self.bottom and self.y > self.top + 0.5:
            base = label or self.page.label
            self.new_page(base if base.endswith("(cont.)") else f"{base} (cont.)")

    def emit(self, op: Op) -> None:
        self.page.ops.append(op)

    def space(self, h: float) -> None:
        self.y += h

    # -- Primitives ----------------------------------------------------

    def paragraph(self, text: str, spec: Font, color: str = INK, indent: float = 0.0,
                  width: Optional[float] = None) -> None:
        width = (width or self.cw) - indent
        lh = self.ts.line_height(spec)
        asc = self.ts.ascent(spec)
        for line in self.ts.wrap(text, spec, width):
            self.need(lh)
            y, x = self.y, self.left + indent
            self.emit(lambda c, _i, x=x, y=y, s=line: c.text(x, y + asc, s, spec, color))
            self.y += lh

    def heading(self, text: str, spec: Font, after: float = 4.0, keep: float = 0.0) -> None:
        """A heading, kept on the same page as the first *keep* points that follow."""
        lh = self.ts.line_height(spec)
        self.need(lh + after + max(keep, 3 * lh))
        self.paragraph(text, spec)
        self.space(after)

    def pills_width(self, pills: list[Pill]) -> float:
        return sum(self.ts.width(p.text, self.F_TINY_BOLD) + 10 for p in pills) + 4 * len(pills)

    def draw_pills(self, x: float, y: float, pills: list[Pill]) -> None:
        """Pills left to right starting at x, vertically centred on y."""
        h = self.ts.line_height(self.F_TINY_BOLD) + 2
        asc = self.ts.ascent(self.F_TINY_BOLD)
        for pill in pills:
            w = self.ts.width(pill.text, self.F_TINY_BOLD) + 10
            top = y - h / 2

            def op(c, _i, x=x, top=top, w=w, pill=pill):
                c.rect(x, top, w, h, fill=pill.color, radius=h / 2)
                c.text(x + 5, top + (h - asc) / 2 + asc - 0.6, pill.text, self.F_TINY_BOLD, "#ffffff")

            self.emit(op)
            x += w + 4

    def table_keep(self, rows: int) -> float:
        """Height a table needs on its first page (see ``table``)."""
        row_h = self.ts.line_height(self.F_SMALL) + 6
        head_h = self.ts.line_height(self.F_SMALL_BOLD) + 6
        return head_h + row_h * (rows if rows <= 8 else 3)

    def table(self, columns: list[Column], rows: list[list], label: Optional[str] = None,
              spec: Optional[Font] = None) -> None:
        spec = spec or self.F_SMALL
        pad = 5.0
        row_h = self.ts.line_height(spec) + 6
        head_h = self.ts.line_height(self.F_SMALL_BOLD) + 6
        widths = [col.width * self.cw for col in columns]
        xs = [self.left + sum(widths[:i]) for i in range(len(widths))]
        asc = self.ts.ascent(spec)
        head_asc = self.ts.ascent(self.F_SMALL_BOLD)

        def header():
            y = self.y

            def op(c, _i, y=y):
                c.rect(self.left, y, self.cw, head_h, fill=TINT)
                for col, x, w in zip(columns, xs, widths):
                    tx = x + w - pad if col.align == "right" else x + pad
                    c.text(tx, y + (head_h - head_asc) / 2 + head_asc - 0.8, col.title,
                           self.F_SMALL_BOLD, MUTED, col.align)

            self.emit(op)
            self.y += head_h

        # Keep short tables in one piece; long ones start with a few rows at least.
        self.need(head_h + row_h * (len(rows) if len(rows) <= 8 else 3), label)
        header()
        for row in rows:
            if self.y + row_h > self.bottom:
                self.need(row_h + head_h + 1, label)
                header()
            y = self.y
            cells = []
            for col, x, w, cell in zip(columns, xs, widths, row):
                inner = w - 2 * pad
                if isinstance(cell, Pill):
                    cells.append(("pill", x, w, Pill(self.ts.elide(cell.text, self.F_TINY_BOLD, inner - 10), cell.color), col.align))
                elif isinstance(cell, Dot):
                    cells.append(("dot", x, w, Dot(self.ts.elide(cell.text, spec, inner - 9), cell.color), col.align))
                else:
                    cells.append(("text", x, w, self.ts.elide(str(cell), spec, inner), col.align))

            def op(c, _i, y=y, cells=cells):
                base = y + (row_h - asc) / 2 + asc - 0.8
                for kind, x, w, value, align in cells:
                    if kind == "text":
                        tx = x + w - pad if align == "right" else x + pad
                        c.text(tx, base, value, spec, INK, align)
                    elif kind == "dot":
                        c.circle(x + pad + 3, y + row_h / 2, 2.6, value.color)
                        c.text(x + pad + 9, base, value.text, spec, INK)
                    else:
                        h = self.ts.line_height(self.F_TINY_BOLD) + 2
                        pw = self.ts.width(value.text, self.F_TINY_BOLD) + 10
                        px = x + w - pad - pw if align == "right" else x + pad
                        top = y + (row_h - h) / 2
                        pasc = self.ts.ascent(self.F_TINY_BOLD)
                        c.rect(px, top, pw, h, fill=value.color, radius=h / 2)
                        c.text(px + 5, top + (h - pasc) / 2 + pasc - 0.6, value.text, self.F_TINY_BOLD, "#ffffff")
                c.line(self.left, y + row_h, self.right, y + row_h, LINE, 0.5)

            self.emit(op)
            self.y += row_h

    def image_box(self, x: float, y: float, w: float, h: float, key: str,
                  highlight: Optional[str]) -> None:
        def op(c, images, key=key):
            image = images.get(key)
            if image is not None and not image.isNull():
                c.image(x, y, w, h, image)
                c.rect(x, y, w, h, stroke=LINE, width=0.5)
            else:
                paint_placeholder(c, x, y, w, h, highlight)

        self.emit(op)

    def caption(self, x: float, y: float, w: float, text: str) -> float:
        """A one-line caption centred under an image at x, *w* wide."""
        lh = self.ts.line_height(self.F_TINY)
        asc = self.ts.ascent(self.F_TINY)
        s = self.ts.elide(text, self.F_TINY, w)
        self.emit(lambda c, _i: c.text(x + w / 2, y + asc, s, self.F_TINY, MUTED, "center"))
        return lh

    def limits_text(self, rule: Rulebook) -> list[str]:
        """Target and limit phrases, or how the threshold is found for derived rules."""
        phrases = limit_phrases(rule, self.data.rule_limit(rule))
        if not phrases:
            hint = self.data.derived_hint(rule).strip().rstrip(".")
            phrases = [hint] if hint else []
        return phrases

    # -- Build ---------------------------------------------------------

    def build(self) -> list[Page]:
        self.pages = []
        self.new_page("Cover")
        self._cover()

        sections = rule_sections(self.data, self.settings)
        for i, section in enumerate(sections):
            self._rule_section(section, first=(i == 0))

        passed = passed_sections(self.data, self.settings)
        if passed:
            self._passed(passed, after_rules=bool(sections))

        listed = max(self.settings.list_at_most, 0)
        if self.settings.findings_appendix and any(len(s.findings) > listed for s in sections):
            self._findings_appendix(sections)
        if self.settings.settings_appendix:
            self._settings_appendix()

        self._chrome()
        return self.pages

    # -- Cover ---------------------------------------------------------

    def _cover(self) -> None:
        d, s = self.data, self.settings
        self.paragraph(d.title(s), self.F_TITLE)
        self.space(2)
        sub = [d.process, d.material]
        if d.run is not None:
            sub.append(f"Run {d.run}")
        sub.append(f"{d.created.day} {d.created:%b %Y, %H:%M}")
        self.paragraph(" · ".join(p for p in sub if p), self.F_BODY, MUTED)
        self.space(10)

        # Verdict banner.
        bh = self.ts.line_height(self.F_BOLD) + 12
        y = self.y
        asc = self.ts.ascent(self.F_BOLD)
        verdict = d.verdict

        def banner(c, _i, y=y):
            c.rect(self.left, y, self.cw, bh, fill=d.verdict_color, radius=3)
            c.text(self.left + 10, y + (bh - asc) / 2 + asc - 0.8, verdict, self.F_BOLD, "#ffffff")

        self.emit(banner)
        self.y += bh + 10

        # Details.
        details = [
            ("Project", s.project), ("Prepared by", s.prepared_by), ("Revision", s.revision),
            ("Document", d.document), ("Model", d.model), ("Process", d.process),
            ("Material", d.material), ("Date", d.created.strftime("%Y-%m-%d")),
        ]
        details = [(k, v) for k, v in details if v]
        col_w = self.cw / 2
        lh = self.ts.line_height(self.F_SMALL) + 2
        label_w = max(self.ts.width(k, self.F_SMALL) for k, _ in details) + 10 if details else 0
        for row in range(0, len(details), 2):
            self.need(lh)
            y = self.y
            for col, (key, value) in enumerate(details[row:row + 2]):
                x = self.left + col * col_w
                val = self.ts.elide(value, self.F_SMALL, col_w - label_w - 12)
                a = self.ts.ascent(self.F_SMALL)
                self.emit(lambda c, _i, x=x, y=y, key=key, val=val: (
                    c.text(x, y + a, key, self.F_SMALL, MUTED),
                    c.text(x + label_w, y + a, val, self.F_SMALL, INK)))
            self.y += lh
        self.space(10)

        # Overview image, or the model as designed and the overview side by side.
        if s.overview_image:
            keys = [k for k in ("design", "overview") if k in self._jobs] or ["overview"]
            gap = 10
            w = self.image_width(keys[0])
            h = w / (self._jobs[keys[0]].aspect if keys[0] in self._jobs else OVERVIEW_ASPECT)
            x0 = self.left + (self.cw - (len(keys) * w + gap * (len(keys) - 1))) / 2
            self.need(h + 14)
            for i, key in enumerate(keys):
                job = self._jobs.get(key)
                x = x0 + i * (w + gap)
                self.image_box(x, self.y, w, h, key, worst_color(job.findings) if job else None)
                self.caption(x, self.y + h + 3, w, job.caption if job else "All findings highlighted")
            self.y += h + 3 + self.ts.line_height(self.F_TINY)
            self.space(8)

        # Counts.
        counts = d.counts()
        tiles = [("errors", "Errors", SEVERITY[Severity.ERROR]),
                 ("warnings", "Warnings", SEVERITY[Severity.WARNING]),
                 ("passed", "Rules passed", PASS),
                 ("ignored", "Ignored", IGNORED)]
        tile_gap = 8
        tw = (self.cw - tile_gap * 3) / 4
        th = self.ts.line_height(self.F_STAT) + self.ts.line_height(self.F_TINY) + 10
        self.need(th + 8)
        y = self.y
        for i, (key, label, color) in enumerate(tiles):
            x = self.left + i * (tw + tile_gap)
            n = counts[key]
            sa, ta = self.ts.ascent(self.F_STAT), self.ts.ascent(self.F_TINY)
            slh = self.ts.line_height(self.F_STAT)

            def tile(c, _i, x=x, n=n, label=label, color=color):
                c.rect(x, y, tw, th, stroke=LINE, radius=3, width=0.6)
                c.text(x + tw / 2, y + 5 + sa, str(n), self.F_STAT, color if n else FAINT, "center")
                c.text(x + tw / 2, y + 5 + slh + ta, label, self.F_TINY, MUTED, "center")

            self.emit(tile)
        self.y += th + 14

        # Summary by criticality.
        if s.summary_table:
            sections = rule_sections(d, s) + passed_sections(d, s)
            # Always by criticality, as the heading says; failed before passed.
            sections.sort(key=lambda sec: (sec.criticality.value, sec.passed, sec.rule.label))
            if sections:
                self.heading("Summary by criticality", self.F_H2, keep=self.table_keep(len(sections)))
                rows = []
                for sec in sections:
                    rows.append([
                        Dot(sec.criticality.label, CRITICALITY[sec.criticality]),
                        sec.rule.label,
                        str(len(sec.findings)) if sec.findings else "",
                        self._result_pill(sec),
                    ])
                self.table([Column("Criticality", 0.18), Column("Rule", 0.52),
                            Column("Findings", 0.12, "right"), Column("Result", 0.18, "right")],
                           rows, "Summary")
                self.space(12)

        if s.show_notes and s.notes.strip():
            self.heading("Notes", self.F_H2)
            self.paragraph(s.notes.strip(), self.F_BODY)

    @staticmethod
    def _result_pill(sec: RuleSection) -> Pill:
        if sec.passed:
            return Pill("Passed", PASS)
        if sec.errors:
            return Pill(f"{sec.errors} error{'s' if sec.errors != 1 else ''}", SEVERITY[Severity.ERROR])
        if sec.warnings:
            return Pill(f"{sec.warnings} warning{'s' if sec.warnings != 1 else ''}", SEVERITY[Severity.WARNING])
        return Pill(f"{sec.ignored} ignored", IGNORED)

    # -- Rules ---------------------------------------------------------

    def _rule_section(self, sec: RuleSection, first: bool) -> None:
        d, s, rule = self.data, self.settings, sec.rule
        label = rule.label
        if self.t.rule_page_break or first:
            self.new_page(label)  # rules always start after the cover
        else:
            self.space(16)
            if self.y + 120 > self.bottom:
                self.new_page(label)
            else:
                yy = self.y - 9
                self.emit(lambda c, _i, yy=yy: c.line(self.left, yy, self.right, yy, LINE, 0.6))
                self._extra_rules[id(self.page)] = self._extra_rules.get(id(self.page), 0) + 1
                base = self.page.label.removesuffix(" (cont.)").split(" +")[0]
                self.page.label = f"{base} +{self._extra_rules[id(self.page)]}"

        # Title with result pills.
        pills = []
        if sec.errors:
            pills.append(Pill(f"{sec.errors} error{'s' if sec.errors != 1 else ''}", SEVERITY[Severity.ERROR]))
        if sec.warnings:
            pills.append(Pill(f"{sec.warnings} warning{'s' if sec.warnings != 1 else ''}", SEVERITY[Severity.WARNING]))
        if sec.ignored:
            pills.append(Pill(f"{sec.ignored} ignored", IGNORED))
        lh = self.ts.line_height(self.F_H1)
        title_w = self.ts.width(label, self.F_H1)
        if title_w + 8 + self.pills_width(pills) <= self.cw:
            y = self.y
            asc = self.ts.ascent(self.F_H1)
            self.emit(lambda c, _i, y=y: c.text(self.left, y + asc, label, self.F_H1, self.t.accent))
            self.draw_pills(self.left + title_w + 8, y + lh * 0.55, pills)
            self.y += lh
        else:
            self.paragraph(label, self.F_H1, self.t.accent)
            self.draw_pills(self.left, self.y + 7, pills)
            self.y += 14

        # Criticality, target and limit.
        meta = [f"Criticality: {sec.criticality.label}"]
        if s.target_and_limit:
            meta += self.limits_text(rule)
            where = d.values_title(rule)
            if where:
                meta.append(where)
        self.space(1)
        self.paragraph(" · ".join(meta), self.F_SMALL, MUTED)
        if rule.description:
            self.paragraph(rule.description, self.F_SMALL, MUTED)
        self.space(8)

        if s.rule_diagrams:
            self._diagram(rule)

        if s.feedback_messages:
            for severity, message in feedback_messages(sec):
                self._callout(message, SEVERITY.get(severity, MUTED))
            self.space(2)

        self._rule_images(sec)
        self._findings_table(sec)

    def _diagram(self, rule: Rulebook) -> None:
        diag = DIAGRAMS.get(rule)
        if diag is None:
            return
        renderer = self._diagrams.get(rule)
        if renderer is None:
            renderer = QSvgRenderer(QtCore.QByteArray(themed_svg(diag.svg, _print_palette())))
            self._diagrams[rule] = renderer
        if not renderer.isValid():
            return
        w = min(self.t.diagram_width_mm * 72 / 25.4, self.cw)
        h = w / DIAGRAM_ASPECT
        self.need(h + 8)
        y = self.y
        x = self.left + (self.cw - w) / 2
        self.emit(lambda c, _i, y=y: c.svg(x, y, w, h, renderer))
        self.y += h + 8

    def _callout(self, text: str, color: str) -> None:
        pad = 6
        lines = self.ts.wrap(text, self.F_SMALL, self.cw - 2 * pad - 4)
        lh = self.ts.line_height(self.F_SMALL)
        asc = self.ts.ascent(self.F_SMALL)
        # Keep short messages together; long ones may break across pages.
        chunk: list[str] = []

        def flush(chunk):
            h = len(chunk) * lh + 2 * pad - 2
            y = self.y

            def op(c, _i, y=y, chunk=list(chunk), h=h):
                c.rect(self.left, y, self.cw, h, fill=TINT)
                c.rect(self.left, y, 2.5, h, fill=color)
                for i, line in enumerate(chunk):
                    c.text(self.left + pad + 4, y + pad - 1 + i * lh + asc, line, self.F_SMALL, INK)

            self.emit(op)
            self.y += h + 5

        self.need(min(len(lines), 4) * lh + 2 * pad)
        for line in lines:
            if self.y + (len(chunk) + 1) * lh + 2 * pad > self.bottom and chunk:
                flush(chunk)
                chunk = []
                self.need(self.H)
            chunk.append(line)
        if chunk:
            flush(chunk)

    def finding_image_width(self) -> float:
        """Width of one image in a per-finding grid, kept short enough for two rows a page."""
        n = max(self.t.images_per_row, 1)
        w = (self.cw - 8 * (n - 1)) / n
        return min(w, (self.H - 2 * self.m) * 0.3 * (4 / 3))

    def image_width(self, key: str) -> float:
        """Width in points of the image for *key* on the page."""
        if key.startswith("finding:"):
            return self.finding_image_width()
        job = self._jobs.get(key)
        aspect = job.aspect if job else 4 / 3
        if key in ("design", "overview") and "design" in self._jobs:
            return min((self.cw - 10) / 2, (self.H - 2 * self.m) * 0.3 * aspect)
        w = self.cw if (key == "overview" or self.t.images_per_row <= 2) else self.cw * 0.75
        cap = (self.H - 2 * self.m) * (0.36 if key == "overview" else 0.42)
        return min(w, cap * aspect)

    def _rule_images(self, sec: RuleSection) -> None:
        s = self.settings
        if s.image_mode == "per_rule":
            key = rule_image_key(sec.rule)
            job = self._jobs.get(key)
            if job is None:
                return
            w = self.cw if self.t.images_per_row <= 2 else self.cw * 0.75
            h = w / job.aspect
            cap = (self.H - 2 * self.m) * 0.42
            if h > cap:
                h, w = cap, cap * job.aspect
            self.heading("Viewport", self.F_H2, keep=h + 14)
            self.need(h + 14)
            x = self.left + (self.cw - w) / 2
            self.image_box(x, self.y, w, h, key, worst_color(job.findings))
            self.y += h + 3
            self.y += self.caption(x, self.y, w, job.caption)
            self.space(10)
        elif s.image_mode == "per_finding":
            jobs = [self._jobs[finding_image_key(f)] for f in sec.findings
                    if finding_image_key(f) in self._jobs]
            if not jobs:
                return
            n = max(self.t.images_per_row, 1)
            gap = 8
            w = self.finding_image_width()
            h = w / (4 / 3)
            cap_h = self.ts.line_height(self.F_TINY)
            row_h = h + 3 + cap_h + 8
            self.heading("Viewport", self.F_H2, keep=row_h)
            for start in range(0, len(jobs), n):
                self.need(row_h)
                y = self.y
                row = jobs[start:start + n]
                x0 = self.left + (self.cw - (len(row) * w + gap * (len(row) - 1))) / 2
                for col, job in enumerate(row):
                    x = x0 + col * (w + gap)
                    self.image_box(x, y, w, h, job.key, worst_color(job.findings))
                    self.caption(x, y + h + 3, w, job.caption)
                self.y += row_h
            self.space(4)

    def _findings_table(self, sec: RuleSection) -> None:
        s = self.settings
        limit = max(s.list_at_most, 0)
        shown = sec.findings[:limit] if limit else []
        rest = len(sec.findings) - len(shown)
        if not shown and not rest:
            return
        self.heading("Findings", self.F_H2, keep=self.table_keep(len(shown)))
        if shown:
            rows = []
            for f in shown:
                measured, threshold = split_result(f.overview)
                color = IGNORED if f.ignore else SEVERITY.get(f.severity, MUTED)
                rows.append([Dot(severity_label(f), color), geometry_label(f), measured, threshold])
            self.table([Column("Status", 0.18), Column("Geometry", 0.40),
                        Column("Measured", 0.21, "right"), Column("Limit", 0.21, "right")],
                       rows, sec.rule.label)
        if rest:
            self.space(4)
            where = ", listed in the appendix" if s.findings_appendix else ""
            more = f"and {rest} more{where}." if shown else f"{rest} findings{where}."
            self.paragraph(more, self.F_SMALL, MUTED)
        self.space(6)

    # -- Passed rules and appendices ------------------------------------

    def _passed(self, passed: list[RuleSection], after_rules: bool) -> None:
        if self.y > self.top + 1:
            self.space(16)
        if self.t.rule_page_break and after_rules and self.y > self.bottom - 160:
            self.new_page("Passed rules")
        elif self.y <= self.top + 1:
            self.page.label = "Passed rules"
        self.heading("Passed rules", self.F_H1 if after_rules else self.F_H2,
                     keep=self.table_keep(len(passed)))
        rows = []
        for sec in passed:
            phrases = self.limits_text(sec.rule) if self.settings.target_and_limit else []
            rows.append([sec.rule.label, Dot(sec.criticality.label, CRITICALITY[sec.criticality]),
                         " · ".join(phrases)])
        self.table([Column("Rule", 0.36), Column("Criticality", 0.15), Column("Target and limit", 0.49)],
                   rows, "Passed rules")

    def _findings_appendix(self, sections: list[RuleSection]) -> None:
        self.new_page("Appendix")
        self.heading("Appendix: all findings", self.F_H1)
        rows = []
        for sec in sections:
            for f in sec.findings:
                measured, threshold = split_result(f.overview)
                color = IGNORED if f.ignore else SEVERITY.get(f.severity, MUTED)
                rows.append([sec.rule.label, geometry_label(f), measured, threshold,
                             Dot(severity_label(f), color)])
        self.table([Column("Rule", 0.34), Column("Geometry", 0.22), Column("Measured", 0.15, "right"),
                    Column("Limit", 0.15, "right"), Column("Status", 0.14)], rows, "Appendix")

    def _settings_appendix(self) -> None:
        d = self.data
        self.new_page("Appendix")
        self.heading("Appendix: process and material settings", self.F_H1)
        self.paragraph(" \u00b7 ".join(dict.fromkeys(p for p in (d.process, d.process_kind, d.material) if p)),
                       self.F_SMALL, MUTED)
        self.space(6)
        rows = []
        for rule in sorted(d.active_rules, key=lambda r: r.label):
            crit = d.criticality(rule)
            rows.append([rule.label, Dot(crit.label, CRITICALITY[crit]),
                         " · ".join(self.limits_text(rule)) or "—"])
        self.table([Column("Rule", 0.36), Column("Criticality", 0.15), Column("Target and limit", 0.49)],
                   rows, "Appendix")

    # -- Header and footer ---------------------------------------------

    def _chrome(self) -> None:
        s, d = self.settings, self.data
        values = d.placeholders(s)
        header = fill_text(self.t.header_text, values)
        footer = fill_text(self.t.footer_text, values)
        company = logos.company_logo(s.company_logo) if s.show_company_logo else None
        marks = [m for m in ((logos.dfm_logo() if s.show_dfm_logo else None),
                             (logos.freecad_logo() if s.show_freecad_logo else None)) if m]
        total = len(self.pages)
        hy = self.m - 24
        line_y = self.m - 7
        fy = self.H - self.m + 6
        asc_s = self.ts.ascent(self.F_TINY)
        logo_h = 15.0
        # Footer text sits on the logos' centre line.
        text_y = fy + 4 + logo_h / 2 + asc_s * 0.38
        accent = self.t.accent
        company_name = s.company_name

        for n, page in enumerate(self.pages, start=1):
            def chrome(c, _i, n=n):
                # Header: company logo or name on the left, header text on the right.
                left_end = self.left
                if company is not None:
                    left_end += c.logo(self.left, hy, 15, company) + 8
                elif company_name:
                    c.text(self.left, hy + 11, company_name, self.F_SMALL_BOLD, accent)
                    left_end += self.ts.width(company_name, self.F_SMALL_BOLD) + 8
                if header:
                    text = self.ts.elide(header, self.F_TINY, self.right - left_end)
                    c.text(self.right, hy + 11, text, self.F_TINY, MUTED, "right")
                c.line(self.left, line_y, self.right, line_y, accent, 0.9)

                # Footer: logos and footer text on the left, page number on the right.
                c.line(self.left, fy, self.right, fy, LINE, 0.5)
                x = self.left
                for mark in marks:
                    x += c.logo(x, fy + 4, logo_h, mark) + 6
                if marks:
                    x += 2
                page_text = f"Page {n} of {total}"
                pw = self.ts.width(page_text, self.F_TINY)
                if footer:
                    c.text(x, text_y, self.ts.elide(footer, self.F_TINY, self.right - pw - 12 - x),
                           self.F_TINY, MUTED)
                c.text(self.right, text_y, page_text, self.F_TINY, MUTED, "right")

            page.ops.insert(0, chrome)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def page_image(page: Page, template: ReportTemplate, ts: Typesetter, width_px: int,
               images: dict, device_ratio: float = 1.0) -> QtGui.QImage:
    """Paint *page* into an image *width_px* logical pixels wide (for previews)."""
    W, H = template.page_points
    k = width_px * device_ratio / W
    image = QtGui.QImage(max(1, round(W * k)), max(1, round(H * k)),
                         QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    dpm = round(72 * k / 0.0254)
    image.setDotsPerMeterX(dpm)
    image.setDotsPerMeterY(dpm)
    image.fill(QtGui.QColor("#ffffff"))
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
    painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
    page.paint(Canvas(painter, k, ts), images)
    painter.end()
    image.setDevicePixelRatio(device_ratio)
    return image


def page_thumbnail(page: Page, template: ReportTemplate, ts: Typesetter, width_px: int,
                   device_ratio: float = 1.0, images: Optional[dict] = None) -> QtGui.QImage:
    """A small page image, drawn three times larger and scaled down.

    Text at thumbnail sizes is drawn with distorted widths; drawing it large
    first keeps the proportions of the real page.
    """
    big = page_image(page, template, ts, width_px * 3, images or {}, 1.0)
    small = big.scaledToWidth(round(width_px * device_ratio),
                              QtCore.Qt.TransformationMode.SmoothTransformation)
    small.setDevicePixelRatio(device_ratio)
    return small


def write_pdf(path: str, pages: list[Page], template: ReportTemplate, ts: Typesetter,
              images: dict, title: str = "", creator: str = "DFM workbench for FreeCAD",
              progress: Optional[Callable[[int], bool]] = None) -> bool:
    """Write *pages* to a PDF at *path*. *progress(n)* returning False cancels."""
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter

    W, H = template.page_points
    writer = QPdfWriter(path)
    writer.setResolution(1200)
    writer.setTitle(title)
    writer.setCreator(creator)
    size = QPageSize(QtCore.QSizeF(min(W, H), max(W, H)), QPageSize.Unit.Point)
    orientation = (QPageLayout.Orientation.Landscape if W > H else QPageLayout.Orientation.Portrait)
    writer.setPageLayout(QPageLayout(size, orientation, QtCore.QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point))

    painter = QtGui.QPainter()
    if not painter.begin(writer):
        return False
    k = writer.resolution() / 72.0
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    canvas = Canvas(painter, k, ts)
    ok = True
    for i, page in enumerate(pages):
        if i:
            writer.newPage()
        page.paint(canvas, images)
        if progress is not None and progress(i + 1) is False:
            ok = False
            break
    painter.end()
    return ok
