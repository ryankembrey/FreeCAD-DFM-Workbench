#  ***************************************************************************
#  *   Copyright (c) 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>              *
#  *                                                                         *
#  *   This file is part of the FreeCAD CAx development system.              *
#  *                                                                         *
#  *   This library is free software; you can redistribute it and/or         *
#  *   modify it under the terms of the GNU Library General Public           *
#  *   License as published by the Free Software Foundation; either          *
#  *   version 2 of the License, or (at your option) any later version.      *
#  *                                                                         *
#  *   This library  is distributed in the hope that it will be useful,      *
#  *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
#  *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
#  *   GNU Library General Public License for more details.                  *
#  *                                                                         *
#  *   You should have received a copy of the GNU Library General Public     *
#  *   License along with this library; see the file COPYING.LIB. If not,    *
#  *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
#  *   Suite 330, Boston, MA  02111-1307, USA                                *
#  *                                                                         *
#  ***************************************************************************

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, Iterator
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face, topods, TopoDS_Edge
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_EDGE
from dfm.models import ProcessRequirement


class BaseAnalyzer(ABC):
    @property
    @abstractmethod
    def analysis_type(self) -> str:
        """
        The unique type of analysis this class performs.
        This is the primary way to identify and retrieve this analyzer.
        """
        pass

    @property
    def requirements(self) -> set[ProcessRequirement]:
        "Returns a list of process requirements."
        return set()

    @property
    @abstractmethod
    def name(self) -> str:
        """
        A user-friendly name for this analysis, e.g., for UI elements.
        """
        pass

    @abstractmethod
    def execute(
        self,
        shape: TopoDS_Shape,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> dict:
        pass

    def iter_faces(
        self,
        shape: TopoDS_Shape,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
    ) -> Iterator[TopoDS_Face]:
        """
        Yields each TopoDS_Face in the shape, handling abort checks.

        Usage:
            for face in self.iter_faces(shape, progress_cb, check_abort):
                results[face] = self._process_face(face)
        """
        explorer = TopExp_Explorer(shape, TopAbs_FACE)  # type: ignore
        count = 0

        while explorer.More():
            if check_abort and check_abort():
                return

            yield topods.Face(explorer.Current())

            count += 1
            if progress_cb:
                progress_cb(count)

            explorer.Next()

    def iter_edges(
        self,
        shape: TopoDS_Shape,
        progress_cb: Optional[Callable[[int], None]] = None,
        check_abort: Optional[Callable[[], bool]] = None,
    ) -> Iterator[TopoDS_Edge]:
        """
        Yields each unique TopoDS_Edge in shape, handling abort checks and
        progress reporting automatically.

        Note: edges are deduplicated by hash — shared edges between two faces
        appear only once, which is correct for edge-level analysis.
        """

        explorer = TopExp_Explorer(shape, TopAbs_EDGE)  # type: ignore
        seen: set[int] = set()
        count = 0

        while explorer.More():
            if check_abort and check_abort():
                return

            edge = topods.Edge(explorer.Current())
            edge_hash = edge.__hash__()

            if edge_hash not in seen:
                seen.add(edge_hash)
                yield edge
                count += 1
                if progress_cb:
                    progress_cb(count)

            explorer.Next()
