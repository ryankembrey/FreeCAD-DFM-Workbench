# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Render every rule diagram to a contact sheet, in a light and a dark theme.

A development aid: the diagrams are SVG templates (gui/rule_information/
_diagrams.py), and the only way to review one is to look at it. This draws
each one as the rule card would (the same colour substitution, QtSvg),
light on the left and dark on the right, labelled with the rule, into one
PNG::

    PYTHONPATH=. python3 tests/recognition_support/render_diagrams.py diagrams.png
    PYTHONPATH=. python3 tests/recognition_support/render_diagrams.py out.png MAX_BOSS_WALL_RATIO ...

No FreeCAD needed (offscreen Qt).
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import FreeCADGui  # noqa: F401
except ImportError:  # outside FreeCAD: the stand-ins the other GUI tests use
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import freecad_stubs  # noqa: F401

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

from freecad.DFM.core.rules import Rulebook  # noqa: E402
from freecad.DFM.gui.rule_information._diagrams import DIAGRAMS  # noqa: E402
from freecad.DFM.gui.rule_information._renderer import themed_svg  # noqa: E402

WIDTH, HEIGHT = 340, 165
SCALE = 2
THEMES = (("light", "#f3f3f3", "#1e1e1e"), ("dark", "#2b2b2b", "#e6e6e6"))


def palette(window: str, text: str) -> QtGui.QPalette:
    pal = QtGui.QPalette()
    pal.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(window))
    pal.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(text))
    return pal


def render(rules: list, path: str) -> None:
    label_h = 22
    cell_w, cell_h = WIDTH * SCALE, (HEIGHT + label_h) * SCALE
    sheet = QtGui.QImage(cell_w * len(THEMES), cell_h * len(rules), QtGui.QImage.Format.Format_ARGB32)
    sheet.fill(QtGui.QColor("#808080"))
    painter = QtGui.QPainter(sheet)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    font = painter.font()
    font.setPixelSize(12 * SCALE)
    painter.setFont(font)
    for row, rule in enumerate(rules):
        for col, (_name, window, text) in enumerate(THEMES):
            x, y = col * cell_w, row * cell_h
            painter.fillRect(x, y, cell_w, cell_h, QtGui.QColor(window))
            painter.setPen(QtGui.QColor(text))
            painter.drawText(QtCore.QRectF(x + 8, y + 2, cell_w - 16, label_h * SCALE),
                             QtCore.Qt.AlignmentFlag.AlignVCenter,
                             f"{rule.label}  ({rule.name})")
            svg = themed_svg(DIAGRAMS[rule].svg, palette(window, text))
            renderer = QSvgRenderer(QtCore.QByteArray(svg))
            if not renderer.isValid():
                raise SystemExit(f"{rule.name}: the SVG doesn't parse")
            renderer.render(painter, QtCore.QRectF(x, y + label_h * SCALE, WIDTH * SCALE, HEIGHT * SCALE))
    painter.end()
    sheet.save(path)


def main(argv: list) -> None:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    out = argv[1] if len(argv) > 1 else "diagrams.png"
    names = argv[2:]
    rules = [Rulebook[n] for n in names] if names else [r for r in Rulebook if r in DIAGRAMS]
    render(rules, out)
    print(f"{len(rules)} diagrams -> {out}")


if __name__ == "__main__":
    main(sys.argv)
