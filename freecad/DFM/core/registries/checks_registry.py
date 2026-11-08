# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from typing import Type

from ...core.rules import Rulebook
from ...core.base.base_check import BaseCheck

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


def _load_checks() -> None:
    """Import every check module once, so their @register_check runs.

    ``core/__init__.py`` used to do this on import, which pulled FreeCAD into
    anything that imported the core. Now it happens on the first lookup.
    """
    from ...core import checks  # noqa: F401


def get_check_class(rule_id: Rulebook) -> Type[BaseCheck] | None:
    """Retrieves a Check class from the registry by its Rulebook ID."""
    if rule_id not in _check_registry:
        _load_checks()
    return _check_registry.get(rule_id)
