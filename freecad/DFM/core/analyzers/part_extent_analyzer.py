# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""PART_EXTENT_ANALYZER: the length, width and thickness of the whole part.

They are the sides of its smallest bounding box, at whatever angle the part
lies (an oriented box, not the axis-aligned one), longest first. HP's
Multi Jet Fusion handbook warns of long thin parts (length over 10 times the
width): they warp as they cool in the powder.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_OBB
from OCP.TopoDS import TopoDS_Shape

from ...core.base.base_analyzer import BaseAnalyzer
from ...core.registries import register_analyzer
from ...core.utils.geometry import EdgeIndex, FaceIndex


@dataclass(frozen=True)
class PartExtent:
    """The sides of the part's smallest bounding box, in mm, longest first."""

    length: float
    width: float
    thickness: float

    @property
    def slenderness(self) -> float:
        """Length over width (0 for a part with no width)."""
        return self.length / self.width if self.width > 0 else 0.0


@register_analyzer("PART_EXTENT_ANALYZER")
class PartExtentAnalyzer(BaseAnalyzer):
    relative_cost = 5  # one step: the box

    @property
    def analysis_type(self) -> str:
        return "PART_EXTENT_ANALYZER"

    @property
    def name(self) -> str:
        return "Part Extent Analyzer"

    def resolve_prefs(self, prefs: dict) -> None:
        pass

    def iteration_count(self, shape: TopoDS_Shape) -> int:
        return 1

    def analyze(self, item: Any) -> None:
        """Not used: execute() measures the whole shape at once."""
        return None

    def execute(
        self,
        shape: TopoDS_Shape,
        face_index: FaceIndex,
        edge_index: EdgeIndex,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> Optional[PartExtent]:
        box = Bnd_OBB()
        # Exact geometry (not the mesh), the optimal (smallest) box.
        BRepBndLib.AddOBB_s(shape, box, False, True, False)
        if progress_cb:
            progress_cb(1)
        if box.IsVoid():
            return None
        sides = sorted((2.0 * box.XHSize(), 2.0 * box.YHSize(), 2.0 * box.ZHSize()), reverse=True)
        return PartExtent(*(round(s, 4) for s in sides))
