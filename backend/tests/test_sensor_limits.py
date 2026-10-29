"""Per-sensor limits: one sensor can carry its own bounds without touching the others.

The map lives in ``event.sensor_limits`` (the key the incident engine already reads) so the
dashboard PASS/FAIL, the limits card and the alerts all resolve the same numbers.
"""
import json

from app.processors.side_by_side_thickness import side_by_side_thickness
from app.services.limits import bounds, sensor_bounds, sensor_overrides


def config(**overrides):
    base = {
        "calibration.mode": "linear",
        "calibration.scale": "-1.0",
        "calibration.offset": "10.0",
        "thickness.limit_active": "True",
        "thickness.lower_limit_mm": "8.3",
        "thickness.upper_limit_mm": "8.7",
    }
    base.update(overrides)
    return base


def test_sensor_overrides_accepts_dict_and_json():
    assert sensor_overrides({"event.sensor_limits": {"a": {"lower": 1}}}) == {"a": {"lower": 1}}
    assert sensor_overrides({"event.sensor_limits": json.dumps({"a": {"upper": 2}})}) == {"a": {"upper": 2}}
    assert sensor_overrides({"event.sensor_limits": "not json"}) == {}
    assert sensor_overrides({}) == {}


def test_sensor_bounds_inherits_and_partially_overrides():
    cfg = config(**{"event.sensor_limits": json.dumps({
        "thickness.sensor_mm.2": {"lower": 1.0},
    })})
    lower, upper, active = bounds(cfg, "thickness.limit_active",
                                 "thickness.lower_limit_mm", "thickness.upper_limit_mm")

    assert sensor_bounds(cfg, "thickness.sensor_mm", lower, upper) == (8.3, 8.7, False)
    # Only the lower bound is overridden; the upper one keeps the instance limit.
    assert sensor_bounds(cfg, "thickness.sensor_mm.2", lower, upper) == (1.0, 8.7, True)
    assert sensor_bounds(cfg, None, lower, upper) == (8.3, 8.7, False)


def test_processor_status_is_resolved_per_sensor():
    cfg = config(**{"event.sensor_limits": json.dumps({
        "thickness.sensor_mm.2": {"upper": 8.4},
    })})
    fields = {"displacement.value_mm": 1.5, "displacement.value_mm.2": 1.5}

    result = side_by_side_thickness(cfg, fields)

    # Same thickness on both sensors, different bounds: 8.5 fails only where the
    # per-sensor upper limit of 8.4 applies.
    assert result["thickness.sensor_mm"] == 8.5
    assert result["thickness.sensor_mm.2"] == 8.5
    assert result["quality.sensor_status"] == "PASS"
    assert result["quality.sensor_status.2"] == "FAIL"
    assert result["quality.status"] == "FAIL"
