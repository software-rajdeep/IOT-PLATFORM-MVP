import pytest

from app.processors.generic_monitoring import generic_monitoring
from app.processors.rubber_thickness import rubber_thickness
from app.processors.side_by_side_thickness import side_by_side_thickness
from app.services.calibration import compute_linear
from app.services.filters import trimmed_mean


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
    assert result == {
        "thickness.actual_mm": 8.5,
        "thickness.deviation_mm": 0.0,
        "quality.status": "PASS",
    }


def test_side_by_side_thickness_supports_single_sensor_fallback():
    result = side_by_side_thickness(
        {"calibration.scale": -1, "calibration.offset": 10},
        {"displacement.value_mm": 1.5},
    )
    assert result["thickness.actual_mm"] == 8.5


def test_trimmed_mean_removes_single_spikes():
    assert trimmed_mean([0, 8.4, 8.5, 8.6, 100], 20) == pytest.approx(8.5)
    assert trimmed_mean([8.5], 45) == 8.5
