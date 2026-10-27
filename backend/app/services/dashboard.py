"""BLOCK 6 — dashboard data assembly, generic for every template.

Values are read from metrics.measurements.fields using the template's field roles
(primary / deviation / status / group / x_speed ...), so no widget or query is
specific to thickness. The widget LAYOUT is per instance (core.instance_dashboards),
defaulting to the template's dashboard metadata.
"""
from __future__ import annotations

import copy
import json
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from ..config import settings
from ..db import fetch_all, fetch_one
from . import alerts
from .devices import load_device, serialize_device
from .instances import _json, get_instance, instance_device_ids, load_config
from .templates import load_template


def _num(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


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


def _secs_since(ts) -> Optional[float]:
    if not ts or isinstance(ts, str):
        return None
    aware = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - aware).total_seconds(), 2)


# Which ui.show_* toggle hides which widget (the earlier run-mode settings keep working).
_UI_TOGGLE = {
    "current_value": "show_current", "target_deviation": "show_target", "statistics": "show_statistics",
    "pass_fail": "show_quality", "device_health": "show_health", "trend_band": "show_trend",
    "history_table": "show_history", "calibration_status": "show_calibration", "limits_editor": "show_limits",
}


def get_layout(instance_id: str, template: dict, config: Optional[dict] = None) -> dict:
    row = fetch_one("SELECT layout FROM core.instance_dashboards WHERE instance_id = %s", (instance_id,))
    if row:
        return _json(row["layout"], {"widgets": []})
    layout = json.loads(json.dumps(template.get("dashboard") or {"widgets": []}))
    ui = _ui(config or {})
    layout["widgets"] = [w for w in layout.get("widgets", []) if ui.get(_UI_TOGGLE.get(w.get("type"), ""), True)]
    for i, w in enumerate(layout["widgets"]):
        w.setdefault("id", f"w{i + 1}")
    return layout


def _health(device: dict) -> dict:
    age = _secs_since(device.get("last_seen"))
    return {
        "device_id": device["device_id"],
        "model": device.get("model_name"),
        "status": device.get("status"),
        "interface": device.get("interface"),
        "last_seen": device["last_seen"].isoformat() if device.get("last_seen") else None,
        "seconds_since": age,
        "communication": "ONLINE" if (age is not None and age <= 10) else "OFFLINE",
        "gateway_id": device.get("gateway_id"),
        "last_error": device.get("last_error"),
    }


# Short-lived response cache. Live pages refresh several times per second across
# tabs/devices; reusing one assembled bundle avoids repeating the same heavy
# queries. Entries are dropped as soon as any write request lands (see main.py).
_CACHE_MAX = 128
_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def invalidate_dashboards() -> None:
    with _cache_lock:
        _cache.clear()


def build_dashboard(instance_id: str, company_id: Optional[str] = None, window: int = 500,
                    device_id: Optional[str] = None) -> Optional[dict]:
    ttl = settings.dashboard_cache_ttl
    if ttl <= 0:
        return _build_dashboard(instance_id, company_id, window, device_id)

    key = (instance_id, company_id, device_id, window)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return copy.deepcopy(hit[1])

    data = _build_dashboard(instance_id, company_id, window, device_id)
    if data is not None:
        with _cache_lock:
            if len(_cache) >= _CACHE_MAX:
                _cache.clear()
            _cache[key] = (now, copy.deepcopy(data))
    return data


