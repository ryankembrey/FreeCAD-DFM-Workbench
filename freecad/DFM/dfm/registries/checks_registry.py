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

from ...dfm.rules import Rulebook
from ...dfm.core.base_check import BaseCheck

_check_registry: dict[Rulebook, Type[BaseCheck]] = {}


def register_check(*rule_ids: Rulebook):
    """
    A decorator that registers a Check class against one or more Rulebook IDs.
    """

    def decorator(cls: Type[BaseCheck]):
        for rule_id in rule_ids:
            if not isinstance(rule_id, Rulebook):
                raise TypeError(
                    f"Invalid type passed to register_check. Expected a Rulebook member, got {type(rule_id)}."
                )

            # print(f"Registering Check: '{cls.__name__}' for rule '{rule_id.name}'")
            _check_registry[rule_id] = cls
        return cls

    return decorator


def get_check_class(rule_id: Rulebook) -> Type[BaseCheck] | None:
    """Retrieves a Check class from the registry by its Rulebook ID."""
    return _check_registry.get(rule_id)
