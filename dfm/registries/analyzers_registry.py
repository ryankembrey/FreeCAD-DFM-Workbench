from typing import Type

from dfm.core.base_analyzer import BaseAnalyzer

_analyzer_registry: dict[str, Type[BaseAnalyzer]] = {}


def register_analyzer(analyzer_id: str):
    """A decorator that registers an Analyzer class in the registry."""

    def decorator(cls: Type[BaseAnalyzer]):
        print(f"Registering Analyzer: '{cls.__name__}' with ID '{analyzer_id}'")
        _analyzer_registry[analyzer_id] = cls
        return cls

    return decorator


def get_analyzer_class(analyzer_id: str) -> Type[BaseAnalyzer] | None:
    """Retrieves an Analyzer class from the registry by its ID."""
    return _analyzer_registry.get(analyzer_id)
