"""Industrial incident history, persistence and connectivity monitoring.

The pure state machine lives in ``event_engine.py``. This module supplies:

* threshold resolution from the existing ``core.template_config`` keys
  (``thickness.lower_limit_mm`` / ``upper_limit_mm`` and the generic equivalents),
  with optional per-sensor overrides;
* durable stores (PostgreSQL for production, in-memory for tests);
* replay-safe processing and a channel-neutral notification outbox;
* daily incident summaries and machine connectivity derived from heartbeat /
  data freshness configuration.

Historical incidents are stored separately from ``metrics.measurements`` so a
circular reading buffer or retention job cannot erase them. No notification is
delivered here; queued outbox rows are ready for a future WhatsApp worker.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from ..db import execute, execute_returning, fetch_all, fetch_one
from .event_engine import (
    ABOVE,
    BELOW,
    CLOSE,
    OPEN,
    UPDATE,
    IncidentAction,
    MonitorState,
    Thresholds,
    evaluate,
)
from .limits import flag, number

DEFAULT_HEARTBEAT_SECONDS = 60.0
OFFLINE_SENSOR_FIELD = "connectivity"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _float(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value, default: int = 1) -> int:
    try:
        return max(1, int(float(value)))
    except (TypeError, ValueError):
        return default


def _first_number(config: dict, keys: Iterable[str], default=None):
    for key in keys:
        if key in config and config.get(key) not in (None, ""):
            value = number(config.get(key))
            if value is not None:
                return value
    return default


def _first_text(config: dict, keys: Iterable[str], default=None):
    for key in keys:
        if key in config and config.get(key) not in (None, ""):
            return str(config.get(key)).strip()
    return default


def _limit_map(config: dict) -> dict:
    raw = config.get("event.sensor_limits") or config.get("sensor_limits")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _limit_entry(config: dict, sensor_field: Optional[str], sensor_id: Optional[str]) -> dict:
    limits = _limit_map(config)
    for key in (sensor_field, sensor_id, "*"):
        if key and isinstance(limits.get(key), dict):
            return limits[key]
    return {}


def _override_keys(sensor_field: Optional[str], suffix: str) -> list[str]:
    if not sensor_field:
        return []
    return [
        f"{sensor_field}.{suffix}",
        f"event.sensors.{sensor_field}.{suffix}",
        f"sensor_limits.{sensor_field}.{suffix}",
    ]


def thresholds_for(config: dict, template: Optional[dict], sensor_field: Optional[str] = None,
                   sensor_id: Optional[str] = None) -> Thresholds:
    """Resolve effective thresholds for one monitored sensor.

    Existing configuration wins unchanged: the template's ``field_roles.lower`` /
    ``field_roles.upper`` point at the same keys the processors already use. A
    sensor may override them through an explicit ``event.sensor_limits`` JSON map
    or through keys such as ``<field>.lower_limit`` / ``<field>.upper_limit``.
    """
    config = config or {}
    roles = (template or {}).get("field_roles") or {}
    lower_key = roles.get("lower") or "thickness.lower_limit_mm"
    upper_key = roles.get("upper") or "thickness.upper_limit_mm"
    prefix = (lower_key or upper_key).split(".")[0] if (lower_key or upper_key) else "thickness"
    active_key = roles.get("limit_active") or f"{prefix}.limit_active"

    entry = _limit_entry(config, sensor_field, sensor_id)
    lower_keys = (_override_keys(sensor_field, "lower_limit") +
                  _override_keys(sensor_field, "lower_limit_mm") +
                  _override_keys(sensor_field, "lower") +
                  _override_keys(sensor_field, "minimum") +
                  _override_keys(sensor_field, "min"))
    upper_keys = (_override_keys(sensor_field, "upper_limit") +
                  _override_keys(sensor_field, "upper_limit_mm") +
                  _override_keys(sensor_field, "upper") +
                  _override_keys(sensor_field, "maximum") +
                  _override_keys(sensor_field, "max"))
    lower = _first_number(config, lower_keys)
    upper = _first_number(config, upper_keys)
    has_lower = any(key in config and config.get(key) not in (None, "") for key in lower_keys)
    has_upper = any(key in config and config.get(key) not in (None, "") for key in upper_keys)
    if "lower" in entry:
        lower = _float(entry.get("lower"))
        has_lower = True
    if "upper" in entry:
        upper = _float(entry.get("upper"))
        has_upper = True
    if "min" in entry:
        lower = _float(entry.get("min"))
        has_lower = True
    if "max" in entry:
        upper = _float(entry.get("max"))
        has_upper = True
    if not has_lower:
        lower = _first_number(config, [lower_key])
    if not has_upper:
        upper = _first_number(config, [upper_key])

    active = _first_flag(config, _override_keys(sensor_field, "limit_active") + [active_key])
    if "active" in entry:
        active = bool(entry.get("active"))
    if "enabled" in entry:
        active = bool(entry.get("enabled"))
    if active is None:
        active = True
    enabled = bool(active) and (lower is not None or upper is not None)

    hysteresis = _first_number(
        config,
        _override_keys(sensor_field, "hysteresis") +
        _override_keys(sensor_field, "hysteresis_mm") +
        ["event.hysteresis_mm", "event.hysteresis", "thresholds.hysteresis"],
        0.0,
    ) or 0.0
    debounce_readings = _int(_first_number(
        config,
        _override_keys(sensor_field, "debounce_readings") + ["event.debounce_readings"],
        1,
    ), 1)
    recovery_readings = _int(_first_number(
        config,
        _override_keys(sensor_field, "recovery_readings") + ["event.recovery_readings"],
        1,
    ), 1)
    debounce_seconds = max(0.0, _first_number(
        config,
        _override_keys(sensor_field, "debounce_seconds") + ["event.debounce_seconds"],
        0.0,
    ) or 0.0)
    recovery_seconds = max(0.0, _first_number(
        config,
        _override_keys(sensor_field, "recovery_seconds") + ["event.recovery_seconds"],
        0.0,
    ) or 0.0)
    severity = (_first_text(
        config,
        _override_keys(sensor_field, "severity") + ["event.severity"],
        "critical",
    ) or "critical").lower()
    unit = _first_text(config, _override_keys(sensor_field, "unit"), None) or roles.get("unit")

    # Explicit per-sensor map values have the final say.
    if "hysteresis" in entry:
        hysteresis = _float(entry.get("hysteresis")) or 0.0
    if "debounce_readings" in entry:
        debounce_readings = _int(entry.get("debounce_readings"), 1)
    if "recovery_readings" in entry:
        recovery_readings = _int(entry.get("recovery_readings"), 1)
    if "debounce_seconds" in entry:
        debounce_seconds = max(0.0, _float(entry.get("debounce_seconds")) or 0.0)
    if "recovery_seconds" in entry:
        recovery_seconds = max(0.0, _float(entry.get("recovery_seconds")) or 0.0)
    if "severity" in entry:
        severity = str(entry.get("severity") or "critical").strip().lower()
    if "unit" in entry:
        unit = str(entry.get("unit")) if entry.get("unit") not in (None, "") else unit
    if severity not in ("warning", "critical"):
        severity = "critical"

    return Thresholds(
        lower=lower,
        upper=upper,
        hysteresis=max(0.0, hysteresis),
        debounce_readings=debounce_readings,
        recovery_readings=recovery_readings,
        debounce_seconds=debounce_seconds,
        recovery_seconds=recovery_seconds,
        severity=severity,
        unit=unit,
        enabled=enabled,
    )


def _first_flag(config: dict, keys: Iterable[str]) -> Optional[bool]:
    for key in keys:
        if key in config and config.get(key) not in (None, ""):
            return flag(config.get(key))
    return None


def _numeric(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def monitor_specs(template: Optional[dict], config: dict, fields: dict,
                  device_id: Optional[str] = None) -> list[tuple[str, Thresholds]]:
    """Which values in one processed reading should the event engine watch?

    Per-sensor outputs (for example ``thickness.sensor_mm.2``) are preferred when
    present. Otherwise the template's primary output is monitored. Thresholds are
    resolved per sensor, so a sensor can carry its own minimum / maximum.
    """
    roles = (template or {}).get("field_roles") or {}
    primary = roles.get("primary")
    per_sensor = [
        key for key, value in (fields or {}).items()
        if _numeric(value) and ("sensor_" in key or key.startswith("sensor."))
    ]
    selected = per_sensor or ([primary] if primary and _numeric((fields or {}).get(primary)) else [])
    specs: list[tuple[str, Thresholds]] = []
    for field in sorted(selected):
        specs.append((field, thresholds_for(config, template, field, device_id)))
    return specs


@dataclass(frozen=True)
class Monitor:
    company_id: str
    instance_id: str
    machine_id: Optional[str]
    device_id: Optional[str]
    sensor_id: Optional[str]
    sensor_field: str
    incident_type: str = "out_of_range"

    @property
    def key(self) -> str:
        return "|".join((self.instance_id, self.device_id or "", self.sensor_field,
                         self.incident_type))


@dataclass(frozen=True)
class Reading:
    value: Optional[float]
    ts: datetime
    measurement_id: Optional[int] = None
    reading_key: Optional[str] = None


def reading_key(monitor: Monitor, value: Optional[float], ts: datetime) -> str:
    stamp = ts.isoformat() if isinstance(ts, datetime) else str(ts)
    value_text = "" if value is None else f"{float(value):.12g}"
    raw = f"{monitor.key}|{stamp}|{value_text}"
    return hashlib.sha256(raw.encode()).hexdigest()


class IncidentStore:
    """Persistence contract used by :class:`IncidentEngine`."""

    def claim(self, monitor: Monitor, reading: Reading) -> bool:
        raise NotImplementedError

    def load_state(self, monitor: Monitor) -> Optional[MonitorState]:
        raise NotImplementedError

    def save_state(self, monitor: Monitor, state: MonitorState) -> None:
        raise NotImplementedError

    def open_incident(self, monitor: Monitor, reading: Reading, thresholds: Thresholds,
                      action: IncidentAction) -> int:
        raise NotImplementedError

    def update_incident(self, monitor: Monitor, incident_id: int, reading: Reading,
                        action: IncidentAction) -> None:
        raise NotImplementedError

    def close_incident(self, monitor: Monitor, incident_id: int, reading: Reading,
                       action: IncidentAction) -> None:
        raise NotImplementedError

    def enqueue(self, incident_id: int, transition: str, payload: dict) -> None:
        raise NotImplementedError

    def close_offline(self, instance_id: str, device_id: Optional[str], ended_at: datetime) -> int:
        raise NotImplementedError


def notification_payload(monitor: Monitor, reading: Reading, thresholds: Thresholds,
                         action: IncidentAction, incident_id: int) -> dict:
    return {
        "incident_id": incident_id,
        "event": "incident_opened" if action.kind == OPEN else "incident_closed",
        "company_id": monitor.company_id,
        "machine_id": monitor.machine_id,
        "instance_id": monitor.instance_id,
        "device_id": monitor.device_id,
        "sensor_id": monitor.sensor_id,
        "sensor": monitor.sensor_field,
        "incident_type": monitor.incident_type,
        "severity": thresholds.severity,
        "direction": action.direction,
        "lower_limit": thresholds.lower,
        "upper_limit": thresholds.upper,
        "threshold_value": thresholds.upper if action.direction == ABOVE else thresholds.lower,
        "unit": thresholds.unit,
        "value": reading.value,
        "max_deviation": action.max_deviation,
        "started_at": _iso(action.started_at or reading.ts),
        "ended_at": _iso(action.ended_at),
        "reason": action.reason,
    }


class IncidentEngine:
    """Replay-safe orchestration of the pure state machine."""

    def __init__(self, store: IncidentStore, clock=None):
        self.store = store
        self._clock = clock or _now

    def process(self, monitor: Monitor, thresholds: Thresholds, value: Optional[float],
                ts: Optional[datetime] = None, measurement_id: Optional[int] = None,
                key: Optional[str] = None) -> dict:
        ts = ts or self._clock()
        if not thresholds.enabled and not thresholds.configured:
            # No limits configured: nothing can become abnormal. Still close an
            # incident if limits were removed while it was open.
            previous = self.store.load_state(monitor)
            if not previous or previous.status != "abnormal":
                return {"status": "skipped"}
        reading = Reading(value=value, ts=ts, measurement_id=measurement_id,
                          reading_key=key or reading_key(monitor, value, ts))
        if not self.store.claim(monitor, reading):
            return {"status": "duplicate", "reading_key": reading.reading_key}

        previous = self.store.load_state(monitor) or MonitorState()
        state, action = evaluate(previous, value, ts, thresholds)
        incident_id = previous.incident_id
        opened = False
        updated = False
        closed = False

        if action.kind == OPEN:
            incident_id = self.store.open_incident(monitor, reading, thresholds, action)
            opened = True
            state = replace(state, incident_id=incident_id)
            self.store.enqueue(incident_id, "opened",
                               notification_payload(monitor, reading, thresholds, action, incident_id))
        elif action.kind == UPDATE:
            if incident_id is None:
                incident_id = self.store.open_incident(monitor, reading, thresholds,
                                                       replace(action, kind=OPEN, started_at=ts))
                state = replace(state, incident_id=incident_id)
                opened = True
                self.store.enqueue(
                    incident_id, "opened",
                    notification_payload(monitor, reading, thresholds,
                                         replace(action, kind=OPEN, started_at=ts), incident_id))
            else:
                self.store.update_incident(monitor, incident_id, reading, action)
                updated = True
        elif action.kind == CLOSE:
            if incident_id is not None:
                self.store.close_incident(monitor, incident_id, reading, action)
                self.store.enqueue(incident_id, "closed",
                                   notification_payload(monitor, reading, thresholds, action, incident_id))
                closed = True
            state = replace(state, incident_id=None)

        self.store.save_state(monitor, state)
        return {
            "status": "processed",
            "action": action.kind,
            "incident_id": incident_id,
            "opened": opened,
            "updated": updated,
            "closed": closed,
            "duplicate": False,
        }

    def data_received(self, instance_id: str, device_id: Optional[str], ts: datetime) -> int:
        """Close any offline incident for this source when fresh data arrives."""
        return self.store.close_offline(instance_id, device_id, ts)


# --------------------------------------------------------------------- postgres
def _state_from_row(row: dict) -> MonitorState:
    return MonitorState(
        status=row.get("status") or "normal",
        threshold_fingerprint=row.get("threshold_fingerprint"),
        violation_count=int(row.get("violation_count") or 0),
        violation_since=row.get("violation_since"),
        recovery_count=int(row.get("recovery_count") or 0),
        recovery_since=row.get("recovery_since"),
        incident_id=row.get("incident_id"),
        direction=row.get("direction"),
        max_deviation=float(row.get("max_deviation") or 0),
        first_value=_float(row.get("first_value")),
        last_value=_float(row.get("last_value")),
        last_ts=row.get("last_ts"),
    )


class PostgresIncidentStore(IncidentStore):
    def claim(self, monitor: Monitor, reading: Reading) -> bool:
        row = execute_returning(
            """
            INSERT INTO metrics.incident_processing
                (reading_key, monitor_key, company_id, instance_id, device_id, sensor_field,
                 measurement_id, reading_ts, value)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (reading_key) DO NOTHING
            RETURNING reading_key
            """,
            (reading.reading_key, monitor.key, monitor.company_id, monitor.instance_id,
             monitor.device_id, monitor.sensor_field, reading.measurement_id, reading.ts,
             reading.value),
        )
        return row is not None

    def load_state(self, monitor: Monitor) -> Optional[MonitorState]:
        row = fetch_one(
            "SELECT * FROM metrics.incident_monitor_state WHERE monitor_key = %s",
            (monitor.key,),
        )
        if row:
            return _state_from_row(row)
        # Defensive recovery: an incident row can outlive a lost state row (for
        # example an interrupted write). Adopt the open incident instead of
        # risking a duplicate or an orphan.
        incident = fetch_one(
            "SELECT incident_id, direction, max_deviation, first_value, last_value, started_at "
            "FROM metrics.incidents WHERE status = 'open' AND instance_id = %s "
            "AND coalesce(device_id, '') = coalesce(%s, '') "
            "AND coalesce(sensor_field, '') = coalesce(%s, '') AND incident_type = %s "
            "ORDER BY started_at DESC LIMIT 1",
            (monitor.instance_id, monitor.device_id, monitor.sensor_field, monitor.incident_type),
        )
        if not incident:
            return None
        return MonitorState(
            status="abnormal",
            incident_id=int(incident["incident_id"]),
            direction=incident.get("direction"),
            max_deviation=float(incident.get("max_deviation") or 0),
            first_value=_float(incident.get("first_value")),
            last_value=_float(incident.get("last_value")),
        )

    def save_state(self, monitor: Monitor, state: MonitorState) -> None:
        execute(
            """
            INSERT INTO metrics.incident_monitor_state
                (monitor_key, company_id, instance_id, machine_id, device_id, sensor_id,
                 sensor_field, status, threshold_fingerprint, violation_count, violation_since,
                 recovery_count, recovery_since, incident_id, direction, max_deviation,
                 first_value, last_value, last_ts, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (monitor_key) DO UPDATE SET
                status = EXCLUDED.status,
                threshold_fingerprint = EXCLUDED.threshold_fingerprint,
                violation_count = EXCLUDED.violation_count,
                violation_since = EXCLUDED.violation_since,
                recovery_count = EXCLUDED.recovery_count,
                recovery_since = EXCLUDED.recovery_since,
                incident_id = EXCLUDED.incident_id,
                direction = EXCLUDED.direction,
                max_deviation = EXCLUDED.max_deviation,
                first_value = EXCLUDED.first_value,
                last_value = EXCLUDED.last_value,
                last_ts = EXCLUDED.last_ts,
                updated_at = now()
            """,
            (monitor.key, monitor.company_id, monitor.instance_id, monitor.machine_id,
             monitor.device_id, monitor.sensor_id, monitor.sensor_field, state.status,
             state.threshold_fingerprint, state.violation_count, state.violation_since,
             state.recovery_count, state.recovery_since, state.incident_id, state.direction,
             state.max_deviation, state.first_value, state.last_value, state.last_ts),
        )

    def open_incident(self, monitor: Monitor, reading: Reading, thresholds: Thresholds,
                      action: IncidentAction) -> int:
        threshold_value = thresholds.upper if action.direction == ABOVE else thresholds.lower
        row = execute_returning(
            """
            INSERT INTO metrics.incidents
                (company_id, instance_id, machine_id, device_id, sensor_id, sensor_field,
                 incident_type, direction, severity, status, lower_limit, upper_limit,
                 threshold_value, unit, started_at, first_value, last_value, max_deviation,
                 reading_count, last_measurement_id, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', %s, %s, %s, %s,
                    %s, %s, %s, %s, 1, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING incident_id
            """,
            (monitor.company_id, monitor.instance_id, monitor.machine_id, monitor.device_id,
             monitor.sensor_id, monitor.sensor_field, monitor.incident_type, action.direction,
             thresholds.severity, thresholds.lower, thresholds.upper, threshold_value,
             thresholds.unit, action.started_at or reading.ts, action.first_value,
             reading.value, action.max_deviation, reading.measurement_id,
             json.dumps({"monitor_key": monitor.key})),
        )
        if row:
            return int(row["incident_id"])
        existing = fetch_one(
            "SELECT incident_id FROM metrics.incidents WHERE status = 'open' "
            "AND instance_id = %s AND coalesce(device_id, '') = coalesce(%s, '') "
            "AND coalesce(sensor_field, '') = coalesce(%s, '') AND incident_type = %s "
            "ORDER BY started_at DESC LIMIT 1",
            (monitor.instance_id, monitor.device_id, monitor.sensor_field, monitor.incident_type),
        )
        if existing:
            return int(existing["incident_id"])
        raise RuntimeError("Could not open incident")

    def update_incident(self, monitor: Monitor, incident_id: int, reading: Reading,
                        action: IncidentAction) -> None:
        execute(
            """
            UPDATE metrics.incidents
            SET last_value = %s,
                max_deviation = GREATEST(max_deviation, %s),
                reading_count = reading_count + 1,
                last_measurement_id = %s,
                updated_at = now()
            WHERE incident_id = %s AND status = 'open'
            """,
            (reading.value, action.max_deviation, reading.measurement_id, incident_id),
        )

    def close_incident(self, monitor: Monitor, incident_id: int, reading: Reading,
                       action: IncidentAction) -> None:
        execute(
            """
            UPDATE metrics.incidents
            SET status = 'closed',
                ended_at = %s,
                last_value = %s,
                max_deviation = GREATEST(max_deviation, %s),
                reading_count = reading_count + 1,
                last_measurement_id = %s,
                recovery_reason = %s,
                updated_at = now()
            WHERE incident_id = %s AND status = 'open'
            """,
            (action.ended_at or reading.ts, reading.value, action.max_deviation,
             reading.measurement_id, action.reason or "recovered", incident_id),
        )

    def enqueue(self, incident_id: int, transition: str, payload: dict) -> None:
        execute(
            "INSERT INTO metrics.event_notifications "
            "(incident_id, company_id, instance_id, device_id, transition, payload) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (incident_id, transition) DO NOTHING",
            (incident_id, payload.get("company_id"), payload.get("instance_id"),
             payload.get("device_id"), transition, json.dumps(payload)),
        )

    def close_offline(self, instance_id: str, device_id: Optional[str],
                      ended_at: datetime) -> int:
        rows = fetch_all(
            "SELECT incident_id FROM metrics.incidents WHERE status = 'open' "
            "AND incident_type = 'offline' AND instance_id = %s "
            "AND coalesce(device_id, '') = coalesce(%s, '')",
            (instance_id, device_id),
        )
        for row in rows:
            execute(
                "UPDATE metrics.incidents SET status = 'closed', ended_at = %s, "
                "recovery_reason = 'data_received', updated_at = now() "
                "WHERE incident_id = %s",
                (ended_at, row["incident_id"]),
            )
            execute(
                "INSERT INTO metrics.event_notifications "
                "(incident_id, company_id, instance_id, device_id, transition, payload) "
                "SELECT incident_id, company_id, instance_id, device_id, 'closed', %s "
                "FROM metrics.incidents WHERE incident_id = %s "
                "ON CONFLICT (incident_id, transition) DO NOTHING",
                (json.dumps({"incident_id": row["incident_id"], "event": "incident_closed",
                             "incident_type": "offline", "reason": "data_received",
                             "ended_at": ended_at.isoformat()}), row["incident_id"]),
            )
        return len(rows)


class MemoryIncidentStore(IncidentStore):
    """Small in-memory store used by unit tests (same contract as PostgreSQL)."""

    def __init__(self):
        self.claimed: set[str] = set()
        self.states: dict[str, MonitorState] = {}
        self.incidents: dict[int, dict] = {}
        self.notifications: list[dict] = []
        self._next_incident_id = 1

    def claim(self, monitor: Monitor, reading: Reading) -> bool:
        if reading.reading_key in self.claimed:
            return False
        self.claimed.add(reading.reading_key)
        return True

    def load_state(self, monitor: Monitor) -> Optional[MonitorState]:
        return self.states.get(monitor.key)

    def save_state(self, monitor: Monitor, state: MonitorState) -> None:
        self.states[monitor.key] = state

    def _open_for(self, monitor: Monitor) -> Optional[int]:
        for incident_id, incident in self.incidents.items():
            if (incident["status"] == "open" and incident["instance_id"] == monitor.instance_id
                    and (incident.get("device_id") or "") == (monitor.device_id or "")
                    and (incident.get("sensor_field") or "") == (monitor.sensor_field or "")
                    and incident["incident_type"] == monitor.incident_type):
                return incident_id
        return None

    def open_incident(self, monitor: Monitor, reading: Reading, thresholds: Thresholds,
                      action: IncidentAction) -> int:
        existing = self._open_for(monitor)
        if existing is not None:
            return existing
        incident_id = self._next_incident_id
        self._next_incident_id += 1
        self.incidents[incident_id] = {
            "incident_id": incident_id,
            "company_id": monitor.company_id,
            "instance_id": monitor.instance_id,
            "machine_id": monitor.machine_id,
            "device_id": monitor.device_id,
            "sensor_id": monitor.sensor_id,
            "sensor_field": monitor.sensor_field,
            "incident_type": monitor.incident_type,
            "direction": action.direction,
            "severity": thresholds.severity,
            "status": "open",
            "lower_limit": thresholds.lower,
            "upper_limit": thresholds.upper,
            "threshold_value": thresholds.upper if action.direction == ABOVE else thresholds.lower,
            "unit": thresholds.unit,
            "started_at": action.started_at or reading.ts,
            "ended_at": None,
            "first_value": action.first_value,
            "last_value": reading.value,
            "max_deviation": action.max_deviation,
            "reading_count": 1,
            "last_measurement_id": reading.measurement_id,
            "recovery_reason": None,
            "metadata": {"monitor_key": monitor.key},
        }
        return incident_id

    def update_incident(self, monitor: Monitor, incident_id: int, reading: Reading,
                        action: IncidentAction) -> None:
        incident = self.incidents[incident_id]
        if incident["status"] != "open":
            return
        incident["last_value"] = reading.value
        incident["max_deviation"] = max(float(incident["max_deviation"] or 0), action.max_deviation)
        incident["reading_count"] += 1
        incident["last_measurement_id"] = reading.measurement_id

    def close_incident(self, monitor: Monitor, incident_id: int, reading: Reading,
                       action: IncidentAction) -> None:
        incident = self.incidents[incident_id]
        if incident["status"] != "open":
            return
        incident.update({
            "status": "closed",
            "ended_at": action.ended_at or reading.ts,
            "last_value": reading.value,
            "max_deviation": max(float(incident["max_deviation"] or 0), action.max_deviation),
            "reading_count": incident["reading_count"] + 1,
            "last_measurement_id": reading.measurement_id,
            "recovery_reason": action.reason or "recovered",
        })

    def enqueue(self, incident_id: int, transition: str, payload: dict) -> None:
        if any(n["incident_id"] == incident_id and n["transition"] == transition
               for n in self.notifications):
            return
        self.notifications.append({"incident_id": incident_id, "transition": transition,
                                   "channel": "whatsapp", "status": "queued",
                                   "payload": payload})

    def close_offline(self, instance_id: str, device_id: Optional[str],
                      ended_at: datetime) -> int:
        closed = 0
        for incident_id, incident in self.incidents.items():
            if (incident["status"] == "open" and incident["incident_type"] == "offline"
                    and incident["instance_id"] == instance_id
                    and (incident.get("device_id") or "") == (device_id or "")):
                incident.update({"status": "closed", "ended_at": ended_at,
                                 "recovery_reason": "data_received"})
                self.notifications.append({
                    "incident_id": incident_id, "transition": "closed", "channel": "whatsapp",
                    "status": "queued",
                    "payload": {"incident_id": incident_id, "event": "incident_closed",
                                "incident_type": "offline", "reason": "data_received"},
                })
                closed += 1
        return closed


# ------------------------------------------------------------------- orchestration
def observe(company_id: str, instance_id: str, device_id: Optional[str],
            machine_id: Optional[str], ts: datetime, fields: dict, config: dict,
            template: Optional[dict], measurement_id: Optional[int] = None,
            store: Optional[IncidentStore] = None) -> list[dict]:
    """Feed one processed measurement to the event engine for every sensor."""
    store = store or PostgresIncidentStore()
    engine = IncidentEngine(store)
    results = []
    if device_id:
        engine.data_received(instance_id, device_id, ts)
    for sensor_field, thresholds in monitor_specs(template, config, fields, device_id):
        monitor = Monitor(
            company_id=company_id,
            instance_id=instance_id,
            machine_id=machine_id,
            device_id=device_id,
            sensor_id=device_id,
            sensor_field=sensor_field,
        )
        result = engine.process(monitor, thresholds, _float(fields.get(sensor_field)), ts,
                                measurement_id=measurement_id)
        if result.get("status") == "processed":
            results.append({"sensor": sensor_field, **result})
    return results


# --------------------------------------------------------------------- connectivity
def heartbeat_seconds(config: Optional[dict], rule_params: Optional[dict] = None) -> float:
    config = config or {}
    value = _first_number(config, [
        "connectivity.freshness_seconds", "connectivity.heartbeat_seconds",
        "heartbeat.seconds", "offline.seconds",
    ])
    if value is None and isinstance(rule_params, dict):
        value = _float(rule_params.get("seconds"))
    if value is None:
        value = DEFAULT_HEARTBEAT_SECONDS
    return max(1.0, float(value))


def connectivity_status(last_seen: Optional[datetime], now: Optional[datetime],
                        seconds: float) -> dict:
    now = now or _now()
    if last_seen is None:
        return {"online": False, "status": "unknown", "age_seconds": None,
                "heartbeat_seconds": seconds}
    aware = last_seen if last_seen.tzinfo else last_seen.replace(tzinfo=timezone.utc)
    age = max(0.0, (now - aware).total_seconds())
    online = age <= seconds
    return {"online": online, "status": "online" if online else "offline",
            "age_seconds": round(age, 2), "heartbeat_seconds": seconds}


def machine_connectivity(company_id: str) -> list[dict]:
    """Current machine/source connectivity based on data freshness."""
    from .devices import load_device
    from .instances import instance_device_ids

    instances = fetch_all(
        "SELECT instance_id, name, asset_id, plant_key FROM core.template_instances "
        "WHERE company_id = %s AND status = 'active' ORDER BY name",
        (company_id,),
    )
    if not instances:
        return []
    configs: dict[str, dict] = {}
    for row in fetch_all(
        "SELECT instance_id, config_key, value FROM core.template_config "
        "WHERE instance_id = ANY(%s)",
        ([i["instance_id"] for i in instances],),
    ):
        configs.setdefault(row["instance_id"], {})[row["config_key"]] = row["value"]
    rules: dict[tuple[str, str], dict] = {}
    for row in fetch_all(
        "SELECT r.instance_id, m.source_device_id AS device_id, r.params "
        "FROM core.alert_rules r "
        "JOIN core.data_mappings m ON m.instance_id = r.instance_id "
        "WHERE r.rule_type = 'offline' AND r.enabled AND r.instance_id = ANY(%s)",
        ([i["instance_id"] for i in instances],),
    ):
        params = row["params"] if isinstance(row["params"], dict) else json.loads(row["params"] or "{}")
        rules[(row["instance_id"], row["device_id"])] = params

    machines = []
    now = _now()
    for instance in instances:
        config = configs.get(instance["instance_id"], {})
        devices = []
        for device_id in instance_device_ids(instance["instance_id"]):
            device = load_device(device_id)
            if not device:
                continue
            seconds = heartbeat_seconds(config, rules.get((instance["instance_id"], device_id)))
            status = connectivity_status(device.get("last_seen"), now, seconds)
            devices.append({
                "device_id": device_id,
                "sensor_id": device_id,
                "status": device["status"],
                **status,
                "last_seen": _iso(device.get("last_seen")),
                "last_error": device.get("last_error"),
            })
        if not devices:
            machine_status = "unknown"
        elif all(d["status"] == "online" for d in devices):
            machine_status = "online"
        elif all(d["status"] == "offline" for d in devices):
            machine_status = "offline"
        else:
            machine_status = "degraded"
        machines.append({
            "machine_id": instance.get("asset_id") or instance["instance_id"],
            "instance_id": instance["instance_id"],
            "name": instance["name"],
            "asset_id": instance.get("asset_id"),
            "plant": instance.get("plant_key"),
            "status": machine_status,
            "online": machine_status == "online",
            "devices": devices,
        })
    return machines


def check_connectivity(company_id: Optional[str] = None,
                       store: Optional[IncidentStore] = None) -> list[dict]:
    """Open/close offline incidents from configured heartbeat freshness."""
    from .devices import load_device
    from .instances import instance_device_ids

    store = store or PostgresIncidentStore()
    sql = ("SELECT instance_id, company_id, name, asset_id FROM core.template_instances "
           "WHERE status = 'active'")
    params: list[Any] = []
    if company_id:
        sql += " AND company_id = %s"
        params.append(company_id)
    instances = fetch_all(sql, params)
    now = _now()
    results = []
    for instance in instances:
        config_rows = fetch_all(
            "SELECT config_key, value FROM core.template_config WHERE instance_id = %s",
            (instance["instance_id"],),
        )
        config = {r["config_key"]: r["value"] for r in config_rows}
        rule = fetch_one(
            "SELECT params FROM core.alert_rules WHERE instance_id = %s AND rule_type = 'offline'",
            (instance["instance_id"],),
        )
        rule_params = (rule["params"] if isinstance(rule.get("params"), dict)
                       else json.loads((rule or {}).get("params") or "{}")) if rule else {}
        seconds = heartbeat_seconds(config, rule_params)
        severity = (_first_text(config, ["event.offline_severity", "connectivity.severity"],
                                "critical") or "critical").lower()
        if severity not in ("warning", "critical"):
            severity = "critical"
        for device_id in instance_device_ids(instance["instance_id"]):
            device = load_device(device_id)
            if not device:
                continue
            status = connectivity_status(device.get("last_seen"), now, seconds)
            machine_id = instance.get("asset_id") or device.get("asset_id") or instance["instance_id"]
            if status["status"] == "offline":
                existing = fetch_one(
                    "SELECT incident_id FROM metrics.incidents WHERE status = 'open' "
                    "AND incident_type = 'offline' AND instance_id = %s "
                    "AND coalesce(device_id, '') = coalesce(%s, '') LIMIT 1",
                    (instance["instance_id"], device_id),
                )
                if not existing:
                    started = (device.get("last_seen") or now) + timedelta(seconds=seconds) \
                        if device.get("last_seen") else now
                    row = execute_returning(
                        """
                        INSERT INTO metrics.incidents
                            (company_id, instance_id, machine_id, device_id, sensor_id,
                             sensor_field, incident_type, direction, severity, status,
                             threshold_value, unit, started_at, last_value, recovery_reason,
                             metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, 'offline', 'offline', %s, 'open',
                                %s, 'seconds', %s, NULL, NULL, %s)
                        ON CONFLICT DO NOTHING
                        RETURNING incident_id
                        """,
                        (instance["company_id"], instance["instance_id"], machine_id, device_id,
                         device_id, OFFLINE_SENSOR_FIELD,
                         severity, seconds, started,
                         json.dumps({"heartbeat_seconds": seconds, "last_seen": _iso(device.get("last_seen"))})),
                    )
                    if row:
                        incident_id = int(row["incident_id"])
                        execute(
                            "INSERT INTO metrics.event_notifications "
                            "(incident_id, company_id, instance_id, device_id, transition, payload) "
                            "VALUES (%s, %s, %s, %s, 'opened', %s) "
                            "ON CONFLICT (incident_id, transition) DO NOTHING",
                            (incident_id, instance["company_id"], instance["instance_id"], device_id,
                             json.dumps({"incident_id": incident_id, "event": "incident_opened",
                                         "incident_type": "offline", "machine_id": machine_id,
                                         "device_id": device_id, "heartbeat_seconds": seconds})),
                        )
                        results.append({"instance_id": instance["instance_id"],
                                        "device_id": device_id, "action": "opened",
                                        "incident_id": incident_id})
            else:
                closed = store.close_offline(instance["instance_id"], device_id, now)
                if closed:
                    results.append({"instance_id": instance["instance_id"],
                                    "device_id": device_id, "action": "closed",
                                    "closed": closed})
    return results


# --------------------------------------------------------------------- queries
def _serialize_incident(row: dict) -> dict:
    out = dict(row)
    for key in ("started_at", "ended_at", "created_at", "updated_at"):
        if out.get(key):
            out[key] = out[key].isoformat()
    for key in ("lower_limit", "upper_limit", "threshold_value", "first_value",
                "last_value", "max_deviation"):
        if out.get(key) is not None:
            out[key] = float(out[key])
    if isinstance(out.get("metadata"), str):
        try:
            out["metadata"] = json.loads(out["metadata"])
        except ValueError:
            out["metadata"] = {}
    if out.get("incident_id") is not None:
        out["incident_id"] = int(out["incident_id"])
    return out


def _where(company_id: str, instance_id: Optional[str] = None,
           device_id: Optional[str] = None, status: Optional[str] = None,
           incident_type: Optional[str] = None, sensor_field: Optional[str] = None,
           start: Optional[datetime] = None, end: Optional[datetime] = None):
    clauses = ["company_id = %s"]
    params: list[Any] = [company_id]
    if instance_id:
        clauses.append("instance_id = %s")
        params.append(instance_id)
    if device_id:
        clauses.append("device_id = %s")
        params.append(device_id)
    if status:
        clauses.append("status = %s")
        params.append(status)
    if incident_type:
        clauses.append("incident_type = %s")
        params.append(incident_type)
    if sensor_field:
        clauses.append("sensor_field = %s")
        params.append(sensor_field)
    if start:
        clauses.append("started_at >= %s")
        params.append(start)
    if end:
        clauses.append("started_at < %s")
        params.append(end)
    return " AND ".join(clauses), params


def list_incidents(company_id: str, instance_id: Optional[str] = None,
                   device_id: Optional[str] = None, status: Optional[str] = None,
                   incident_type: Optional[str] = None, sensor_field: Optional[str] = None,
                   start: Optional[datetime] = None, end: Optional[datetime] = None,
                   limit: int = 100, offset: int = 0) -> list[dict]:
    where, params = _where(company_id, instance_id, device_id, status, incident_type,
                           sensor_field, start, end)
    rows = fetch_all(
        f"SELECT * FROM metrics.incidents WHERE {where} "
        "ORDER BY started_at DESC, incident_id DESC LIMIT %s OFFSET %s",
        (*params, limit, offset),
    )
    return [_serialize_incident(r) for r in rows]


def active_incidents(company_id: str, instance_id: Optional[str] = None,
                     device_id: Optional[str] = None) -> list[dict]:
    return list_incidents(company_id, instance_id=instance_id, device_id=device_id,
                          status="open", limit=500)


def get_incident(company_id: str, incident_id: int) -> Optional[dict]:
    row = fetch_one("SELECT * FROM metrics.incidents WHERE company_id = %s AND incident_id = %s",
                    (company_id, incident_id))
    if not row:
        return None
    incident = _serialize_incident(row)
    incident["notifications"] = [
        {**n, "payload": n["payload"] if isinstance(n["payload"], dict) else json.loads(n["payload"] or "{}"),
         "created_at": n["created_at"].isoformat() if n.get("created_at") else None,
         "sent_at": n["sent_at"].isoformat() if n.get("sent_at") else None}
        for n in fetch_all(
            "SELECT notification_id, transition, channel, status, payload, created_at, sent_at "
            "FROM metrics.event_notifications WHERE incident_id = %s ORDER BY created_at",
            (incident_id,),
        )
    ]
    return incident


def incident_counts(company_id: str, instance_id: Optional[str] = None,
                    device_id: Optional[str] = None, start: Optional[datetime] = None,
                    end: Optional[datetime] = None, tz: str = "UTC") -> dict:
    """Daily out-of-range / offline counts and summary metrics."""
    where, params = _where(company_id, instance_id, device_id, start=start, end=end)
    rows = fetch_all(
        f"""
        SELECT (started_at AT TIME ZONE %s)::date AS day,
               count(*) AS total,
               count(*) FILTER (WHERE incident_type = 'out_of_range') AS out_of_range,
               count(*) FILTER (WHERE incident_type = 'offline') AS offline,
               count(*) FILTER (WHERE incident_type = 'out_of_range' AND direction = 'above') AS above,
               count(*) FILTER (WHERE incident_type = 'out_of_range' AND direction = 'below') AS below,
               count(*) FILTER (WHERE status = 'open') AS open,
               avg(extract(epoch FROM (coalesce(ended_at, now()) - started_at))) AS avg_duration_seconds,
               max(max_deviation) AS max_deviation
        FROM metrics.incidents
        WHERE {where}
        GROUP BY 1 ORDER BY 1
        """,
        (tz, *params),
    )
    days = []
    for row in rows:
        days.append({
            "day": row["day"].isoformat() if isinstance(row["day"], date) else str(row["day"]),
            "total": int(row["total"] or 0),
            "out_of_range": int(row["out_of_range"] or 0),
            "offline": int(row["offline"] or 0),
            "above": int(row["above"] or 0),
            "below": int(row["below"] or 0),
            "open": int(row["open"] or 0),
            "avg_duration_seconds": round(float(row["avg_duration_seconds"]), 2)
            if row["avg_duration_seconds"] is not None else None,
            "max_deviation": float(row["max_deviation"]) if row["max_deviation"] is not None else None,
        })
    total = sum(d["total"] for d in days)
    out_of_range = sum(d["out_of_range"] for d in days)
    offline = sum(d["offline"] for d in days)
    weighted = [d["avg_duration_seconds"] * d["total"] for d in days
                if d["avg_duration_seconds"] is not None]
    return {
        "timezone": tz,
        "days": days,
        "totals": {
            "total": total,
            "out_of_range": out_of_range,
            "offline": offline,
            "above": sum(d["above"] for d in days),
            "below": sum(d["below"] for d in days),
            "open": sum(d["open"] for d in days),
            "avg_duration_seconds": round(sum(weighted) / total, 2) if total and weighted else None,
        },
    }
