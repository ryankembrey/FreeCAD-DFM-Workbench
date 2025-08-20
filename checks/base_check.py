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
from typing import Generator, Any

from OCC.Core.TopoDS import TopoDS_Face

from enums import CheckType, AnalysisType
from data_types import CheckResult


class BaseCheck(ABC):
    """
    The base class for all checks. This class defines how all checks should behave.
    """

    # A list of check types this check can perform (some can check multiple things)
    handled_check_types: list[CheckType] = []

    # A list of the Analyzer dependencies needed for this check to run.
    # (some Checks need data from multiple Analyzers)
    dependencies: list[AnalysisType] = []

    # Returns a human readable string for UI uses
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    # This is the method where the check is run
    @abstractmethod
    def run_check(
        self,
        analysis_data_map: dict[AnalysisType, dict],
        parameters: dict[str, Any],
        check_type: CheckType,
    ) -> Generator[CheckResult, None, None]:
        pass