def _build_dashboard(instance_id: str, company_id: Optional[str] = None, window: int = 500,
                     device_id: Optional[str] = None) -> Optional[dict]:
    instance = get_instance(instance_id, company_id)
    if not instance:
        return None
    template = load_template(instance["template_id"])
    roles = template.get("field_roles") or {}
    config = load_config(instance_id)

    device_ids = [d for d in instance_device_ids(instance_id) if d]
    if device_id not in device_ids:
        device_id = instance.get("device_id")

    f_primary = roles.get("primary")
    f_dev = roles.get("deviation")
    f_status = roles.get("status") or "quality.status"
    f_group = roles.get("group")
    f_speed = roles.get("x_speed")

    latest = fetch_one(
        "SELECT ts, fields FROM metrics.measurements WHERE instance_id = %s AND device_id = %s "
        "ORDER BY ts DESC LIMIT 1", (instance_id, device_id))
    latest_fields = _json(latest["fields"], {}) if latest else {}

    stats = fetch_one(
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE fields->>%(st)s = 'PASS') AS pass_count,
               count(*) FILTER (WHERE fields->>%(st)s = 'FAIL') AS fail_count,
               min((fields->>%(p)s)::float) AS minimum, max((fields->>%(p)s)::float) AS maximum,
               avg((fields->>%(p)s)::float) AS average, stddev_samp((fields->>%(p)s)::float) AS std_dev
        FROM (SELECT fields FROM metrics.measurements
              WHERE instance_id = %(i)s AND device_id = %(d)s AND fields IS NOT NULL
              ORDER BY ts DESC LIMIT %(w)s) s
        """,
        {"st": f_status, "p": f_primary, "i": instance_id, "d": device_id, "w": window},
    ) or {}

    series_rows = fetch_all(
        "SELECT ts, fields FROM metrics.measurements WHERE instance_id = %s AND device_id = %s AND fields IS NOT NULL "
        "ORDER BY ts DESC LIMIT 200", (instance_id, device_id))
    series_rows.reverse()
    series = []
    for r in series_rows:
        f = _json(r["fields"], {})
        series.append({
            "ts": r["ts"].isoformat(),
            "value": _num(f.get(f_primary)),
            "deviation": _num(f.get(f_dev)) if f_dev else None,
            "status": f.get(f_status),
            "group": f.get(f_group) if f_group else None,
            "speed": _num(f.get(f_speed)) if f_speed else None,
            "input": _num(f.get(roles.get("input"))) if roles.get("input") else None,
        })

    groups = []
    if f_group:
        groups = fetch_all(
            """
            SELECT fields->>%(g)s AS grp, count(*) AS n, avg((fields->>%(p)s)::float) AS average,
                   stddev_samp((fields->>%(p)s)::float) AS std_dev,
                   count(*) FILTER (WHERE fields->>%(st)s = 'PASS') AS pass_count
            FROM (SELECT fields FROM metrics.measurements WHERE instance_id = %(i)s AND device_id = %(d)s
                  AND fields IS NOT NULL ORDER BY ts DESC LIMIT 2000) s
            WHERE fields ? %(g)s GROUP BY 1 ORDER BY 1
            """,
            {"g": f_group, "p": f_primary, "st": f_status, "i": instance_id, "d": device_id},
        )
        groups = [{"group": g["grp"], "count": int(g["n"]), "average": _num(g["average"]),
                   "std_dev": _num(g["std_dev"]),
                   "pass_percentage": round(int(g["pass_count"]) / int(g["n"]) * 100, 2) if g["n"] else None}
                  for g in groups]

    target, lower, upper = (_num(config.get(roles.get(k))) if roles.get(k) else None for k in ("target", "lower", "upper"))
    total = int(stats.get("total") or 0)
    pass_count, fail_count = int(stats.get("pass_count") or 0), int(stats.get("fail_count") or 0)
    mean, sigma = _num(stats.get("average")), _num(stats.get("std_dev"))
    capability = {"cp": None, "cpk": None}
    if sigma and sigma > 0 and lower is not None and upper is not None and mean is not None:
        capability = {"cp": round((upper - lower) / (6 * sigma), 3),
                      "cpk": round(min(upper - mean, mean - lower) / (3 * sigma), 3)}

    devices = []
    for did in device_ids:
        dev = load_device(did)
        if dev:
            summary = serialize_device(dev)
            h = _health(dev)
            devices.append({"device_id": did, "name": summary["name"], "ip": summary.get("ip"),
                            "model": (summary.get("model") or {}).get("model_name"),
                            "online": h["communication"] == "ONLINE", "selected": did == device_id})
    device = load_device(device_id) if device_id else None

    # Extra sensors: several source columns can feed one input, stored as "<input>.<n>".
    input_key = roles.get("input") or next(
        (c["key"] for c in (template.get("requires") or []) if c.get("required")), None)
    sensors = []
    if input_key:
        keys = sorted(
            (k for k in latest_fields
             if k == input_key or k.startswith(input_key + ".") or k.startswith(input_key + "_")),
            key=lambda k: (k != input_key, k),
        )
        per_key = {k: [] for k in keys}
        for r in series_rows:
            f = _json(r["fields"], {})
            for k in keys:
                per_key[k].append({"ts": r["ts"].isoformat(), "value": _num(f.get(k))})
        sensors = [{"key": k, "label": f"Sensor {i + 1}", "value": _num(latest_fields.get(k)),
                    "series": per_key[k]} for i, k in enumerate(keys)]

    return {
        "instance_id": instance_id,
        "name": instance["name"],
        "status": instance["status"],
        "template": {"template_id": template["template_id"], "name": template["name"],
                     "processor_key": template.get("processor_key"), "commissioning": template.get("commissioning")},
        "roles": roles,
        "label": roles.get("label") or "Value",
        "unit": roles.get("unit") or "",
        "active_device": device_id,
        "devices": devices,
        "layout": get_layout(instance_id, template, config),
        "ui": _ui(config),
        "config": {
            "target": target, "lower": lower, "upper": upper,
            "keys": {k: roles.get(k) for k in ("target", "lower", "upper")},
            "calibration.scale": _num(config.get("calibration.scale")),
            "calibration.offset": _num(config.get("calibration.offset")),
            "calibration.calibrated_at": config.get("calibration.calibrated_at"),
            "calibration.calibrated_by": config.get("calibration.calibrated_by"),
            "filter.window": _num(config.get("filter.window")),
        },
        "current": {
            "ts": latest["ts"].isoformat() if latest else None,
            "value": _num(latest_fields.get(f_primary)),
            "deviation": _num(latest_fields.get(f_dev)) if f_dev else None,
            "status": latest_fields.get(f_status),
            "input": _num(latest_fields.get(roles.get("input"))) if roles.get("input") else None,
            "group": latest_fields.get(f_group) if f_group else None,
            "speed": _num(latest_fields.get(f_speed)) if f_speed else None,
            "fields": latest_fields,
        },
        "statistics": {"minimum": _num(stats.get("minimum")), "maximum": _num(stats.get("maximum")),
                       "average": mean, "std_dev": sigma, "samples": total},
        "quality": {"pass_count": pass_count, "fail_count": fail_count,
                    "pass_percentage": round(pass_count / (pass_count + fail_count) * 100, 2)
                    if (pass_count + fail_count) else None},
        "capability": capability,
        "groups": groups,
        "sensors": sensors,
        "trend": series,
        "device_health": _health(device) if device else None,
        "alerts": alerts.list_events(instance_id, 10),
    }

