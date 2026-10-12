"""BLOCK 6 — dashboard data assembly (current, target, trend, stats, quality, health).

The widget LAYOUT comes from the template's dashboard metadata; this function
only supplies the values for the widgets.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..db import fetch_all, fetch_one
from .devices import load_device, serialize_device
from .instances import _json, get_instance, instance_device_ids, load_config


def _num(value):
    return float(value) if value is not None else None


# Run-mode sections the customer can show/hide (config key ui.<name>, "0" = off).
UI_KEYS = (
    "show_device_switcher", "show_live_status", "show_calibration", "show_limits",
    "show_current", "show_target", "show_statistics", "show_quality", "show_health",
    "show_trend", "show_history",
)


def _ui(config: dict) -> dict:
    ui = {key: str(config.get(f"ui.{key}")) != "0" for key in UI_KEYS}
    ui["graph_type"] = str(config.get("ui.graph_type") or "line").strip() or "line"
    return ui


def _secs_since(iso_ts) -> Optional[float]:
    if not iso_ts:
        return None
    if isinstance(iso_ts, str):
        return None
    aware = iso_ts if iso_ts.tzinfo else iso_ts.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - aware).total_seconds(), 2)


def build_dashboard(instance_id: str, company_id: Optional[str] = None, window: int = 500,
                    device_id: Optional[str] = None) -> Optional[dict]:
    instance = get_instance(instance_id, company_id)
    if not instance:
        return None
    config = load_config(instance_id)

    device_ids = instance_device_ids(instance_id)
    if device_id not in device_ids:
        device_id = instance.get("device_id")
    device_ids = [did for did in device_ids if did]

    latest = fetch_one(
        "SELECT ts, displacement_value_mm, thickness_mm, deviation_mm, quality_status "
        "FROM metrics.measurements WHERE instance_id = %s AND device_id = %s ORDER BY ts DESC LIMIT 1",
        (instance_id, device_id),
    )
    stats = fetch_one(
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE quality_status = 'PASS') AS pass_count,
               count(*) FILTER (WHERE quality_status = 'FAIL') AS fail_count,
               min(thickness_mm) AS minimum, max(thickness_mm) AS maximum,
               avg(thickness_mm) AS average, stddev_samp(thickness_mm) AS std_dev
        FROM (
            SELECT thickness_mm, quality_status FROM metrics.measurements
            WHERE instance_id = %s AND device_id = %s ORDER BY ts DESC LIMIT %s
        ) s
        """,
        (instance_id, device_id, window),
    )
    trend_rows = fetch_all(
        "SELECT ts, thickness_mm, deviation_mm, quality_status FROM metrics.measurements "
        "WHERE instance_id = %s AND device_id = %s ORDER BY ts DESC LIMIT %s",
        (instance_id, device_id, 200),
    )
    trend_rows.reverse()

    total = int(stats["total"]) if stats and stats["total"] is not None else 0
    pass_count = int(stats["pass_count"]) if stats and stats["pass_count"] is not None else 0
    fail_count = int(stats["fail_count"]) if stats and stats["fail_count"] is not None else 0

    def _summarize(did: str) -> Optional[dict]:
        dev = load_device(did)
        if not dev:
            return None
        summary = serialize_device(dev)
        last_seen = dev.get("last_seen")
        age = _secs_since(last_seen)
        summary["online"] = age is not None and age <= 5
        summary["device_id"] = did
        return summary

    devices = [s for s in (_summarize(did) for did in device_ids) if s]

    device = load_device(device_id)
    health = None
    if device:
        last_seen = device.get("last_seen")
        age = _secs_since(last_seen)
        health = {
            "device_id": device["device_id"],
            "model": device.get("model_name"),
            "status": device.get("status"),
            "last_seen": last_seen.isoformat() if last_seen else None,
            "seconds_since": age,
            "communication": "ONLINE" if (age is not None and age <= 5) else "OFFLINE",
            "gateway_id": device.get("gateway_id"),
        }

    return {
        "instance_id": instance_id,
        "name": instance["name"],
        "status": instance["status"],
        "active_device": device_id,
        "devices": [
            {
                "device_id": d["device_id"],
                "name": d.get("name") or d["device_id"],
                "ip": d.get("ip"),
                "model": d.get("model", {}).get("model_name"),
                "online": d.get("online", False),
                "selected": d["device_id"] == device_id,
            }
            for d in devices
        ],
        "layout": _json(instance.get("dashboard"), {}),
        "ui": _ui(config),
        "config": {
            "thickness.target_mm": _num(config.get("thickness.target_mm")),
            "thickness.lower_limit_mm": _num(config.get("thickness.lower_limit_mm")),
            "thickness.upper_limit_mm": _num(config.get("thickness.upper_limit_mm")),
            "calibration.scale": _num(config.get("calibration.scale")),
            "calibration.offset": _num(config.get("calibration.offset")),
            "calibration.calibrated_at": config.get("calibration.calibrated_at"),
        },
        "current": {
            "ts": latest["ts"].isoformat() if latest and latest.get("ts") else None,
            "displacement.value_mm": _num(latest["displacement_value_mm"]) if latest else None,
            "thickness.actual_mm": _num(latest["thickness_mm"]) if latest else None,
            "thickness.deviation_mm": _num(latest["deviation_mm"]) if latest else None,
            "quality.status": latest["quality_status"] if latest else None,
        },
        "statistics": {
            "minimum": _num(stats["minimum"]) if stats else None,
            "maximum": _num(stats["maximum"]) if stats else None,
            "average": _num(stats["average"]) if stats else None,
            "std_dev": _num(stats["std_dev"]) if stats else None,
            "samples": total,
        },
        "quality": {
            "pass_count": pass_count,
            "fail_count": fail_count,
            "pass_percentage": round(pass_count / total * 100, 2) if total else None,
        },
        "trend": [
            {
                "ts": r["ts"].isoformat() if r.get("ts") else None,
                "thickness.actual_mm": _num(r["thickness_mm"]),
                "thickness.deviation_mm": _num(r["deviation_mm"]),
                "quality.status": r["quality_status"],
            }
            for r in trend_rows
        ],
        "device_health": health,
    }
