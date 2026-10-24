# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""PDF report generator for DFM analysis results.

Requires the ``reportlab`` library::

    pip install reportlab
"""

import datetime
from typing import Callable, Optional

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        KeepTogether,
        Flowable,
    )

    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

from ...core.models import CheckResult, Severity
from ...core.rules import Criticality, Rulebook
from ...gui.results.models import DFMReportModel


# ---------------------------------------------------------------------------
# Colour constants & helpers  (only usable when reportlab is present)
# ---------------------------------------------------------------------------

# Hex strings – safe to reference in Paragraph XML markup without reportlab.
_HEX_PRIMARY = "#1B2A4A"
_HEX_ACCENT = "#378ADD"
_HEX_ERROR = "#E24B4A"
_HEX_WARNING = "#D4900A"
_HEX_SUCCESS = "#639922"
_HEX_LIGHT_GRAY = "#F7F8FA"
_HEX_MID_GRAY = "#E0E0E0"
_HEX_DARK_GRAY = "#666666"
_HEX_TEXT = "#1A1A1A"

_SEVERITY_HEX = {
    Severity.ERROR: _HEX_ERROR,
    Severity.WARNING: _HEX_WARNING,
    Severity.SUCCESS: _HEX_SUCCESS,
    Severity.INFO: _HEX_ACCENT,
}

_SEVERITY_LABEL = {
    Severity.ERROR: "Error",
    Severity.WARNING: "Warning",
    Severity.SUCCESS: "Pass",
    Severity.INFO: "Info",
}


# ---------------------------------------------------------------------------
# Custom Flowables  (guarded behind HAS_REPORTLAB)
# ---------------------------------------------------------------------------

if HAS_REPORTLAB:
    _C_PRIMARY = HexColor(_HEX_PRIMARY)
    _C_ACCENT = HexColor(_HEX_ACCENT)
    _C_ERROR = HexColor(_HEX_ERROR)
    _C_WARNING = HexColor(_HEX_WARNING)
    _C_SUCCESS = HexColor(_HEX_SUCCESS)
    _C_LIGHT_GRAY = HexColor(_HEX_LIGHT_GRAY)
    _C_MID_GRAY = HexColor(_HEX_MID_GRAY)
    _C_DARK_GRAY = HexColor(_HEX_DARK_GRAY)
    _C_TEXT = HexColor(_HEX_TEXT)

    class _VerdictBanner(Flowable):
        """Full-width coloured banner displaying the analysis verdict."""

        def __init__(self, text: str, colour: "HexColor", width: float):
            super().__init__()
            self.text = text
            self.colour = colour
            self._width = width
            self.height = 16 * mm

        def wrap(self, available_width, available_height):
            return self._width, self.height

        def draw(self):
            c = self.canv
            c.setFillColor(self.colour)
            c.roundRect(0, 0, self._width, self.height, 3 * mm, fill=1, stroke=0)
            c.setFillColor(white)
            c.setFont("Helvetica-Bold", 13)
            c.drawCentredString(self._width / 2, self.height / 2 - 4, self.text)

    class _AccentLine(Flowable):
        """Thin coloured horizontal rule."""

        def __init__(self, width: float, colour=None, thickness: float = 1.5):
            super().__init__()
            self._width = width
            self.colour = colour or _C_ACCENT
            self.thickness = thickness
            self.height = thickness + 2

        def wrap(self, available_width, available_height):
            return self._width, self.height

        def draw(self):
            self.canv.setStrokeColor(self.colour)
            self.canv.setLineWidth(self.thickness)
            self.canv.line(0, 1, self._width, 1)


# ---------------------------------------------------------------------------
# Report generator
# ---------------------------------------------------------------------------


class DFMPDFReport:
    """Generates a professional, multi-page DFM analysis PDF report.

    Usage::

        report = DFMPDFReport(
            filepath="/tmp/report.pdf",
            target_label="Bracket_v3",
            model=model,                        # DFMReportModel
            get_criticality=process.get_criticality,
        )
        ok = report.build()
    """

    PAGE_W, PAGE_H = A4 if HAS_REPORTLAB else (595.28, 841.89)
    _MARGIN_L = 20 * mm if HAS_REPORTLAB else 56.69
    _MARGIN_R = 20 * mm if HAS_REPORTLAB else 56.69
    _MARGIN_T = 25 * mm if HAS_REPORTLAB else 70.87
    _MARGIN_B = 20 * mm if HAS_REPORTLAB else 56.69
    _CONTENT_W = PAGE_W - _MARGIN_L - _MARGIN_R

    def __init__(
        self,
        filepath: str,
        target_label: str,
        model: "DFMReportModel",
        get_criticality: Callable[["Rulebook"], "Criticality"],
    ):
        self.filepath = filepath
        self.target_label = target_label
        self.model = model
        self.get_criticality = get_criticality
        self.timestamp = datetime.datetime.now()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def build(self) -> bool:
        """Generate the PDF.  Returns *True* on success."""
        if not HAS_REPORTLAB:
            return False

        self._define_styles()

        doc = SimpleDocTemplate(
            self.filepath,
            pagesize=A4,
            leftMargin=self._MARGIN_L,
            rightMargin=self._MARGIN_R,
            topMargin=self._MARGIN_T,
            bottomMargin=self._MARGIN_B,
            title=f"DFM Report - {self.target_label}",
            author="DFM Workbench for FreeCAD",
        )

        story: list = []
        self._add_header(story)
        self._add_metadata(story)
        self._add_verdict(story)
        self._add_summary_table(story)
        self._add_findings_detail(story)
        self._add_passed_rules(story)
        self._add_footer_note(story)

        doc.build(
            story,
            onFirstPage=self._draw_page_frame,
            onLaterPages=self._draw_page_frame,
        )
        return True

    # ------------------------------------------------------------------
    # Page template – header line & footer on every page
    # ------------------------------------------------------------------

    def _draw_page_frame(self, canvas, doc):
        canvas.saveState()

        # Top accent line
        canvas.setStrokeColor(_C_ACCENT)
        canvas.setLineWidth(2)
        y_top = self.PAGE_H - 18 * mm
        canvas.line(self._MARGIN_L, y_top, self.PAGE_W - self._MARGIN_R, y_top)

        # Footer line
        footer_y = 12 * mm
        canvas.setStrokeColor(_C_MID_GRAY)
        canvas.setLineWidth(0.5)
        canvas.line(
            self._MARGIN_L,
            footer_y + 4 * mm,
            self.PAGE_W - self._MARGIN_R,
            footer_y + 4 * mm,
        )

        # Footer text
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(_C_DARK_GRAY)
        canvas.drawString(
            self._MARGIN_L,
            footer_y,
            f"Generated {self.timestamp.strftime('%Y-%m-%d %H:%M')}  |  "
            f"DFM Workbench for FreeCAD",
        )
        canvas.drawRightString(
            self.PAGE_W - self._MARGIN_R,
            footer_y,
            f"Page {doc.page}",
        )

        canvas.restoreState()

    # ------------------------------------------------------------------
    # Paragraph styles
    # ------------------------------------------------------------------

    def _define_styles(self):
        S = ParagraphStyle
        self._s = {
            "title": S(
                "Title",
                fontName="Helvetica-Bold",
                fontSize=22,
                leading=28,
                textColor=_C_PRIMARY,
                spaceAfter=2 * mm,
            ),
            "subtitle": S(
                "Subtitle",
                fontName="Helvetica",
                fontSize=10,
                leading=14,
                textColor=_C_DARK_GRAY,
                spaceAfter=6 * mm,
            ),
            "section": S(
                "Section",
                fontName="Helvetica-Bold",
                fontSize=13,
                leading=18,
                textColor=_C_PRIMARY,
                spaceBefore=8 * mm,
                spaceAfter=3 * mm,
            ),
            "subsection": S(
                "Subsection",
                fontName="Helvetica-Bold",
                fontSize=11,
                leading=15,
                textColor=_C_TEXT,
                spaceBefore=5 * mm,
                spaceAfter=2 * mm,
            ),
            "body": S(
                "Body",
                fontName="Helvetica",
                fontSize=9.5,
                leading=13,
                textColor=_C_TEXT,
            ),
            "body_small": S(
                "BodySmall",
                fontName="Helvetica",
                fontSize=8.5,
                leading=11,
                textColor=_C_DARK_GRAY,
            ),
            "meta_label": S(
                "MetaLabel",
                fontName="Helvetica-Bold",
                fontSize=9.5,
                leading=14,
                textColor=_C_DARK_GRAY,
            ),
            "meta_value": S(
                "MetaValue",
                fontName="Helvetica",
                fontSize=9.5,
                leading=14,
                textColor=_C_TEXT,
            ),
            "th": S(
                "TH",
                fontName="Helvetica-Bold",
                fontSize=8.5,
                leading=11,
                textColor=white,
            ),
            "td": S(
                "TD",
                fontName="Helvetica",
                fontSize=8.5,
                leading=11,
                textColor=_C_TEXT,
            ),
            "td_bold": S(
                "TDBold",
                fontName="Helvetica-Bold",
                fontSize=8.5,
                leading=11,
                textColor=_C_TEXT,
            ),
            "passed": S(
                "PassedRule",
                fontName="Helvetica",
                fontSize=9.5,
                leading=16,
                textColor=_C_TEXT,
                leftIndent=5 * mm,
            ),
        }

    # ------------------------------------------------------------------
    # Report sections
    # ------------------------------------------------------------------

    def _add_header(self, story: list):
        story.append(Paragraph("DFM Analysis Report", self._s["title"]))
        story.append(
            Paragraph(
                self.timestamp.strftime("%d %B %Y, %H:%M"),
                self._s["subtitle"],
            )
        )
        story.append(_AccentLine(self._CONTENT_W, _C_ACCENT, 1.5))
        story.append(Spacer(1, 5 * mm))

    def _add_metadata(self, story: list):
        rows = []
        for label, value in [
            ("Target Model", self.target_label),
            ("Process", self.model.process.name),
            ("Material", self.model.material),
        ]:
            rows.append(
                [
                    Paragraph(label, self._s["meta_label"]),
                    Paragraph(value, self._s["meta_value"]),
                ]
            )

        t = Table(rows, colWidths=[35 * mm, self._CONTENT_W - 35 * mm])
        t.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ("LEFTPADDING", (0, 0), (0, -1), 0),
                ]
            )
        )
        story.append(t)
        story.append(Spacer(1, 6 * mm))

    def _add_verdict(self, story: list):
        text, colour_hex = self.model.get_verdict()
        banner = _VerdictBanner(
            f"Verdict: {text}",
            HexColor(colour_hex),
            self._CONTENT_W,
        )
        story.append(banner)
        story.append(Spacer(1, 8 * mm))

    # ---- Summary table ------------------------------------------------

    def _add_summary_table(self, story: list):
        story.append(Paragraph("Summary", self._s["section"]))
        story.append(_AccentLine(self._CONTENT_W, _C_MID_GRAY, 0.5))
        story.append(Spacer(1, 3 * mm))

        grouped = self.model.get_grouped_results()
        total_errors = sum(
            1 for r in self.model.active_results if r.severity == Severity.ERROR
        )
        total_warnings = sum(
            1 for r in self.model.active_results if r.severity == Severity.WARNING
        )
        passed_rules = [
            r for r in self.model.process.active_rules if r not in grouped
        ]

        stats = (
            f"<b>{total_errors}</b> error{'s' if total_errors != 1 else ''}  |  "
            f"<b>{total_warnings}</b> warning{'s' if total_warnings != 1 else ''}  |  "
            f"<b>{len(passed_rules)}</b> passed  |  "
            f"<b>{len(self.model.process.active_rules)}</b> rules checked"
        )
        story.append(Paragraph(stats, self._s["body"]))
        story.append(Spacer(1, 4 * mm))

        # Header row
        header = [
            Paragraph("Rule", self._s["th"]),
            Paragraph("Criticality", self._s["th"]),
            Paragraph("Errors", self._s["th"]),
            Paragraph("Warnings", self._s["th"]),
            Paragraph("Status", self._s["th"]),
        ]

        rows = [header]

        def _sort_key(item):
            rule_id, _ = item
            return (self.get_criticality(rule_id).value, rule_id.label)

        for rule_id, findings in sorted(grouped.items(), key=_sort_key):
            active = [f for f in findings if not f.ignore]
            errs = sum(1 for f in active if f.severity == Severity.ERROR)
            warns = sum(1 for f in active if f.severity == Severity.WARNING)
            crit = self.get_criticality(rule_id)

            if errs:
                status = f'<font color="{_HEX_ERROR}"><b>Failed</b></font>'
            elif warns:
                status = f'<font color="{_HEX_WARNING}"><b>Warning</b></font>'
            else:
                status = f'<font color="{_HEX_SUCCESS}"><b>Passed</b></font>'

            rows.append(
                [
                    Paragraph(rule_id.label, self._s["td_bold"]),
                    Paragraph(crit.label, self._s["td"]),
                    Paragraph(str(errs) if errs else "-", self._s["td"]),
                    Paragraph(str(warns) if warns else "-", self._s["td"]),
                    Paragraph(status, self._s["td"]),
                ]
            )

        # Append passed rules
        for rule in passed_rules:
            crit = self.get_criticality(rule)
            rows.append(
                [
                    Paragraph(rule.label, self._s["td"]),
                    Paragraph(crit.label, self._s["td"]),
                    Paragraph("-", self._s["td"]),
                    Paragraph("-", self._s["td"]),
                    Paragraph(
                        f'<font color="{_HEX_SUCCESS}"><b>Passed</b></font>',
                        self._s["td"],
                    ),
                ]
            )

        col_w = [
            self._CONTENT_W * 0.35,
            self._CONTENT_W * 0.18,
            self._CONTENT_W * 0.13,
            self._CONTENT_W * 0.15,
            self._CONTENT_W * 0.19,
        ]

        table = Table(rows, colWidths=col_w, repeatRows=1)
        table.setStyle(self._table_style(len(rows)))
        story.append(table)

    # ---- Detailed findings per rule -----------------------------------

    def _add_findings_detail(self, story: list):
        grouped = self.model.get_grouped_results()
        if not grouped:
            return

        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("Detailed Findings", self._s["section"]))
        story.append(_AccentLine(self._CONTENT_W, _C_MID_GRAY, 0.5))

        def _sort_key(item):
            rule_id, findings = item
            crit = self.get_criticality(rule_id)
            active = [f for f in findings if not f.ignore]
            worst = max((f.severity.value for f in active), default=0)
            return (crit.value, -worst, rule_id.label)

        for rule_id, findings in sorted(grouped.items(), key=_sort_key):
            active = [f for f in findings if not f.ignore]
            ignored = [f for f in findings if f.ignore]

            if not active and not ignored:
                continue

            crit = self.get_criticality(rule_id)
            errs = sum(1 for f in active if f.severity == Severity.ERROR)
            warns = sum(1 for f in active if f.severity == Severity.WARNING)

            # Counts string
            parts: list[str] = []
            if errs:
                parts.append(
                    f'<font color="{_HEX_ERROR}">'
                    f'{errs} error{"s" if errs != 1 else ""}</font>'
                )
            if warns:
                parts.append(
                    f'<font color="{_HEX_WARNING}">'
                    f'{warns} warning{"s" if warns != 1 else ""}</font>'
                )
            if ignored:
                parts.append(
                    f'<font color="{_HEX_DARK_GRAY}">'
                    f"{len(ignored)} ignored</font>"
                )
            count_str = "  |  ".join(parts) if parts else ""

            # Rule header (kept together with the start of the table)
            intro: list = [
                Paragraph(
                    f'{rule_id.label}  '
                    f'<font size="8" color="{_HEX_DARK_GRAY}">'
                    f"[{crit.label}]</font>",
                    self._s["subsection"],
                ),
            ]
            if rule_id.description:
                intro.append(Paragraph(rule_id.description, self._s["body_small"]))
            if count_str:
                intro.append(Paragraph(count_str, self._s["body_small"]))
            intro.append(Spacer(1, 2 * mm))
            story.append(KeepTogether(intro))

            # Findings table
            header = [
                Paragraph("Geometry", self._s["th"]),
                Paragraph("Measured", self._s["th"]),
                Paragraph("", self._s["th"]),
                Paragraph("Limit", self._s["th"]),
                Paragraph("Status", self._s["th"]),
                Paragraph("Details", self._s["th"]),
            ]

            table_rows = [header]

            for f in sorted(
                active, key=lambda x: (-x.severity.value, x.value if x.value is not None else 0)
            ):
                geo = ", ".join(ref.label for ref in f.refs) or "-"
                sev_hex = _SEVERITY_HEX.get(f.severity, _HEX_SUCCESS)
                sev_lbl = _SEVERITY_LABEL.get(f.severity, "-")
                val = f"{f.value:.2f}{f.unit}" if f.value is not None else "N/A"
                lim = f"{f.limit:.2f}{f.unit}" if f.limit is not None else "N/A"
                detail = f.message or f.overview

                table_rows.append(
                    [
                        Paragraph(geo, self._s["td_bold"]),
                        Paragraph(val, self._s["td"]),
                        Paragraph(f.comparison or "", self._s["td"]),
                        Paragraph(lim, self._s["td"]),
                        Paragraph(
                            f'<font color="{sev_hex}"><b>{sev_lbl}</b></font>',
                            self._s["td"],
                        ),
                        Paragraph(detail, self._s["td"]),
                    ]
                )

            # Ignored findings (greyed out)
            if ignored:
                _muted = ParagraphStyle(
                    "Muted",
                    parent=self._s["td"],
                    textColor=HexColor("#AAAAAA"),
                )
                for f in ignored:
                    geo = ", ".join(ref.label for ref in f.refs) or "-"
                    val = f"{f.value:.2f}{f.unit}" if f.value is not None else "N/A"
                    lim = f"{f.limit:.2f}{f.unit}" if f.limit is not None else "N/A"
                    table_rows.append(
                        [
                            Paragraph(geo, _muted),
                            Paragraph(val, _muted),
                            Paragraph(f.comparison or "", _muted),
                            Paragraph(lim, _muted),
                            Paragraph("Ignored", _muted),
                            Paragraph(f.message or f.overview, _muted),
                        ]
                    )

            col_w = [
                self._CONTENT_W * 0.14,
                self._CONTENT_W * 0.12,
                self._CONTENT_W * 0.04,
                self._CONTENT_W * 0.12,
                self._CONTENT_W * 0.10,
                self._CONTENT_W * 0.48,
            ]

            table = Table(table_rows, colWidths=col_w, repeatRows=1)
            table.setStyle(self._table_style(len(table_rows)))
            story.append(table)
            story.append(Spacer(1, 4 * mm))

    # ---- Passed rules -------------------------------------------------

    def _add_passed_rules(self, story: list):
        grouped = self.model.get_grouped_results()
        passed = [r for r in self.model.process.active_rules if r not in grouped]
        if not passed:
            return

        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("Passed Rules", self._s["section"]))
        story.append(_AccentLine(self._CONTENT_W, _C_MID_GRAY, 0.5))
        story.append(Spacer(1, 3 * mm))

        for rule in passed:
            bullet = f'<font color="{_HEX_SUCCESS}"><b>&#x2022;</b></font>'
            desc = (
                f'  <font color="{_HEX_DARK_GRAY}" size="8">'
                f"- {rule.description}</font>"
                if rule.description
                else ""
            )
            story.append(
                Paragraph(f"{bullet}  {rule.label}{desc}", self._s["passed"])
            )

        story.append(Spacer(1, 3 * mm))

    # ---- Closing note -------------------------------------------------

    def _add_footer_note(self, story: list):
        story.append(Spacer(1, 8 * mm))
        story.append(_AccentLine(self._CONTENT_W, _C_MID_GRAY, 0.5))
        story.append(Spacer(1, 2 * mm))
        story.append(
            Paragraph(
                "This report was generated by the DFM Workbench for FreeCAD. "
                "Results are based on geometric analysis and configured rule "
                "thresholds. Always verify findings against actual "
                "manufacturing constraints.",
                self._s["body_small"],
            )
        )

    # ------------------------------------------------------------------
    # Shared table style
    # ------------------------------------------------------------------

    def _table_style(self, num_rows: int) -> "TableStyle":
        """Returns a consistent table style with header and alternating rows."""
        cmds = [
            # Header
            ("BACKGROUND", (0, 0), (-1, 0), _C_PRIMARY),
            ("TEXTCOLOR", (0, 0), (-1, 0), white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8.5),
            # All cells
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            # Lines
            ("LINEBELOW", (0, 0), (-1, 0), 1, _C_PRIMARY),
            ("LINEBELOW", (0, 1), (-1, -1), 0.25, _C_MID_GRAY),
        ]
        # Alternating row colours
        for i in range(2, num_rows, 2):
            cmds.append(("BACKGROUND", (0, i), (-1, i), _C_LIGHT_GRAY))
        return TableStyle(cmds)
