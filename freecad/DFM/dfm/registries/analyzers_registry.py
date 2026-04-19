# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the DFM addon.

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
#  *   License along with this library; see the file COPYING.LIB. If not,   *
#  *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
#  *   Suite 330, Boston, MA  02111-1307, USA                                *
#  *                                                                         *
#  ***************************************************************************

from typing import Type

from ...dfm.core.base_analyzer import BaseAnalyzer

_analyzer_registry: dict[str, Type[BaseAnalyzer]] = {}


def register_analyzer(analyzer_id: str):
    """A decorator that registers an Analyzer class in the registry."""

    def decorator(cls: Type[BaseAnalyzer]):
        # print(f"Registering Analyzer: '{cls.__name__}' with ID '{analyzer_id}'")
        _analyzer_registry[analyzer_id] = cls
        return cls

    return decorator


def get_analyzer_class(analyzer_id: str) -> Type[BaseAnalyzer] | None:
    """Retrieves an Analyzer class from the registry by its ID."""
    return _analyzer_registry.get(analyzer_id)
