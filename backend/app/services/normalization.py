"""BLOCK 3 — Normalization.

Converts a connector's raw output into the Rajdeep-standard field(s). From this
point upward, the application knows nothing about CD22, RS-485 or OPTEX.

    raw count (151)  ->  displacement.value_mm = raw * model.resolution (0.01)  ->  1.51
"""
from __future__ import annotations

from typing import Optional


def decode_displacement(measurement_raw: Optional[float], resolution: float) -> Optional[float]:
    """raw counts -> millimetres using the device model's resolution."""
    if measurement_raw is None:
        return None
    try:
        return round(float(measurement_raw) * float(resolution or 0.01), 6)
    except (TypeError, ValueError):
        return None


def normalize(measurement_raw: Optional[float], resolution: float) -> dict:
    """Return the standardized device field(s) for a raw reading."""
    displacement = decode_displacement(measurement_raw, resolution)
    return {"displacement.value_mm": displacement}
