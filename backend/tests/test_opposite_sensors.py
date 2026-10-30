"""Opposite-side is a two-sensor layout: A (input) and B (input.2).

A third mapped sensor column ("Sensor 3") must never reach calibration or the
dashboard, even if an older mapping still contains one.
"""
from app.services.mappings import limit_extra_sensors
from app.services.opposite_calibration import limit_readings

OPPOSITE = {"sensor_layout": "opposite"}
SIDE_BY_SIDE = {"sensor_layout": "side_by_side"}


def definition(*targets):
    return {"fields": [{"target": t, "path": t} for t in targets]}


def test_limit_extra_sensors_keeps_a_and_b():
    limited = limit_extra_sensors(definition(
        "displacement.value_mm",
        "displacement.value_mm.2",
        "displacement.value_mm.3",
    ), OPPOSITE, "displacement.value_mm")

    assert [f["target"] for f in limited["fields"]] == [
        "displacement.value_mm",
        "displacement.value_mm.2",
    ]


def test_limit_extra_sensors_keeps_unrelated_fields_and_timestamp():
    original = {
        **definition("displacement.value_mm", "displacement.value_mm.2",
                     "displacement.value_mm.3", "context.product_code"),
        "timestamp": {"path": "ts"},
    }

    limited = limit_extra_sensors(original, OPPOSITE, "displacement.value_mm")

    assert [f["target"] for f in limited["fields"]] == [
        "displacement.value_mm",
        "context.product_code",
        "displacement.value_mm.2",
    ]
    assert limited["timestamp"] == {"path": "ts"}


def test_limit_extra_sensors_leaves_other_layouts_untouched():
    original = definition("displacement.value_mm", "displacement.value_mm.2",
                          "displacement.value_mm.3")

    assert limit_extra_sensors(original, SIDE_BY_SIDE, "displacement.value_mm") == original
    assert limit_extra_sensors(original, {}, "displacement.value_mm") == original


def test_limit_readings_keeps_only_the_pair():
    readings = [
        ("displacement.value_mm", 1.0),
        ("displacement.value_mm.2", 2.0),
        ("displacement.value_mm.3", 3.0),
    ]

    assert limit_readings(readings) == readings[:2]
    assert limit_readings(None) == []
    assert limit_readings([]) == []
