"""Processor registry — maps a template's processor_key to its maths."""
from __future__ import annotations

from typing import Callable

_REGISTRY: dict[str, Callable[[dict, dict], dict]] = {}


def register(key: str):
    def decorator(fn: Callable[[dict, dict], dict]):
        _REGISTRY[key] = fn
        return fn
    return decorator


def get_processor(key: str) -> Callable[[dict, dict], dict]:
    fn = _REGISTRY.get(key)
    if fn is None:
        # Unknown processor: pass the standardized fields through unchanged.
        return lambda config, fields: dict(fields)
    return fn


def registered_keys() -> list[str]:
    return sorted(_REGISTRY.keys())


from . import generic_monitoring, rubber_thickness  # noqa: E402,F401
