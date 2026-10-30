"""BLOCK 4/5 — calibration (two-point linear fit)."""
from __future__ import annotations

import math
from typing import Optional

from ..db import fetch_one
from .sbs_calibration import baseline_complete

DEFAULT_VERIFY_TOLERANCE_MM = 0.02


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def calibration_complete(config: dict, method: Optional[str],
                         sensor_keys: Optional[list[str]] = None) -> bool:
    """Whether the saved calibration contains everything the template's method needs."""
    config = config or {}
    if config.get("calibration.calibrated_at") in (None, ""):
        return False
    if method == "baseline":
        return baseline_complete(config, sensor_keys or [])
    if method in ("gap", "auto_gap"):
        gap = config.get("calibration.gap_distance")
        return _finite(gap) and float(gap) > 0
    if method == "two_point":
        return (_finite(config.get("calibration.scale"))
                and _finite(config.get("calibration.offset")))
    return True


def verify_tolerance(config: dict, requested: Optional[float] = None) -> float:
    """Acceptance band for the verification piece.

    A per-call value wins; otherwise the instance's saved
    ``calibration.verify_tolerance_mm``; otherwise the historical 0.02 mm default.
    """
    value = requested
    if value is None:
        value = (config or {}).get("calibration.verify_tolerance_mm")
    if value in (None, ""):
        value = DEFAULT_VERIFY_TOLERANCE_MM
    try:
        value = abs(float(value))
    except (TypeError, ValueError):
        value = DEFAULT_VERIFY_TOLERANCE_MM
    if not math.isfinite(value):
        value = DEFAULT_VERIFY_TOLERANCE_MM
    return round(value, 6)


def compute_linear(ref1_thickness: float, ref1_displacement: float,
                   ref2_thickness: float, ref2_displacement: float) -> tuple[float, float]:
    """Return (scale, offset) for thickness = scale * displacement + offset."""
    d1, d2 = float(ref1_displacement), float(ref2_displacement)
    t1, t2 = float(ref1_thickness), float(ref2_thickness)
    if abs(d2 - d1) < 1e-9:
        raise ValueError("The two reference displacements are identical; use different references.")
    scale = (t2 - t1) / (d2 - d1)
    offset = t1 - scale * d1
    return round(scale, 6), round(offset, 6)


def current_displacement(device_id: str):
    row = fetch_one(
        "SELECT displacement_value_mm, ts FROM metrics.measurements "
        "WHERE device_id = %s ORDER BY ts DESC LIMIT 1",
        (device_id,),
    )
    if not row:
        return None
    return {
        "displacement.value_mm": float(row["displacement_value_mm"]) if row["displacement_value_mm"] is not None else None,
        "ts": row["ts"].isoformat() if row.get("ts") else None,
    }


def latest_fields(instance_id: str, device_id: str) -> tuple[dict, str | None]:
    """All mapped sensor fields from the newest measurement for one source."""
    row = fetch_one(
        "SELECT fields, ts FROM metrics.measurements "
        "WHERE instance_id = %s AND device_id = %s ORDER BY ts DESC LIMIT 1",
        (instance_id, device_id),
    )
    if not row:
        return {}, None
    fields = row.get("fields") or {}
    if isinstance(fields, str):
        import json
        try:
            fields = json.loads(fields)
        except (TypeError, ValueError):
            fields = {}
    return fields, row["ts"].isoformat() if row.get("ts") else None
