from app.services.compat import compatible, resolve_widgets, unlocks_by_input

CD22 = [{"kind": "displacement", "unit": "mm"}]

THICKNESS = {
    "requires": [{"key": "displacement.value_mm", "kind": "displacement", "required": True}],
    "optional": [{"key": "context.product_code", "label": "Product code", "kind": "text"},
                 {"key": "line.speed_m_min", "label": "Line speed", "kind": "speed"}],
    "field_roles": {"primary": "thickness.actual_mm", "status": "quality.status", "target": "thickness.target_mm",
                    "lower": "thickness.lower_limit_mm", "upper": "thickness.upper_limit_mm",
                    "group": "context.product_code", "x_speed": "line.speed_m_min",
                    "calibration": "calibration.scale", "unit": "mm"},
}
GENERIC = {
    "requires": [{"key": "signal.value", "kind": "number", "required": True}],
    "optional": [{"key": "context.product_code", "kind": "text"}],
    "field_roles": {"primary": "signal.actual", "group": "context.product_code"},
}
TEMPERATURE_APP = {"requires": [{"key": "t", "kind": "temperature", "required": True}]}

WIDGETS = [
    {"widget_type": "current_value", "name": "Current value", "requires": ["primary"]},
    {"widget_type": "capability", "name": "Capability", "requires": ["primary", "lower", "upper"]},
    {"widget_type": "group_summary", "name": "Per-product summary", "requires": ["primary", "group"]},
    {"widget_type": "speed_scatter", "name": "Value vs speed", "requires": ["primary", "x_speed"]},
    {"widget_type": "calibration_status", "name": "Calibration", "requires": ["calibration"]},
    {"widget_type": "device_health", "name": "Health", "requires": []},
]


def test_catalog_compatibility():
    assert compatible(CD22, THICKNESS)
    assert compatible(CD22, GENERIC)
    assert not compatible(CD22, TEMPERATURE_APP)
    assert not compatible([], GENERIC)


def _status(resolved):
    return {w["widget_type"]: w["status"] for w in resolved}


def test_optional_inputs_lock_and_unlock_widgets():
    base = _status(resolve_widgets(WIDGETS, THICKNESS, {"displacement.value_mm"}))
    assert base["current_value"] == "available"
    assert base["capability"] == "available"
    assert base["group_summary"] == "locked"
    assert base["speed_scatter"] == "locked"
    mapped = _status(resolve_widgets(WIDGETS, THICKNESS, {"displacement.value_mm", "context.product_code"}))
    assert mapped["group_summary"] == "available"
    assert mapped["speed_scatter"] == "locked"


def test_widgets_not_applicable_to_application():
    s = _status(resolve_widgets(WIDGETS, GENERIC, {"signal.value"}))
    assert s["calibration_status"] == "not_applicable"
    assert s["capability"] == "not_applicable"
    assert s["device_health"] == "available"


def test_locked_widget_says_what_to_map():
    locked = next(w for w in resolve_widgets(WIDGETS, THICKNESS, set()) if w["widget_type"] == "speed_scatter")
    assert locked["unlock_with"] == ["Line speed"]


def test_unlock_hints():
    hints = unlocks_by_input(WIDGETS, THICKNESS)
    assert hints["context.product_code"] == ["Per-product summary"]
    assert hints["line.speed_m_min"] == ["Value vs speed"]
