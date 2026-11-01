# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Logos for the report header and footer.

SVG logos stay vector graphics in the PDF. Bitmap logos (PNG, JPEG) are
embedded at their own resolution.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui
from PySide6.QtSvg import QSvgRenderer

# The workbench icon, from the compiled resources or the addon's icon folder.
_DFM_SOURCES = (
    ":/icons/dfm_analysis.svg",
    str(Path(__file__).resolve().parents[4] / "resources" / "icons" / "logo.svg"),
)


# The official FreeCAD symbol (CC BY 4.0, The FreeCAD Project Association AISBL),
# shipped with the addon from https://fpa.freecad.org/images/logos/FreeCAD-symbol.svg
_FREECAD_SYMBOL = str(Path(__file__).resolve().parents[4] / "resources" / "icons" / "FreeCAD-symbol.svg")


def _freecad_sources() -> tuple[str, ...]:
    sources = [_FREECAD_SYMBOL, ":/icons/freecad.svg", ":/icons/freecad", ":/icons/freecad-icon-64.png"]
    try:
        import FreeCAD as App  # type: ignore

        root = Path(App.getResourceDir())
        sources += [
            str(root / "freecad-icon-64.png"),
            str(root / "freecad-icon-48.png"),
            str(root.parent / "icons" / "hicolor" / "scalable" / "apps" / "org.freecad.FreeCAD.svg"),
        ]
    except Exception:
        pass
    return tuple(sources)


class Logo:
    """A logo that can be drawn at any height, keeping its proportions."""

    def __init__(self, svg: Optional[QSvgRenderer] = None, image: Optional[QtGui.QImage] = None):
        self._svg = svg
        self._image = image

    @property
    def aspect(self) -> float:
        if self._svg is not None:
            size = self._svg.viewBoxF().size()
            if size.isEmpty():
                size = QtCore.QSizeF(self._svg.defaultSize())
        else:
            size = QtCore.QSizeF(self._image.size())
        return size.width() / size.height() if size.height() > 0 else 1.0

    def draw(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        if self._svg is not None:
            self._svg.render(painter, rect)
        else:
            painter.drawImage(rect, self._image)


def _load(path: str) -> Optional[Logo]:
    if not path:
        return None
    is_resource = path.startswith(":")
    if not (QtCore.QFile.exists(path) if is_resource else os.path.isfile(path)):
        return None
    if path.lower().endswith(".svg") or (is_resource and not path.lower().endswith(".png")):
        renderer = QSvgRenderer(path)
        if renderer.isValid():
            return Logo(svg=renderer)
    image = QtGui.QImage(path)
    if not image.isNull():
        return Logo(image=image)
    return None


def _first(paths) -> Optional[Logo]:
    for path in paths:
        logo = _load(path)
        if logo is not None:
            return logo
    return None


@lru_cache(maxsize=None)
def dfm_logo() -> Optional[Logo]:
    return _first(_DFM_SOURCES)


@lru_cache(maxsize=None)
def freecad_logo() -> Optional[Logo]:
    return _first(_freecad_sources())


def company_logo(path: str) -> Optional[Logo]:
    """The company logo at *path*; None when it is missing or not an image."""
    return _load(os.path.expanduser(path)) if path else None
