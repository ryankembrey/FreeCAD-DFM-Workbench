# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""What the Symmetry Check and Compare Bodies tools find."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from OCP.TopoDS import TopoDS_Shape

from .geometry import Box, Vec

# Kinds of difference.
EXTRA = "extra"        # symmetry: material on one side only
CUT = "cut"            # symmetry: a cut on one side only
MISMATCH = "mismatch"  # symmetry: on both sides, but they don't line up
ADDED = "added"        # compare: in B, not in A
REMOVED = "removed"    # compare: in A, not in B
CHANGED = "changed"    # compare: a feature in both, changed


@dataclass
class Difference:
    """One thing that isn't symmetric, or one change between two bodies."""

    number: int
    kind: str
    name: str
    detail: str
    #: Volume of material involved (mm³), signed for a comparison.
    volume: float
    #: How far the furthest point of it is from where it should be (mm).
    deviation: float
    #: Faces of the shown body (1-based) to highlight.
    faces: list[int] = field(default_factory=list)
    #: Material to draw solid (in the shown body's place).
    solids: list[TopoDS_Shape] = field(default_factory=list)
    #: Material to draw as a ghost: where a partner would be, or what was removed.
    ghosts: list[TopoDS_Shape] = field(default_factory=list)
    #: A point on it, for its marker and card.
    anchor: Optional[Vec] = None
    box: Optional[Box] = None
    #: Stays the same across runs while the part doesn't change, so an
    #: ignored difference stays ignored.
    key: str = ""
    #: Ignored by the user: shown greyed, and doesn't count against the verdict.
    ignored: bool = False

    @property
    def counts(self) -> bool:
        return not self.ignored


@dataclass
class SymmetryResult:
    kind: str  # "mirror" or "rotational"
    origin: Vec
    direction: Vec
    copies: int
    tolerance: float
    volume: float
    differences: list[Difference]
    #: How the plane or axis was chosen, for the panel ("Auto chose YZ...").
    note: str = ""

    @property
    def counted(self) -> list[Difference]:
        return [d for d in self.differences if d.counts]

    @property
    def symmetric(self) -> bool:
        return not self.counted

    @property
    def difference_volume(self) -> float:
        return sum(abs(d.volume) for d in self.counted)

    @property
    def matched_fraction(self) -> float:
        if self.volume <= 0:
            return 1.0
        return max(0.0, 1.0 - self.difference_volume / self.volume)

    @property
    def furthest(self) -> float:
        return max((d.deviation for d in self.counted), default=0.0)


@dataclass
class BodyProps:
    volume: float
    area: float
    faces: int
    size: Vec


@dataclass
class CompareResult:
    tolerance: float
    alignment: str
    #: How B was moved onto A (a 3 x 4 matrix), and how well it fits (RMS, mm).
    transform: Optional[list] = None
    fit_rms: Optional[float] = None
    a: Optional[BodyProps] = None
    b: Optional[BodyProps] = None
    differences: list[Difference] = field(default_factory=list)

    @property
    def counted(self) -> list[Difference]:
        return [d for d in self.differences if d.counts]

    @property
    def identical(self) -> bool:
        return not self.counted

    @property
    def added(self) -> float:
        return sum(d.volume for d in self.counted if d.volume > 0)

    @property
    def removed(self) -> float:
        return -sum(d.volume for d in self.counted if d.volume < 0)

    @property
    def net(self) -> float:
        if self.a is None or self.b is None:
            return self.added - self.removed
        return self.b.volume - self.a.volume
