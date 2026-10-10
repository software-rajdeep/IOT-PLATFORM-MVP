"""Driver/connector registry — maps a device_profiles.connector_type to a class."""
from __future__ import annotations

from typing import Optional, Type

from .base import DeviceDriver

_REGISTRY: dict[str, Type[DeviceDriver]] = {}


def register(cls: Type[DeviceDriver]) -> Type[DeviceDriver]:
    _REGISTRY[cls.driver_key] = cls
    return cls


def get_driver_class(key: str) -> Optional[Type[DeviceDriver]]:
    return _REGISTRY.get(key)


def registered_keys() -> list[str]:
    return sorted(_REGISTRY.keys())


# Self-register connectors on package import.
from . import cd22_connector  # noqa: E402,F401
