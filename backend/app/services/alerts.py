"""Alert rules + events.

Rules (core.alert_rules, seeded from the template's alert_defaults):
    fail_streak      N consecutive FAIL readings      -> critical, resolves on the next PASS
    offline          no reading for N seconds         -> critical, resolves when data returns
    mapping_failure  a payload could not be mapped    -> warning, at most one per minute
Delivery in the MVP is the in-app alerts list; email/WhatsApp hook in at _emit().
"""
from __future__ import annotations

import json
import threading
import time
from typing import Optional

from ..db import execute, fetch_all, fetch_one

_streaks: dict[tuple, int] = {}
_last_failure_alert: dict[str, float] = {}
_lock = threading.Lock()


def ensure_rules(instance_id: str, template: dict) -> None:
    for rule in template.get("alert_defaults") or []:
        execute(
            "INSERT INTO core.alert_rules (instance_id, rule_type, params) VALUES (%s, %s, %s) "
            "ON CONFLICT (instance_id, rule_type) DO NOTHING",
            (instance_id, rule["type"], json.dumps(rule.get("params") or {})),
        )


def rules(instance_id: str) -> dict:
    rows = fetch_all("SELECT rule_type, params, enabled FROM core.alert_rules WHERE instance_id = %s", (instance_id,))
    out = {}
    for r in rows:
        params = r["params"] if isinstance(r["params"], dict) else json.loads(r["params"] or "{}")
        out[r["rule_type"]] = {"params": params, "enabled": r["enabled"]}
    return out


def _open_event(instance_id: str, rule_type: str, device_id: Optional[str]) -> Optional[dict]:
    return fetch_one(
        "SELECT event_id FROM metrics.alert_events WHERE instance_id = %s AND rule_type = %s "
        "AND coalesce(device_id, '') = coalesce(%s, '') AND resolved_at IS NULL ORDER BY created_at DESC LIMIT 1",
        (instance_id, rule_type, device_id),
    )


def _emit(company_id, instance_id: str, device_id: Optional[str], rule_type: str, severity: str, message: str) -> None:
    execute(
        "INSERT INTO metrics.alert_events (company_id, instance_id, device_id, rule_type, severity, message) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (company_id, instance_id, device_id, rule_type, severity, message),
    )


def _resolve(instance_id: str, rule_type: str, device_id: Optional[str]) -> None:
    execute(
        "UPDATE metrics.alert_events SET resolved_at = now() WHERE instance_id = %s AND rule_type = %s "
        "AND coalesce(device_id, '') = coalesce(%s, '') AND resolved_at IS NULL",
        (instance_id, rule_type, device_id),
    )


def on_measurement(company_id, instance_id: str, device_id: str, status: Optional[str], rule_set: dict) -> None:
    rule = rule_set.get("fail_streak")
    if not rule or not rule["enabled"] or status is None:
        return
    key = (instance_id, device_id)
    limit = int(rule["params"].get("count") or 5)
    with _lock:
        if status == "FAIL":
            _streaks[key] = _streaks.get(key, 0) + 1
            count = _streaks[key]
        else:
            count = _streaks.pop(key, 0)
            status = "PASS"
    if status == "FAIL" and count == limit:
        _emit(company_id, instance_id, device_id, "fail_streak", "critical",
              f"{limit} consecutive FAIL readings on {device_id}")
    elif status == "PASS" and count >= limit:
        _resolve(instance_id, "fail_streak", device_id)


def on_mapping_failure(company_id, instance_id: str, device_id: str, errors: list, rule_set: dict) -> None:
    rule = rule_set.get("mapping_failure")
    if not rule or not rule["enabled"]:
        return
    now = time.time()
    with _lock:
        if now - _last_failure_alert.get(instance_id, 0) < 60:
            return
        _last_failure_alert[instance_id] = now
    first = errors[0] if errors else {}
    _emit(company_id, instance_id, device_id, "mapping_failure", "warning",
          f"Data from {device_id} could not be mapped: {first.get('message', 'unknown error')}")


def check_offline() -> None:
    """Called by the source runtime every few seconds."""
    rows = fetch_all(
        """
        SELECT t.instance_id, t.company_id, m.source_device_id AS device_id, d.last_seen,
               r.params, extract(epoch FROM (now() - d.last_seen)) AS age
        FROM core.template_instances t
        JOIN core.data_mappings m ON m.instance_id = t.instance_id
        JOIN core.devices d ON d.device_id = m.source_device_id
        JOIN core.alert_rules r ON r.instance_id = t.instance_id AND r.rule_type = 'offline' AND r.enabled
        WHERE t.status = 'active'
        """
    )
    for r in rows:
        params = r["params"] if isinstance(r["params"], dict) else json.loads(r["params"] or "{}")
        limit = float(params.get("seconds") or 15)
        offline = r["last_seen"] is None or float(r["age"] or 0) > limit
        open_event = _open_event(r["instance_id"], "offline", r["device_id"])
        if offline and not open_event:
            _emit(r["company_id"], r["instance_id"], r["device_id"], "offline", "critical",
                  f"No data from {r['device_id']} for more than {int(limit)} s")
        elif not offline and open_event:
            _resolve(r["instance_id"], "offline", r["device_id"])


def list_events(instance_id: str, limit: int = 50) -> list[dict]:
    rows = fetch_all(
        "SELECT event_id, device_id, rule_type, severity, message, created_at, resolved_at, ack_at, ack_by "
        "FROM metrics.alert_events WHERE instance_id = %s ORDER BY created_at DESC LIMIT %s",
        (instance_id, limit),
    )
    for r in rows:
        for k in ("created_at", "resolved_at", "ack_at"):
            r[k] = r[k].isoformat() if r.get(k) else None
    return rows


def acknowledge(instance_id: str, event_id: int, username: str) -> None:
    execute("UPDATE metrics.alert_events SET ack_at = now(), ack_by = %s WHERE instance_id = %s AND event_id = %s",
            (username, instance_id, event_id))
