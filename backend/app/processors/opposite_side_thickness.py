"""Thickness processor for two sensors facing each other.

Ported unchanged from the field-proven thickness-monitoring app:

    manual gap:  thickness = gap - (35 + A) - (35 + B)
    auto gap:    gap       = 2 * 35 + A + B + known_object_thickness
    fallback:    thickness = reference + (baseline_A - A) + (baseline_B - B)

A and B are the two mapped ``displacement.value_mm`` / ``.2`` columns. Limits use
the same optional lower/upper bounds as the old app.
"""
from __future__ import annotations

from .registry import register
from ..services.limits import bounds, number, status
from ..services.opposite_calibration import (
    distance_mm,
    gap_distance,
    object_thickness,
    pair_readings,
    ZERO_OFFSET_MM,
)
from ..services.sbs_calibration import derived_key, parse_baselines


@register("opposite_thickness")
def opposite_side_thickness(config: dict, fields: dict) -> dict:
    config = config or {}
    result = {
        "thickness.actual_mm": None,
        "thickness.deviation_mm": None,
        "quality.status": None,
        "opposite.gap_mm": None,
        "opposite.distance_a_mm": None,
        "opposite.distance_b_mm": None,
        "opposite.object_thickness_mm": None,
    }

    pair = pair_readings(fields, config)
    if pair is None:
        return result

    (key_a, reading_a), (key_b, reading_b) = pair
    gap = gap_distance(config)
    baselines = parse_baselines(config)
    reference = number(config.get("calibration.reference_thickness"), 0.0) or 0.0

    actual = None
    if gap is not None:
        actual = gap - (ZERO_OFFSET_MM + reading_a) - (ZERO_OFFSET_MM + reading_b)
    elif key_a in baselines and key_b in baselines:
        actual = reference + (baselines[key_a] - reading_a) + (baselines[key_b] - reading_b)
    if actual is None:
        return result

    actual = round(actual, 6)
    target = object_thickness(config)
    lower, upper, active = bounds(
        config, "thickness.limit_active", "thickness.lower_limit_mm", "thickness.upper_limit_mm")
    result.update({
        "thickness.actual_mm": actual,
        "thickness.deviation_mm": round(actual - target, 6) if target is not None else None,
        "quality.status": status(actual, lower, upper, active),
        "opposite.gap_mm": gap,
        "opposite.distance_a_mm": distance_mm(reading_a),
        "opposite.distance_b_mm": distance_mm(reading_b),
        "opposite.object_thickness_mm": target,
    })
    result[derived_key("opposite.distance_mm", key_a)] = distance_mm(reading_a)
    result[derived_key("opposite.distance_mm", key_b)] = distance_mm(reading_b)
    return result
