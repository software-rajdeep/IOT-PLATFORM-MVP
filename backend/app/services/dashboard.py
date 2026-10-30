"""BLOCK 6 — dashboard data assembly, generic for every template.

Values are read from metrics.measurements.fields using the template's field roles
(primary / deviation / status / group / x_speed ...), so no widget or query is
specific to thickness. The widget LAYOUT is per instance (core.instance_dashboards),
defaulting to the template's dashboard metadata.
"""
from __future__ import annotations

import copy
import json
import math
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from ..config import settings
from ..db import fetch_all, fetch_one
from . import alerts
from .devices import load_device, serialize_device
from .instances import _json, get_instance, instance_device_ids, load_config
from .limits import bounds, sensor_bounds, sensor_overrides
from .mappings import active_mapping
from .opposite_calibration import (
    MAX_SENSORS,
    ZERO_OFFSET_MM,
    auto_gap_active,
    gap_distance,
    object_thickness,
    tolerance,
)
from .sbs_calibration import parse_baselines, reference_enabled, reference_key, sensor_keys
from .templates import load_template


def _num(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


# Only values that look numeric are cast in SQL. A blank or textual value in the
# primary field must not abort the whole dashboard bundle (the AI analytics
# service guards its casts the same way).
_NUMERIC_RE = r"^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$"


def capability_indices(mean: Optional[float], sigma: Optional[float],
                       lower: Optional[float], upper: Optional[float]) -> dict:
    """Cp / Cpk from the window mean and sample standard deviation.

    ``Cp = (USL - LSL) / 6σ`` and ``Cpk = min(USL - µ, µ - LSL) / 3σ``. A missing
    or zero σ, missing limits or inverted limits return ``None`` instead of a
    division by zero / a meaningless negative spread.
    """
    if (mean is None or sigma is None or not math.isfinite(sigma) or sigma <= 0
            or lower is None or upper is None or upper <= lower):
        return {"cp": None, "cpk": None}
    return {"cp": round((upper - lower) / (6 * sigma), 3),
            "cpk": round(min(upper - mean, mean - lower) / (3 * sigma), 3)}


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


def _sensor_thickness_series(processor_key: str, config: dict, keys: list[str],
                             per_key: dict, ref_key: Optional[str]) -> dict:
    """Per-sensor thickness trend for side-by-side calibration.

    Mirrors ``processors.side_by_side_thickness`` so each sensor graph uses the
    same maths as its live value: ``scale * input + offset`` for linear
    calibration, or ``reference + (baseline - current) + reference drift`` for
    baseline calibration. Other processors keep raw series only.
    """
    if processor_key != "sbs_thickness":
        return {}
    mode = str((config or {}).get("calibration.mode") or "auto").strip().lower()
    baselines = parse_baselines(config)
    if mode == "linear" or not baselines:
        scale = _num((config or {}).get("calibration.scale"))
        scale = -1.0 if scale is None else scale
        offset = _num((config or {}).get("calibration.offset")) or 0.0
        out: dict[str, list] = {}
        for key in keys:
            if key == ref_key:
                continue
            out[key] = [
                {"ts": point["ts"], "value": round(scale * point["value"] + offset, 6)}
                for point in per_key.get(key, []) if point["value"] is not None
            ]
        return out
    reference_thickness = _num((config or {}).get("calibration.reference_thickness")) or 0.0
    ref_base = baselines.get(ref_key) if ref_key else None
    ref_values: dict[str, float] = {}
    if ref_key:
        for point in per_key.get(ref_key, []):
            if point["value"] is not None:
                ref_values[point["ts"]] = point["value"]
    out: dict[str, list] = {}
    for key in keys:
        if key == ref_key:
            continue
        baseline = baselines.get(key)
        if baseline is None:
            continue
        series = []
        for point in per_key.get(key, []):
            value = point["value"]
            if value is None:
                continue
            drift = 0.0
            if ref_key and ref_base is not None:
                ref_value = ref_values.get(point["ts"])
                if ref_value is not None:
                    drift = ref_value - ref_base
            series.append({"ts": point["ts"],
                           "value": round(reference_thickness + (baseline - value) + drift, 6)})
        out[key] = series
    return out


def _health(device: dict) -> dict:
    age = _secs_since(device.get("last_seen"))
    # A replay source walks historical rows, so its next reading can be a few
    # seconds apart while the source runtime works through other devices.
    params = _json(device.get("connection_parameters"), {})
    replay = bool(((params.get("database") or {}).get("replay") or {}).get("enabled"))
    online_within = 60 if replay else 10
    return {
        "device_id": device["device_id"],
        "model": device.get("model_name"),
        "status": device.get("status"),
        "interface": device.get("interface"),
        "last_seen": device["last_seen"].isoformat() if device.get("last_seen") else None,
        "seconds_since": age,
        "communication": "ONLINE" if (age is not None and age <= online_within) else "OFFLINE",
        "replay": replay,
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
    processor_key = template.get("processor_key") or ""
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
        SELECT count(*) AS readings,
               count(v) AS samples,
               count(*) FILTER (WHERE v IS NOT NULL AND status = 'PASS') AS pass_count,
               count(*) FILTER (WHERE v IS NOT NULL AND status = 'FAIL') AS fail_count,
               min(v) AS minimum, max(v) AS maximum, avg(v) AS average, stddev_samp(v) AS std_dev
        FROM (SELECT CASE WHEN (fields->>%(p)s) ~ %(num)s THEN (fields->>%(p)s)::float END AS v,
                     fields->>%(st)s AS status
              FROM (SELECT fields FROM metrics.measurements
                    WHERE instance_id = %(i)s AND device_id = %(d)s AND fields IS NOT NULL
                    ORDER BY ts DESC LIMIT %(w)s) s) x
        """,
        {"st": f_status, "p": f_primary, "num": _NUMERIC_RE,
         "i": instance_id, "d": device_id, "w": window},
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
            SELECT grp, count(*) AS n, avg(v) AS average, stddev_samp(v) AS std_dev,
                   count(*) FILTER (WHERE v IS NOT NULL AND status = 'PASS') AS pass_count,
                   count(*) FILTER (WHERE v IS NOT NULL AND status = 'FAIL') AS fail_count
            FROM (SELECT fields->>%(g)s AS grp,
                         CASE WHEN (fields->>%(p)s) ~ %(num)s THEN (fields->>%(p)s)::float END AS v,
                         fields->>%(st)s AS status
                  FROM (SELECT fields FROM metrics.measurements WHERE instance_id = %(i)s AND device_id = %(d)s
                        AND fields IS NOT NULL ORDER BY ts DESC LIMIT 2000) s) x
            WHERE grp IS NOT NULL GROUP BY 1 ORDER BY 1
            """,
            {"g": f_group, "p": f_primary, "num": _NUMERIC_RE, "st": f_status,
             "i": instance_id, "d": device_id},
        )
        groups = [{"group": g["grp"], "count": int(g["n"]), "average": _num(g["average"]),
                   "std_dev": _num(g["std_dev"]),
                   "pass_percentage": round(int(g["pass_count"]) / (int(g["pass_count"]) + int(g["fail_count"])) * 100, 2)
                   if (int(g["pass_count"]) + int(g["fail_count"])) else None}
                  for g in groups]

    target, lower, upper = (_num(config.get(roles.get(k))) if roles.get(k) else None for k in ("target", "lower", "upper"))
    readings = int(stats.get("readings") or 0)
    samples = int(stats.get("samples") or 0)
    pass_count, fail_count = int(stats.get("pass_count") or 0), int(stats.get("fail_count") or 0)
    mean, sigma = _num(stats.get("average")), _num(stats.get("std_dev"))
    capability = capability_indices(mean, sigma, lower, upper)

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
    ref_key = None
    if input_key:
        # Every sensor mapped to this input gets a card and a chart, even before
        # the first reading arrives or when the latest row omitted a column.
        mapped_keys: list[str] = []
        mapping = active_mapping(instance_id, device_id) if device_id else None
        if mapping:
            targets = [f.get("target") for f in (mapping.get("definition") or {}).get("fields", [])
                       if f.get("target")]
            mapped_keys = [k for k in sensor_keys({t: None for t in targets}, input_key)
                           if k not in mapped_keys]
        keys = sensor_keys(latest_fields, input_key)
        for key in mapped_keys:
            if key not in keys:
                keys.append(key)
        keys = sensor_keys({k: None for k in keys}, input_key) or [input_key]
        if processor_key == "opposite_thickness":
            # Opposite-side is exactly two sensors (A + B); ignore any third column.
            keys = keys[:MAX_SENSORS]
        ref_key = reference_key(config, keys)
        per_key = {k: [] for k in keys}
        for r in series_rows:
            f = _json(r["fields"], {})
            for k in keys:
                per_key[k].append({"ts": r["ts"].isoformat(), "value": _num(f.get(k))})
        thickness_series = _sensor_thickness_series(processor_key, config, keys, per_key, ref_key)
        linear = (processor_key == "sbs_thickness"
                  and (str(config.get("calibration.mode") or "auto").strip().lower() == "linear"
                       or not parse_baselines(config)))
        linear_scale = _num(config.get("calibration.scale"))
        linear_scale = -1.0 if linear_scale is None else linear_scale
        linear_offset = _num(config.get("calibration.offset")) or 0.0
        sensors = []
        for i, k in enumerate(keys):
            suffix = k[len(input_key):] if k.startswith(input_key) else ""
            thickness_key = f"thickness.sensor_mm{suffix}"
            status_key = "quality.reference_status" if k == ref_key else f"quality.sensor_status{suffix}"
            value = _num(latest_fields.get(k))
            thickness = _num(latest_fields.get(thickness_key))
            if thickness is None and linear and value is not None and k != ref_key:
                thickness = round(linear_scale * value + linear_offset, 6)
            distance = None
            if processor_key == "opposite_thickness" and value is not None:
                distance = round(ZERO_OFFSET_MM + value, 6)
            s_lower, s_upper, s_override = sensor_bounds(config, thickness_key, lower, upper)
            sensors.append({
                "key": k,
                "label": f"Sensor {i + 1}" + (" (reference)" if k == ref_key else ""),
                "value": value,
                "distance": distance,
                "thickness": thickness,
                "status": latest_fields.get(status_key),
                "error": _num(latest_fields.get("reference.error_mm")) if k == ref_key else None,
                "is_reference": k == ref_key,
                "limit_field": thickness_key,
                "limits": {"lower": s_lower, "upper": s_upper, "target": target, "override": s_override},
                "series": per_key[k],
                "thickness_series": thickness_series.get(k),
            })

    limit_keys = {k: roles.get(k) for k in ("target", "lower", "upper")}
    lower_key = roles.get("lower")
    upper_key = roles.get("upper")
    active_key = None
    if lower_key or upper_key:
        active_key = (lower_key or upper_key).split(".")[0] + ".limit_active"
    if reference_enabled(config):
        limit_keys.update({"reference_lower": "reference.lower_limit_mm",
                           "reference_upper": "reference.upper_limit_mm"})
    limit_keys["limit_active"] = active_key
    _, _, limit_active = bounds(
        config, active_key or "thickness.limit_active",
        lower_key or "thickness.lower_limit_mm", upper_key or "thickness.upper_limit_mm")

    return {
        "instance_id": instance_id,
        "name": instance["name"],
        "status": instance["status"],
        "template": {"template_id": template["template_id"], "name": template["name"],
                     "processor_key": processor_key, "commissioning": template.get("commissioning")},
        "processor_key": processor_key,
        "roles": roles,
        "label": roles.get("label") or "Value",
        "unit": roles.get("unit") or "",
        "active_device": device_id,
        "devices": devices,
        "layout": get_layout(instance_id, template, config),
        "ui": _ui(config),
        "config": {
            "target": target, "lower": lower, "upper": upper,
            "limit_active": limit_active,
            "keys": limit_keys,
            "sensor_limits": sensor_overrides(config),
            "calibration.scale": _num(config.get("calibration.scale")),
            "calibration.offset": _num(config.get("calibration.offset")),
            "calibration.mode": config.get("calibration.mode") or "auto",
            "calibration.reference_thickness": _num(config.get("calibration.reference_thickness")),
            "calibration.baselines": parse_baselines(config),
            "calibration.gap_distance": gap_distance(config),
            "calibration.object_thickness": object_thickness(config),
            "calibration.tolerance_mm": tolerance(config),
            "calibration.auto_gap_active": auto_gap_active(config),
            "calibration.calibrated_at": config.get("calibration.calibrated_at"),
            "calibration.calibrated_by": config.get("calibration.calibrated_by"),
            "reference.enabled": reference_enabled(config),
            "reference.sensor_key": ref_key,
            "reference.limit_active": str(config.get("reference.limit_active") or "").lower()
            in {"1", "true", "yes", "on"},
            "reference.lower": _num(config.get("reference.lower_limit_mm")),
            "reference.upper": _num(config.get("reference.upper_limit_mm")),
            "filter.window": _num(config.get("filter.window")),
        },
        "current": {
            "ts": latest["ts"].isoformat() if latest else None,
            "value": _num(latest_fields.get(f_primary)),
            "deviation": _num(latest_fields.get(f_dev)) if f_dev else None,
            "status": latest_fields.get(f_status),
            "reference_error": _num(latest_fields.get("reference.error_mm")),
            "reference_status": latest_fields.get("quality.reference_status"),
            "input": _num(latest_fields.get(roles.get("input"))) if roles.get("input") else None,
            "group": latest_fields.get(f_group) if f_group else None,
            "speed": _num(latest_fields.get(f_speed)) if f_speed else None,
            "fields": latest_fields,
        },
        "statistics": {"minimum": _num(stats.get("minimum")), "maximum": _num(stats.get("maximum")),
                       "average": mean, "std_dev": sigma, "samples": samples, "readings": readings},
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

