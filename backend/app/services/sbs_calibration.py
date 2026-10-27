"""Side-by-side sensor helpers: sensor columns, baselines and reference drift.

These functions are pure (no database access) and are shared by the thickness
processor and the calibration API so both agree on which mapped fields are
sensor readings and which of them is the reference sensor.
"""
from __future__ import annotations

import json
import re
from typing import Optional

DEFAULT_INPUT_KEY = "displacement.value_mm"


def _num(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _sort_key(key: str, input_key: str):
    suffix = key[len(input_key):]
    match = re.match(r"^[._](\d+)$", suffix)
    if not suffix:
        return (0, 0, "")
    if match:
        return (1, int(match.group(1)), suffix)
    return (2, 0, suffix)


def sensor_keys(fields: dict, input_key: str = DEFAULT_INPUT_KEY) -> list[str]:
    """All mapped sensor columns for one application input, base sensor first.

    Extra sensors are stored as ``displacement.value_mm.2`` (mapping UI) or
    ``displacement.value_mm_b`` (older payloads); both forms are accepted.
    """
    keys = [
        key
        for key in (fields or {})
        if key == input_key or key.startswith(input_key + ".") or key.startswith(input_key + "_")
    ]
    return sorted(keys, key=lambda key: _sort_key(key, input_key))


def sensor_readings(fields: dict, input_key: str = DEFAULT_INPUT_KEY) -> list[tuple[str, float]]:
    """``[(sensor_key, numeric_value)]`` for every mapped sensor that has a value."""
    readings = []
    for key in sensor_keys(fields, input_key):
        value = _num((fields or {}).get(key))
        if value is not None:
            readings.append((key, value))
    return readings


def suffix(key: str, input_key: str = DEFAULT_INPUT_KEY) -> str:
    return key[len(input_key):] if key.startswith(input_key) else ""


def derived_key(base: str, key: str, input_key: str = DEFAULT_INPUT_KEY) -> str:
    """``thickness.sensor_mm`` + the sensor key suffix (``thickness.sensor_mm.2``)."""
    return base + suffix(key, input_key)


def parse_baselines(config: dict) -> dict[str, float]:
    """Baselines captured during calibration, keyed by the sensor's mapped field."""
    raw = (config or {}).get("calibration.baselines")
    parsed = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
    baselines = {}
    if isinstance(parsed, dict):
        for key, value in parsed.items():
            number = _num(value)
            if number is not None:
                baselines[str(key)] = number
    prefix = "calibration.baseline."
    for key, value in (config or {}).items():
        if key.startswith(prefix):
            number = _num(value)
            if number is not None:
                baselines[key[len(prefix):]] = number
    return baselines


def reference_key(config: dict, keys: list[str]) -> Optional[str]:
    """Which sensor column is the drifting reference sensor.

    ``reference.sensor_key`` wins when it names a mapped column. Otherwise, when
    ``reference.enabled`` is on, the last mapped sensor is the reference (the old
    app always listed the reference sensor last).
    """
    config = config or {}
    explicit = str(config.get("reference.sensor_key") or "").strip()
    if explicit and explicit in keys:
        return explicit
    if _bool(config.get("reference.enabled"), False) and len(keys) > 1:
        return keys[-1]
    return None


def reference_enabled(config: dict) -> bool:
    return _bool((config or {}).get("reference.enabled"), False)


def reference_limits(config: dict) -> tuple[Optional[float], Optional[float], bool]:
    config = config or {}
    active = _bool(config.get("reference.limit_active"), False)
    return (
        _num(config.get("reference.lower_limit_mm")),
        _num(config.get("reference.upper_limit_mm")),
        active,
    )
