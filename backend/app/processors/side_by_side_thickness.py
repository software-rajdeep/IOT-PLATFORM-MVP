"""Thickness processor for sensors mounted side by side over the same surface.

Two calibration styles are supported, matching the field-proven behaviour of the
standalone thickness-monitoring app:

* ``baseline`` (side-by-side sensors): calibration captures one baseline reading
  per sensor plus the known reference thickness. Every later reading becomes
  ``reference_thickness + (baseline - current)``. When a reference sensor is
  configured its drift ``current_ref - baseline_ref`` is added back, so a
  drifting reference does not distort the measured thickness.
* ``linear`` (single sensor / legacy two-point): ``scale * input + offset``.

The processor returns the aggregate thickness plus one thickness per sensor
(``thickness.sensor_mm``, ``.2``, ``.3`` ...) and the reference error, so the
dashboard can show each sensor exactly like the old app.
"""
from __future__ import annotations

from .registry import register
from .rubber_thickness import _num
from ..services.limits import bounds, sensor_bounds, status
from ..services.sbs_calibration import (
    DEFAULT_INPUT_KEY,
    derived_key,
    parse_baselines,
    reference_enabled,
    reference_key,
    reference_limits,
    sensor_readings,
)


def _baseline_mode(config: dict, baselines: dict) -> bool:
    mode = str(config.get("calibration.mode") or "auto").strip().lower()
    if mode == "linear":
        return False
    return bool(baselines)


@register("sbs_thickness")
def side_by_side_thickness(config: dict, fields: dict) -> dict:
    config = config or {}
    readings = sensor_readings(fields, DEFAULT_INPUT_KEY)
    keys = [key for key, _ in readings]
    current = dict(readings)
    ref_key = reference_key(config, keys)
    baselines = parse_baselines(config)

    lower, upper, limit_active = bounds(
        config, "thickness.limit_active", "thickness.lower_limit_mm", "thickness.upper_limit_mm")
    target = _num(config.get("thickness.target_mm"))
    ref_lower, ref_upper, ref_active = reference_limits(config)

    ref_error = None
    if ref_key is not None and ref_key in current and ref_key in baselines:
        ref_error = round(current[ref_key] - baselines[ref_key], 6)

    result = {
        "thickness.actual_mm": None,
        "thickness.deviation_mm": None,
        "quality.status": None,
    }
    if reference_enabled(config) or ref_key is not None:
        result["reference.error_mm"] = ref_error
        result["quality.reference_status"] = None

    measurement = [(key, value) for key, value in readings if key != ref_key]

    sensor_thickness: dict[str, float] = {}
    if _baseline_mode(config, baselines):
        reference_thickness = _num(config.get("calibration.reference_thickness"), 0.0) or 0.0
        for key, value in measurement:
            baseline = baselines.get(key)
            if baseline is None:
                continue
            thickness = reference_thickness + (baseline - value)
            if ref_error is not None:
                thickness += ref_error
            sensor_thickness[key] = round(thickness, 6)
        actual = round(sum(sensor_thickness.values()) / len(sensor_thickness), 6) if sensor_thickness else None
    else:
        if not measurement:
            return result
        scale = _num(config.get("calibration.scale"), -1.0)
        offset = _num(config.get("calibration.offset"), 0.0)
        for key, value in measurement:
            sensor_thickness[key] = round(scale * value + offset, 6)
        actual = round(sum(sensor_thickness.values()) / len(sensor_thickness), 6)

    if actual is None:
        return result
    result["thickness.actual_mm"] = actual
    if target is not None:
        result["thickness.deviation_mm"] = round(actual - target, 6)

    if sensor_thickness:
        statuses = []
        for key, thickness in sensor_thickness.items():
            field = derived_key("thickness.sensor_mm", key)
            lo, hi, _ = sensor_bounds(config, field, lower, upper)
            result[field] = thickness
            sensor_status = status(thickness, lo, hi, limit_active)
            if sensor_status is not None:
                result[derived_key("quality.sensor_status", key)] = sensor_status
            statuses.append(status(thickness, lo, hi, limit_active))
        if any(item == "FAIL" for item in statuses):
            result["quality.status"] = "FAIL"
        elif any(item == "PASS" for item in statuses):
            result["quality.status"] = "PASS"
    else:
        result["quality.status"] = status(actual, lower, upper, limit_active)

    if "quality.reference_status" in result and ref_error is not None:
        result["quality.reference_status"] = status(ref_error, ref_lower, ref_upper, ref_active)
    return result
