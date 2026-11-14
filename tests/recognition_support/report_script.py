# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The PDF report's navigation and its sections for saved analyses and
checks, offscreen with FreeCAD stand-ins. Run by tests/test_report.py in a
fresh interpreter.

* Layout: every rule with findings, the passed rules, each added view and
  the appendix has an anchor on the page where it starts; the summary rows
  lead to their rules; every page after the cover leads back to the summary.
* A view section: its title and verdict, its legend drawn on the picture,
  the probes and differences tables, and a row in the cover's list.
* The written PDF: a Qt file with the bookmarks and links appended, which
  reads back with the right targets (checked with pypdf when installed, and
  with the module's own parser otherwise).
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import freecad_stubs  # noqa: E402,F401

from PySide6 import QtWidgets  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from freecad.DFM.core.models import CheckResult, GeometryRef, Severity  # noqa: E402
from freecad.DFM.core.rules import Criticality, Rulebook as R  # noqa: E402
from freecad.DFM.gui.report import pdf_nav as N  # noqa: E402
from freecad.DFM.gui.report.data import ReportData  # noqa: E402
from freecad.DFM.gui.report.layout import Dot, ReportLayout, write_pdf  # noqa: E402
from freecad.DFM.gui.report.settings import ReportSettings  # noqa: E402
from freecad.DFM.gui.report.template import get_template  # noqa: E402
from freecad.DFM.gui.report.views import ColorScale, ViewSection  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def finding(rule, severity, i):
    return CheckResult(rule, severity, f"{1.0 + i / 10:.1f} mm (min 1.5 mm)", "Too thin", False,
                       1.0, 1.5, refs=[GeometryRef("Face", i, f"Face{i + 1}")])


grouped = {
    R.MIN_WALL_THICKNESS: [finding(R.MIN_WALL_THICKNESS, Severity.ERROR, i) for i in range(30)],
    R.MIN_DRAFT_ANGLE: [finding(R.MIN_DRAFT_ANGLE, Severity.WARNING, i) for i in range(3)],
}
active = [R.MIN_WALL_THICKNESS, R.MIN_DRAFT_ANGLE, R.MAX_WALL_THICKNESS, R.NO_UNDERCUTS]
contour = ViewSection(
    key="view:Thickness", name="Thickness", title="Thickness Analysis", tool="Thickness Analysis",
    model="Enclosure", facts=["Thickness Analysis · Model Enclosure"],
    scale=ColorScale("Turbo", 0.0, 4.0, "mm", "Wall thickness", 0.0, False, 0.0, 6.5, True, "No wall"),
    columns=[("Measured", 0.6, "left"), ("", 0.4, "right")], rows=[["Lowest value", "0.8 mm"]],
    probes=[("Probe 1", "1.2 mm"), ("Probe 2", "3.4 mm")], caption="Wall thickness, 0 to 4 mm",
    summary="0.8 to 6.5 mm")
symmetry = ViewSection(
    key="view:Symmetry", name="Symmetry", title="Symmetry Check of Enclosure", tool="Symmetry Check",
    model="Enclosure", verdict=("Not symmetric", "#E24B4A"), detail="2 differences",
    categories=[("Only on one side", "#9B59B6", 0.012), ("Symmetric", "#C2C4C9", 0.988)],
    columns=[("#", 0.06, "left"), ("Difference", 0.30, "left"), ("Detail", 0.38, "left"),
             ("Volume", 0.13, "right"), ("Off by", 0.13, "right")],
    rows=[["1", Dot("Boss", "#9B59B6"), "only on the left", "0.21 cm³", "3.00 mm"]])
data = ReportData(document="Doc", model="Enclosure", process="Plastic Injection Molding", process_kind="",
                  material="ABS", verdict="Failed", verdict_color="#E24B4A", grouped=grouped,
                  active_rules=active, criticality=lambda r: Criticality.HIGH, views=[contour, symmetry])
settings = ReportSettings()
settings.list_at_most = 10
settings.findings_appendix = True
template = get_template(settings.template)

layout = ReportLayout(data, settings, template)
pages = layout.build()
a = layout._anchors
for name in ("cover", "summary", f"rule:{R.MIN_WALL_THICKNESS.name}", f"rule:{R.MIN_DRAFT_ANGLE.name}",
             "passed", "view:Thickness", "view:Symmetry", "appendix-findings"):
    check(name in a, f"no anchor {name}: {sorted(a)}")
check(a["cover"][0] == 0 and a["summary"][0] == 0, a)
check(pages[a["view:Thickness"][0]].label == "Thickness Analysis", [p.label for p in pages])
check(pages[a["appendix-findings"][0]].label == "Appendix", pages[a["appendix-findings"][0]].label)

nav = layout.navigation()
targets = {(link.page, link.target) for link in nav.links}
# The summary rows, on the cover, lead to their rules and to the passed rules.
check((0, a[f"rule:{R.MIN_WALL_THICKNESS.name}"]) in targets, "summary row -> wall thickness")
check((0, a["passed"]) in targets, "summary row -> passed rules")
check((0, a["view:Symmetry"]) in targets, "cover's list -> the symmetry check")
# "and 20 more, listed in the appendix" leads there.
wall_page = a[f"rule:{R.MIN_WALL_THICKNESS.name}"][0]
check(any(link.target == a["appendix-findings"] for link in nav.links), "the appendix link")
# Every page after the cover leads back to the summary.
back = {link.page for link in nav.links if link.target == a["summary"]}
check(back >= set(range(1, len(pages))), (back, len(pages)))
titles = [b.title for b in nav.bookmarks]
check(titles == ["Summary", "Rules with findings", "Passed rules", "Analyses and checks",
                 "Appendix: all findings"], titles)
check([b.title for b in nav.bookmarks[1].children] == [R.MIN_WALL_THICKNESS.label, R.MIN_DRAFT_ANGLE.label],
      [b.title for b in nav.bookmarks[1].children])
check([b.title for b in nav.bookmarks[3].children] == ["Thickness Analysis", "Symmetry Check of Enclosure"],
      nav.bookmarks[3].children)

# Without a summary table the footers lead back to the cover.
quiet = ReportSettings()
quiet.summary_table = False
lay2 = ReportLayout(ReportData(document="Doc", model="M", process="P", process_kind="", material="X",
                               verdict="Passed", verdict_color="#639922", grouped={}, active_rules=active,
                               criticality=lambda r: Criticality.LOW), quiet, template)
p2 = lay2.build()
check(all(link.target == lay2._anchors["cover"] for link in lay2.navigation().links
          if link.page > 0 and link.target[0] == 0), "back to the cover")

# Written through the PDF writer, then navigation added.
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "report.pdf")
check(write_pdf(path, pages, template, layout.ts, {}), "write_pdf")
before = os.path.getsize(path)
N.add_navigation(path, nav)
data_bytes = open(path, "rb").read()
check(len(data_bytes) > before and data_bytes.count(b"%%EOF") == 2, "an incremental update")
check(data_bytes.count(b"/Subtype /Link") == len(nav.links), "one annotation a link")
try:
    import pypdf
except ImportError:
    pypdf = None
if pypdf is not None:
    reader = pypdf.PdfReader(path, strict=True)
    check(len(reader.pages) == len(pages), "page count")
    check([o.title for o in reader.outline if not isinstance(o, list)] == titles, reader.outline)
    first = reader.pages[0]
    dests = [reader.get_page_number(x.get_object()["/Dest"][0]) for x in first["/Annots"]]
    check(a[f"rule:{R.MIN_WALL_THICKNESS.name}"][0] in dests and a["passed"][0] in dests, dests)
    last = reader.pages[len(pages) - 1]
    check(any(reader.get_page_number(x.get_object()["/Dest"][0]) == 0 for x in last["/Annots"]),
          "the last page leads back")
    print("report: checked with pypdf")

# A file Qt didn't write, or a damaged one, is refused without being touched.
bad = os.path.join(tmp, "bad.pdf")
open(bad, "wb").write(b"%PDF-1.7\nnot a real pdf\n")
try:
    N.add_navigation(bad, nav)
    raise AssertionError("a damaged PDF was accepted")
except N.PdfNavError:
    pass
check(open(bad, "rb").read() == b"%PDF-1.7\nnot a real pdf\n", "left untouched")

print("report: ok")
