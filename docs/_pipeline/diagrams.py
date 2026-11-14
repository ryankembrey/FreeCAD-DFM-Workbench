# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Rule diagrams as SVG files, in the site's light and dark colours.

The workbench's own ``themed_svg`` picks the colours, as it does for a
FreeCAD theme, from a palette matching the site's background and text.
Pages show the light file in the light scheme and the dark file in the dark
one (``#only-light`` / ``#only-dark``).
"""

from __future__ import annotations

#: (name, page background, text): the site's light and dark schemes.
THEMES = (
    ("light", "#ffffff", "#1f2329"),
    ("dark", "#0b0c0f", "#e2e4e9"),  # Zensical slate: hsl(hue 15% 5%) and 90%
)


def path(slug: str, theme: str) -> str:
    """Where a diagram is written, relative to docs/content."""
    return f"assets/diagrams/{slug}-{theme}.svg"


def images(slug: str, alt: str, prefix: str, attrs: str = "") -> str:
    """Markdown for a diagram in both schemes; *prefix* leads from the page to
    docs/content (such as ``../../``)."""
    a = f"{{ {attrs} }}" if attrs else ""
    return (
        f"![{alt}]({prefix}{path(slug, 'light')}#only-light){a}\n"
        f"![{alt}]({prefix}{path(slug, 'dark')}#only-dark){a}"
    )


def write(out, rules) -> None:
    from PySide6 import QtGui
    from freecad.DFM.gui.rule_information._renderer import themed_svg

    for rule in rules:
        if not rule.svg:
            continue
        for name, window, text in THEMES:
            palette = QtGui.QPalette()
            palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(window))
            palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(text))
            out.write("content/" + path(rule.slug, name), themed_svg(rule.svg, palette))
