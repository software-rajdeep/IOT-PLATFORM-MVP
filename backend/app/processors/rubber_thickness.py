"""BLOCK 4 — Rubber Thickness processor.

A reusable application template's maths. Input: the standardized
displacement.value_mm plus configuration (calibration + limits). Output:
thickness.actual_mm, thickness.deviation_mm and quality.status.

    thickness.actual_mm = calibration.scale * displacement + calibration.offset
    thickness.deviation = thickness.actual_mm - thickness.target_mm
    quality.status       = PASS if lower <= actual <= upper else FAIL

The scale/offset are NEVER hard-coded by the platform: they come from the
customer's calibration (BLOCK 5).
"""
from __future__ import annotations

from .registry import register
from ..services.limits import bounds, number as _num, status


@register("rubber_thickness")
def rubber_thickness(config: dict, fields: dict) -> dict:
    displacement = _num(fields.get("displacement.value_mm"))
    scale = _num(config.get("calibration.scale"), -1.0)
    offset = _num(config.get("calibration.offset"), 0.0)
    target = _num(config.get("thickness.target_mm"))
    lower, upper, active = bounds(
        config, "thickness.limit_active", "thickness.lower_limit_mm", "thickness.upper_limit_mm")

    result = {
        "thickness.actual_mm": None,
        "thickness.deviation_mm": None,
        "quality.status": None,
    }
    if displacement is None:
        return result

    actual = round(scale * displacement + offset, 6)
    result["thickness.actual_mm"] = actual
    if target is not None:
        result["thickness.deviation_mm"] = round(actual - target, 6)
    result["quality.status"] = status(actual, lower, upper, active)
    return result
