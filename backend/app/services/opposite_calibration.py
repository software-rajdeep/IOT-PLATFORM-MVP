"""Opposite-side (facing sensors) calibration helpers.

Ported from the field-proven thickness-monitoring app:

* manual gap:      thickness = gap - (35 + A) - (35 + B)
* auto gap:        gap = 2 * 35 + A + B + known object thickness
* baseline fallback: reference + (baseline_A - A) + (baseline_B - B)

The zero offset (35 mm) is the CD22-35 centre distance; it is a property of the
sensor model, not of a customer, and matches the old appliance.
"""
from __future__ import annotations

from typing import Optional

from .limits import number
from .sbs_calibration import parse_baselines, sensor_readings, DEFAULT_INPUT_KEY

ZERO_OFFSET_MM = 35.0

# Opposite-side is a two-sensor layout: A (input) and B (input.2). Any further mapped
# column is ignored so a stray Sensor 3 never reaches calibration or the dashboard.
MAX_SENSORS = 2

GAP_KEYS = ("calibration.gap_distance", "gap_distance")


def limit_readings(readings: list) -> list:
    """Keep only the A/B pair, dropping any extra mapped sensor columns."""
    return list(readings or [])[:MAX_SENSORS]


def gap_distance(config: dict) -> Optional[float]:
    """Calibrated face-to-face gap, or ``None`` when the mode is not gap-based."""
    config = config or {}
    for key in GAP_KEYS:
        value = number(config.get(key))
        if value is not None and value > 0:
            return value
    return None


def object_thickness(config: dict) -> Optional[float]:
    config = config or {}
    for key in ("calibration.object_thickness", "object_thickness"):
        value = number(config.get(key))
        if value is not None and value > 0:
            return value
    return number(config.get("thickness.target_mm"))


def tolerance(config: dict) -> Optional[float]:
    config = config or {}
    for key in ("calibration.tolerance_mm", "tolerance_mm"):
        value = number(config.get(key))
        if value is not None:
            return abs(value)
    return None


def auto_gap_active(config: dict) -> bool:
    config = config or {}
    mode = str(config.get("calibration.mode") or "").strip().lower()
    if mode == "auto_gap":
        return True
    return str(config.get("auto_gap_active") or "").strip().lower() in {"1", "true", "yes", "on"}


def distance_mm(displacement: Optional[float]) -> Optional[float]:
    if displacement is None:
        return None
    return round(ZERO_OFFSET_MM + float(displacement), 6)


def auto_gap_total(reading_a: float, reading_b: float, thickness: float) -> float:
    return round(2 * ZERO_OFFSET_MM + float(reading_a) + float(reading_b) + float(thickness), 6)


def tolerance_limits(thickness: float, tolerance_mm: Optional[float]):
    if tolerance_mm is None:
        return None, None
    value = abs(float(tolerance_mm))
    return round(float(thickness) - value, 6), round(float(thickness) + value, 6)


def pair_readings(fields: dict, config: Optional[dict] = None):
    """The two opposite-side sensor readings, if the mapped payload has them."""
    readings = sensor_readings(fields or {}, DEFAULT_INPUT_KEY)
    if len(readings) < 2:
        return None
    return readings[0], readings[1]
