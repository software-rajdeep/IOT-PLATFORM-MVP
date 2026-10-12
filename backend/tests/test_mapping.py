import pytest

from app.services.mapping import (
    convert_unit, extract, flatten, map_payload, preset_definition, suggest, unmapped_required, _MISSING,
)

UNITS = {
    "mm": {"dimension": "length", "to_base_factor": 1, "to_base_offset": 0},
    "um": {"dimension": "length", "to_base_factor": 0.001, "to_base_offset": 0},
    "in": {"dimension": "length", "to_base_factor": 25.4, "to_base_offset": 0},
    "m/min": {"dimension": "speed", "to_base_factor": 1, "to_base_offset": 0},
}

CONTRACT = [
    {"key": "displacement.value_mm", "kind": "displacement", "unit": "mm", "required": True, "valid_min": -60, "valid_max": 60},
    {"key": "context.product_code", "kind": "text"},
    {"key": "line.speed_m_min", "kind": "speed", "unit": "m/min"},
]

CD22_PROFILE = {"preset_mapping": {"fields": [{"signal": "displacement", "path": "measurement_raw",
                "transforms": [{"type": "cast", "to": "decimal"}, {"type": "scale", "factor": "@model.resolution"}]}]}}


def test_extract_paths():
    payload = {"laser": {"value_um": 1502}, "items": [{"v": 3}], "a": None}
    assert extract(payload, "laser.value_um") == 1502
    assert extract(payload, "$.laser.value_um") == 1502
    assert extract(payload, "items[0].v") == 3
    assert extract(payload, "a") is None
    assert extract(payload, "nope.x") is _MISSING


def test_flatten_lists_leaves_with_types():
    paths = {p["path"]: p["type"] for p in flatten({"ts": "2026-10-12T10:00:00", "laser": {"value_um": "1502"}, "prod": "RUB-3"})}
    assert paths == {"ts": "datetime_text", "laser.value_um": "numeric_text", "prod": "text"}


def test_unit_conversion():
    assert convert_unit(1502, "um", "mm", UNITS) == pytest.approx(1.502)
    assert convert_unit(1, "in", "mm", UNITS) == pytest.approx(25.4)
    with pytest.raises(Exception):
        convert_unit(1, "mm", "m/min", UNITS)


def test_cd22_preset_matches_signal_and_inlines_resolution():
    definition = preset_definition(CD22_PROFILE, {"resolution": 0.01}, CONTRACT)
    assert definition["fields"][0]["target"] == "displacement.value_mm"
    values, _, errors = map_payload({"measurement_raw": 151}, definition, CONTRACT, UNITS)
    assert errors == []
    assert values["displacement.value_mm"] == pytest.approx(1.51)


def test_preset_falls_back_to_generic_number_input():
    generic = [{"key": "signal.value", "kind": "number", "required": True}]
    definition = preset_definition(CD22_PROFILE, {"resolution": 0.01}, generic)
    values, _, errors = map_payload({"measurement_raw": 200}, definition, generic, UNITS)
    assert errors == [] and values["signal.value"] == pytest.approx(2.0)


def test_customer_payload_with_units_and_optional_fields():
    definition = {
        "fields": [
            {"target": "displacement.value_mm", "path": "laser.value_um",
             "transforms": [{"type": "cast", "to": "decimal"}, {"type": "unit", "from": "um", "to": "mm"}]},
            {"target": "context.product_code", "path": "prod"},
        ],
        "timestamp": {"path": "ts", "format": "auto", "tz": "Asia/Kolkata"},
    }
    payload = {"machine": "EXT-07", "ts": "2026-10-12 10:00:00", "laser": {"value_um": "1498"}, "prod": "RUB-EXTR-3"}
    values, context, errors = map_payload(payload, definition, CONTRACT, UNITS)
    assert errors == []
    assert values == {"displacement.value_mm": pytest.approx(1.498), "context.product_code": "RUB-EXTR-3"}
    assert context["timestamp"].startswith("2026-10-12T10:00:00+05:30")


def test_missing_required_field_is_an_error():
    definition = {"fields": [{"target": "displacement.value_mm", "path": "laser.value_um"}]}
    values, _, errors = map_payload({"other": 1}, definition, CONTRACT, UNITS)
    assert values == {}
    assert errors[0]["code"] == "MISSING_FIELD"


def test_out_of_template_range_is_an_error():
    definition = {"fields": [{"target": "displacement.value_mm", "path": "v"}]}
    _, _, errors = map_payload({"v": 999}, definition, CONTRACT, UNITS)
    assert errors[0]["code"] == "OUT_OF_RANGE"


def test_bad_cast_is_an_error():
    definition = {"fields": [{"target": "displacement.value_mm", "path": "v", "transforms": [{"type": "cast", "to": "decimal"}]}]}
    _, _, errors = map_payload({"v": "abc"}, definition, CONTRACT, UNITS)
    assert errors[0]["code"] == "CAST_FAILED"


def test_default_fills_missing_optional():
    definition = {"fields": [
        {"target": "displacement.value_mm", "path": "v"},
        {"target": "context.product_code", "path": "prod", "transforms": [{"type": "default", "value": "UNKNOWN"}]},
    ]}
    values, _, errors = map_payload({"v": 1.2}, definition, CONTRACT, UNITS)
    assert errors == [] and values["context.product_code"] == "UNKNOWN"


def test_unmapped_required():
    assert unmapped_required({"fields": []}, CONTRACT) == ["displacement.value_mm"]
    assert unmapped_required({"fields": [{"target": "displacement.value_mm", "path": "x"}]}, CONTRACT) == []


def test_suggest_picks_sensible_paths():
    paths = flatten({"machine": "EXT-07", "ts": "2026-10-12T10:00:00+05:30",
                     "laser": {"value_um": 1502}, "prod": "RUB-3", "speed_mpm": 12.5})
    s = suggest(paths, CONTRACT)
    assert s["displacement.value_mm"]["path"] == "laser.value_um"
    assert s["context.product_code"]["path"] == "prod"
    assert s["line.speed_m_min"]["path"] == "speed_mpm"
    assert s["timestamp"]["path"] == "ts"
