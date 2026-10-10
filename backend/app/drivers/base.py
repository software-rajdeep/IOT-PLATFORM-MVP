"""Device driver base class.

A driver knows how to talk to ONE kind of hardware and return a plain dict of
raw values (e.g. {"distance": 24.31}). Everything above it (normalisation,
storage, display) is hardware-agnostic.
"""
from __future__ import annotations

from typing import Optional


class DriverError(RuntimeError):
    pass


class DeviceDriver:
    driver_key: str = "base"

    def __init__(self, device_config: Optional[dict] = None, model_spec: Optional[dict] = None):
        self.config = device_config or {}
        self.model_spec = model_spec or {}

    def open(self) -> None:
        """Acquire the transport (serial port, socket, ...). Optional."""

    def close(self) -> None:
        """Release the transport. Optional."""

    def read(self) -> dict:
        """Return raw values keyed by measurement, e.g. {"distance": 24.31}."""
        raise NotImplementedError

    def __enter__(self) -> "DeviceDriver":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
