import json

import pytest

from app.processors.generic_monitoring import generic_monitoring
from app.processors.opposite_side_thickness import opposite_side_thickness
from app.processors.rubber_thickness import rubber_thickness
from app.processors.side_by_side_thickness import side_by_side_thickness
from app.services.calibration import compute_linear
from app.services.filters import trimmed_mean
from app.services.opposite_calibration import auto_gap_total, tolerance_limits


def test_two_point_calibration_drives_thickness_and_limits():
    scale, offset = compute_linear(10, 0, 8, 2)
    config = {"calibration.scale": scale, "calibration.offset": offset,
              "thickness.target_mm": 8.5, "thickness.lower_limit_mm": 8.3,
              "thickness.upper_limit_mm": 8.7}
    result = rubber_thickness(config, {"displacement.value_mm": 1.52})
    assert result == {"thickness.actual_mm": 8.48, "thickness.deviation_mm": -0.02, "quality.status": "PASS"}
    assert rubber_thickness(config, {"displacement.value_mm": 2})["quality.status"] == "FAIL"


def test_identical_calibration_references_are_rejected():
    with pytest.raises(ValueError):
        compute_linear(8, 1, 9, 1)


def test_generic_limits_are_inclusive_and_optional():
    config = {"signal.target": 5, "signal.lower_limit": 4, "signal.upper_limit": 6}
    for value in (4, 5, 6):
        assert generic_monitoring(config, {"signal.value": value})["quality.status"] == "PASS"
    assert generic_monitoring(config, {"signal.value": 6.1})["quality.status"] == "FAIL"
    assert generic_monitoring({}, {"signal.value": 5}) == {
        "signal.actual": 5, "signal.deviation": None, "quality.status": None}


def test_side_by_side_thickness_averages_all_sensor_readings():
    config = {
        "calibration.scale": -1,
        "calibration.offset": 10,
        "thickness.target_mm": 8.5,
        "thickness.lower_limit_mm": 8.3,
        "thickness.upper_limit_mm": 8.7,
    }
    result = side_by_side_thickness(config, {
        "displacement.value_mm": 1.4,
        "displacement.value_mm_b": 1.6,
    })
    assert result["thickness.actual_mm"] == 8.5
    assert result["thickness.deviation_mm"] == 0.0
    assert result["quality.status"] == "PASS"
    # Linear calibration also exposes each sensor's own thickness and status.
    assert result["thickness.sensor_mm"] == 8.6
    assert result["thickness.sensor_mm_b"] == 8.4
    assert result["quality.sensor_status"] == "PASS"
    assert result["quality.sensor_status_b"] == "PASS"


def test_side_by_side_thickness_supports_single_sensor_fallback():
    result = side_by_side_thickness(
        {"calibration.scale": -1, "calibration.offset": 10},
        {"displacement.value_mm": 1.5},
    )
    assert result["thickness.actual_mm"] == 8.5


def test_side_by_side_baseline_calibration_is_per_sensor():
    config = {
        "calibration.mode": "baseline",
        "calibration.reference_thickness": 8.0,
        "calibration.baselines": json.dumps({
            "displacement.value_mm": 2.0,
            "displacement.value_mm.2": 2.5,
        }),
        "thickness.target_mm": 8.0,
        "thickness.lower_limit_mm": 7.9,
        "thickness.upper_limit_mm": 8.1,
    }
    result = side_by_side_thickness(config, {
        "displacement.value_mm": 2.1,
        "displacement.value_mm.2": 2.4,
    })
    assert result["thickness.sensor_mm"] == pytest.approx(7.9)
    assert result["thickness.sensor_mm.2"] == pytest.approx(8.1)
    assert result["thickness.actual_mm"] == pytest.approx(8.0)
    assert result["quality.status"] == "PASS"


def test_side_by_side_reference_error_corrects_thickness_and_has_its_own_limit():
    config = {
        "calibration.mode": "baseline",
        "calibration.reference_thickness": 8.0,
        "calibration.baselines": json.dumps({
            "displacement.value_mm": 2.0,
            "displacement.value_mm.2": 1.0,
        }),
        "reference.enabled": True,
        "reference.limit_active": True,
        "reference.lower_limit_mm": -0.1,
        "reference.upper_limit_mm": 0.1,
        "thickness.lower_limit_mm": 7.9,
        "thickness.upper_limit_mm": 8.1,
    }
    result = side_by_side_thickness(config, {
        "displacement.value_mm": 2.1,
        "displacement.value_mm.2": 1.2,
    })
    assert result["reference.error_mm"] == pytest.approx(0.2)
    assert result["thickness.sensor_mm"] == pytest.approx(8.1)
    assert result["thickness.actual_mm"] == pytest.approx(8.1)
    assert result["quality.status"] == "PASS"
    assert result["quality.reference_status"] == "FAIL"


