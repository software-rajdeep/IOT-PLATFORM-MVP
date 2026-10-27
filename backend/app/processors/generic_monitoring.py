"""BLOCK 4 — Generic Monitoring processor.

Any numeric signal: value, deviation from an optional target, PASS/FAIL against
optional limits. The fallback application every product supports.
"""
from __future__ import annotations

from .registry import register
from ..services.limits import bounds, number as _num, status


@register("generic_monitoring")
def generic_monitoring(config: dict, fields: dict) -> dict:
    value = _num(fields.get("signal.value"))
    target = _num(config.get("signal.target"))
    lower, upper, active = bounds(
        config, "signal.limit_active", "signal.lower_limit", "signal.upper_limit")
    result = {"signal.actual": None, "signal.deviation": None, "quality.status": None}
    if value is None:
        return result
    result["signal.actual"] = round(value, 6)
    if target is not None:
        result["signal.deviation"] = round(value - target, 6)
    result["quality.status"] = status(value, lower, upper, active)
    return result
