# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Live direction arrow shown while hovering faces/edges in a pull-direction pick mode.

Usage from a panel that is a ``Gui.Selection`` observer::

    self._preview = PullPreview()          # when picking starts
    ...
    def setPreselection(self, doc, obj, sub, *_):
        self._preview.show_for(doc, obj, sub)
    def removePreselection(self, *_):
        self._preview.hide()
    ...
    self._preview.end()                    # when picking ends
"""

from typing import Optional

from ..visuals import DirectionIndicator
from .picking import resolve_subshape
from .pull_direction import PULL_DIRECTION_COLOR, compute_direction


class PullPreview:
    def __init__(self, color=PULL_DIRECTION_COLOR):
        self._color = color
        self._indicator: Optional[DirectionIndicator] = None

    @property
    def active(self) -> bool:
        return self._indicator is not None

    def begin(self) -> None:
        """Create the arrow (hidden until something is hovered). Safe to call repeatedly."""
        if self._indicator is None:
            self._indicator = DirectionIndicator(self._color, "")

    def show_for(self, doc_name: str, obj_name: str, sub_name: str) -> None:
        """Show the arrow for the hovered sub-element, or hide it if it has no direction."""
        if self._indicator is None:
            return
        sub_shape = resolve_subshape(doc_name, obj_name, sub_name)
        if sub_shape is None:
            return
        try:
            result = compute_direction(sub_shape)
        except Exception:
            result = None
        if result is None:
            self._indicator.set_visible(False)
            return
        pnt, dir_vec = result
        self._indicator.show(pnt, dir_vec)
        self._indicator.set_visible(True)

    def hide(self) -> None:
        """Hide the arrow when the mouse leaves the geometry."""
        if self._indicator is not None:
            self._indicator.set_visible(False)

    def end(self) -> None:
        """Remove the arrow from the scene graph."""
        if self._indicator is not None:
            self._indicator.remove()
            self._indicator = None
