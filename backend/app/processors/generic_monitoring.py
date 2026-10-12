"""BLOCK 4 — Generic Monitoring processor.

Any numeric signal: value, deviation from an optional target, PASS/FAIL against
optional limits. The fallback application every product supports.
"""
from __future__ import annotations

from .registry import register
from .rubber_thickness import _num


@register("generic_monitoring")
def generic_monitoring(config: dict, fields: dict) -> dict:
    value = _num(fields.get("signal.value"))
    target = _num(config.get("signal.target"))
    lower = _num(config.get("signal.lower_limit"))
    upper = _num(config.get("signal.upper_limit"))
    result = {"signal.actual": None, "signal.deviation": None, "quality.status": None}
    if value is None:
        return result
    result["signal.actual"] = round(value, 6)
    if target is not None:
        result["signal.deviation"] = round(value - target, 6)
    if lower is not None or upper is not None:
        ok = (lower is None or value >= lower) and (upper is None or value <= upper)
        result["quality.status"] = "PASS" if ok else "FAIL"
    return result