def test_side_by_side_any_sensor_out_of_limit_fails_the_reading():
    config = {
        "calibration.mode": "baseline",
        "calibration.reference_thickness": 8.0,
        "calibration.baselines": json.dumps({
            "displacement.value_mm": 2.0,
            "displacement.value_mm.2": 2.0,
        }),
        "thickness.lower_limit_mm": 7.9,
        "thickness.upper_limit_mm": 8.1,
    }
    result = side_by_side_thickness(config, {
        "displacement.value_mm": 2.0,
        "displacement.value_mm.2": 2.5,
    })
    assert result["quality.status"] == "FAIL"


def test_opposite_manual_gap_matches_old_formula():
    config = {
        "calibration.mode": "gap",
        "calibration.gap_distance": 100,
        "thickness.target_mm": 26.5,
        "thickness.lower_limit_mm": 26.4,
        "thickness.upper_limit_mm": 26.6,
    }
    result = opposite_side_thickness(config, {
        "displacement.value_mm": 1.5,
        "displacement.value_mm.2": 2.0,
    })
    assert result["thickness.actual_mm"] == pytest.approx(26.5)
    assert result["thickness.deviation_mm"] == pytest.approx(0.0)
    assert result["opposite.gap_mm"] == pytest.approx(100)
    assert result["opposite.distance_a_mm"] == pytest.approx(36.5)
    assert result["opposite.distance_b_mm"] == pytest.approx(37.0)
    assert result["quality.status"] == "PASS"

    fail = opposite_side_thickness({**config, "thickness.upper_limit_mm": 26.4}, {
        "displacement.value_mm": 1.5,
        "displacement.value_mm.2": 2.0,
    })
    assert fail["quality.status"] == "FAIL"


def test_opposite_auto_gap_and_tolerance_match_old_formula():
    gap = auto_gap_total(1.5, 2.0, 5.0)
    lower, upper = tolerance_limits(5.0, 0.2)
    assert gap == pytest.approx(78.5)
    assert (lower, upper) == (pytest.approx(4.8), pytest.approx(5.2))
    result = opposite_side_thickness({
        "calibration.mode": "auto_gap",
        "calibration.gap_distance": gap,
        "calibration.object_thickness": 5.0,
        "thickness.target_mm": 5.0,
        "thickness.lower_limit_mm": lower,
        "thickness.upper_limit_mm": upper,
    }, {"displacement.value_mm": 1.5, "displacement.value_mm.2": 2.0})
    assert result["thickness.actual_mm"] == pytest.approx(5.0)
    assert result["quality.status"] == "PASS"


def test_opposite_calibration_baseline_fallback():
    result = opposite_side_thickness({
        "calibration.mode": "baseline",
        "calibration.reference_thickness": 8.0,
        "calibration.baselines": json.dumps({
            "displacement.value_mm": 2.0,
            "displacement.value_mm.2": 2.5,
        }),
    }, {"displacement.value_mm": 2.1, "displacement.value_mm.2": 2.4})
    assert result["thickness.actual_mm"] == pytest.approx(8.0)


def test_opposite_requires_two_sensor_readings():
    assert opposite_side_thickness(
        {"calibration.gap_distance": 100}, {"displacement.value_mm": 1.5}
    )["thickness.actual_mm"] is None


def test_limits_can_be_open_ended_or_switched_off():
    config = {
        "calibration.mode": "baseline",
        "calibration.reference_thickness": 8.0,
        "calibration.baselines": json.dumps({
            "displacement.value_mm": 2.0,
            "displacement.value_mm.2": 2.0,
        }),
        "thickness.lower_limit_mm": 7.9,
    }
    result = side_by_side_thickness(config, {
        "displacement.value_mm": 2.2,
        "displacement.value_mm.2": 2.0,
    })
    assert result["quality.status"] == "FAIL"
    off = side_by_side_thickness({**config, "thickness.limit_active": False}, {
        "displacement.value_mm": 2.2,
        "displacement.value_mm.2": 2.0,
    })
    assert off["quality.status"] is None


def test_reference_limit_supports_a_single_bound():
    result = side_by_side_thickness({
        "calibration.mode": "baseline",
        "calibration.reference_thickness": 8.0,
        "calibration.baselines": json.dumps({
            "displacement.value_mm": 2.0,
            "displacement.value_mm.2": 1.0,
        }),
        "reference.enabled": True,
        "reference.limit_active": True,
        "reference.upper_limit_mm": 0.1,
    }, {"displacement.value_mm": 2.0, "displacement.value_mm.2": 1.2})
    assert result["reference.error_mm"] == pytest.approx(0.2)
    assert result["quality.reference_status"] == "FAIL"


def test_trimmed_mean_removes_single_spikes():
    assert trimmed_mean([0, 8.4, 8.5, 8.6, 100], 20) == pytest.approx(8.5)
    assert trimmed_mean([8.5], 45) == 8.5
