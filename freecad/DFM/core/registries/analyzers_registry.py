# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from typing import Callable, Type, TypeVar

from ...core.base.base_analyzer import BaseAnalyzer

TAnalyzer = TypeVar("TAnalyzer", bound=BaseAnalyzer)

_analyzer_registry: dict[str, Type[BaseAnalyzer]] = {}


def register_analyzer(analyzer_id: str) -> Callable[[Type[TAnalyzer]], Type[TAnalyzer]]:
    """A decorator that registers an Analyzer class in the registry."""

    def decorator(cls: Type[TAnalyzer]) -> Type[TAnalyzer]:
        _analyzer_registry[analyzer_id] = cls
        return cls

    return decorator


def get_analyzer_class(analyzer_id: str) -> Type[BaseAnalyzer] | None:
    """Retrieves an Analyzer class from the registry by its ID."""
    return _analyzer_registry.get(analyzer_id)
