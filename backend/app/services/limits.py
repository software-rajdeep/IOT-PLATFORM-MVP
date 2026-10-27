"""Shared tolerance / limit evaluation.

The field-proven thickness app lets each bound be optional and keeps a separate
on/off flag for a limit group. These helpers implement exactly that, so every
processor (side-by-side, opposite, single-sensor, generic) behaves the same.
"""
from __future__ import annotations

from typing import Optional


def number(value, default=None) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def flag(value, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def bounds(config: dict, active_key: str, lower_key: str, upper_key: str,
           default_active: bool = True) -> tuple[Optional[float], Optional[float], bool]:
    """Return ``(lower, upper, active)`` for one limit group.

    Missing bounds stay ``None`` (open-ended), and ``active`` defaults to
    "true when any bound is configured" so older instances keep working.
    """
    config = config or {}
    lower = number(config.get(lower_key))
    upper = number(config.get(upper_key))
    raw_active = config.get(active_key)
    active = default_active if raw_active in (None, "") else flag(raw_active, default_active)
    if lower is None and upper is None:
        active = False
    return lower, upper, active


def inside(value: float, lower: Optional[float], upper: Optional[float]) -> bool:
    return (lower is None or value >= lower) and (upper is None or value <= upper)


def status(value, lower: Optional[float], upper: Optional[float], active: bool = True):
    """PASS/FAIL for a value, or ``None`` when the limit group is off/empty."""
    if value is None or not active or (lower is None and upper is None):
        return None
    return "PASS" if inside(float(value), lower, upper) else "FAIL"
