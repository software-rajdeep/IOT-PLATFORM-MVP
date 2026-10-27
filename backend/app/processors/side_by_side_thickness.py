"""Thickness processor for sensors mounted side by side over the same surface."""
from __future__ import annotations

from .registry import register
from .rubber_thickness import _num


@register("sbs_thickness")
def side_by_side_thickness(config: dict, fields: dict) -> dict:
    readings = [
        value
        for key, value in fields.items()
        if key == "displacement.value_mm"
        or key.startswith("displacement.value_mm.")
        or key.startswith("displacement.value_mm_")
    ]
    if not readings:
        readings = [fields.get("displacement.value_mm")]
    values = [number for number in (_num(value) for value in readings) if number is not None]

    scale = _num(config.get("calibration.scale"), -1.0)
    offset = _num(config.get("calibration.offset"), 0.0)
    target = _num(config.get("thickness.target_mm"))
    lower = _num(config.get("thickness.lower_limit_mm"))
    upper = _num(config.get("thickness.upper_limit_mm"))
    result = {
        "thickness.actual_mm": None,
        "thickness.deviation_mm": None,
        "quality.status": None,
    }
    if not values:
        return result

    actual = round(scale * (sum(values) / len(values)) + offset, 6)
    result["thickness.actual_mm"] = actual
    if target is not None:
        result["thickness.deviation_mm"] = round(actual - target, 6)
    if lower is not None and upper is not None:
        result["quality.status"] = "PASS" if lower <= actual <= upper else "FAIL"
    return result
