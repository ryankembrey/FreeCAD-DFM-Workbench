from typing import Type

from dfm.rules import Rulebook
from dfm.core.base_check import BaseCheck

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

            print(f"Registering Check: '{cls.__name__}' for rule '{rule_id.name}'")
            _check_registry[rule_id] = cls
        return cls

    return decorator


def get_check_class(rule_id: Rulebook) -> Type[BaseCheck] | None:
    """Retrieves a Check class from the registry by its Rulebook ID."""
    return _check_registry.get(rule_id)
