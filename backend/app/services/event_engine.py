"""Reusable industrial event state machine.

This module is intentionally free of database and framework imports. It turns a
stream of optional numeric readings into open / update / close incident actions
so it can be reused by readers, MQTT workers, tests or a future streaming job.

Key rules
---------
* Entry uses the configured lower / upper limits (inclusive, like the processors).
* Debounce requires ``debounce_readings`` consecutive violations and optionally
  ``debounce_seconds`` in the abnormal state before an incident is opened.
* Recovery uses hysteresis (the value must pass back inside the limit by that
  margin) plus ``recovery_readings`` / ``recovery_seconds`` before closing.
* A missing reading is a no-op. It never invents or closes an incident.
* One abnormal period is one incident; only a real recovery followed by a new
  breach opens a new incident.
* Threshold changes keep an open incident open but reset candidate counters, so
  old debounce evidence cannot be re-used under new limits.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional

NORMAL = "normal"
ABNORMAL = "abnormal"

ABOVE = "above"
BELOW = "below"

OPEN = "open"
UPDATE = "update"
CLOSE = "close"
NONE = "none"


def _seconds_since(start: Optional[datetime], now: datetime) -> float:
    if start is None:
        return 0.0
    try:
        return max(0.0, (now - start).total_seconds())
    except TypeError:
        return 0.0


def _as_positive_int(value, default: int) -> int:
    try:
        return max(1, int(float(value)))
    except (TypeError, ValueError):
        return default


def _as_non_negative(value, default: float) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Thresholds:
    """Effective thresholds for one monitored sensor."""

    lower: Optional[float] = None
    upper: Optional[float] = None
    hysteresis: float = 0.0
    debounce_readings: int = 1
    recovery_readings: int = 1
    debounce_seconds: float = 0.0
    recovery_seconds: float = 0.0
    severity: str = "critical"
    unit: Optional[str] = None
    enabled: bool = True

    @property
    def configured(self) -> bool:
        return self.enabled and (self.lower is not None or self.upper is not None)

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(
            {
                "lower": self.lower,
                "upper": self.upper,
                "hysteresis": self.hysteresis,
                "debounce_readings": self.debounce_readings,
                "recovery_readings": self.recovery_readings,
                "debounce_seconds": self.debounce_seconds,
                "recovery_seconds": self.recovery_seconds,
                "severity": self.severity,
                "enabled": self.enabled,
            },
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def breach_direction(self, value: Optional[float]) -> Optional[str]:
        if value is None or not self.enabled:
            return None
        if self.upper is not None and value > self.upper:
            return ABOVE
        if self.lower is not None and value < self.lower:
            return BELOW
        return None

    def deviation(self, value: float, direction: str) -> float:
        if direction == ABOVE and self.upper is not None:
            return round(max(0.0, float(value) - self.upper), 9)
        if direction == BELOW and self.lower is not None:
            return round(max(0.0, self.lower - float(value)), 9)
        return 0.0

    def recovered(self, value: Optional[float]) -> bool:
        """True only when the value is back inside the recovery band."""
        if value is None:
            return False
        value = float(value)
        if self.upper is not None and value > self.upper - self.hysteresis:
            return False
        if self.lower is not None and value < self.lower + self.hysteresis:
            return False
        return True


@dataclass(frozen=True)
class MonitorState:
    status: str = NORMAL
    threshold_fingerprint: Optional[str] = None
    violation_count: int = 0
    violation_since: Optional[datetime] = None
    recovery_count: int = 0
    recovery_since: Optional[datetime] = None
    incident_id: Optional[int] = None
    direction: Optional[str] = None
    max_deviation: float = 0.0
    first_value: Optional[float] = None
    last_value: Optional[float] = None
    last_ts: Optional[datetime] = None


@dataclass(frozen=True)
class IncidentAction:
    kind: str = NONE
    direction: Optional[str] = None
    max_deviation: float = 0.0
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    first_value: Optional[float] = None
    reason: Optional[str] = None


def reset_candidates(state: MonitorState, fingerprint: Optional[str] = None) -> MonitorState:
    return replace(
        state,
        threshold_fingerprint=fingerprint or state.threshold_fingerprint,
        violation_count=0,
        violation_since=None,
        recovery_count=0,
        recovery_since=None,
        direction=None if state.status == NORMAL else state.direction,
        max_deviation=0.0 if state.status == NORMAL else state.max_deviation,
        first_value=None if state.status == NORMAL else state.first_value,
    )


def evaluate(state: Optional[MonitorState], value: Optional[float], ts: datetime,
             thresholds: Thresholds) -> tuple[MonitorState, IncidentAction]:
    """Advance one monitor by one reading.

    ``value is None`` means the reading was missing: the state is returned
    unchanged. The returned action tells the caller what to persist.
    """
    state = state or MonitorState()

    if not thresholds.enabled:
        if state.status == ABNORMAL:
            action = IncidentAction(
                kind=CLOSE,
                direction=state.direction,
                max_deviation=state.max_deviation,
                ended_at=ts,
                reason="limits_disabled",
            )
            return MonitorState(last_value=value, last_ts=ts,
                                threshold_fingerprint=thresholds.fingerprint), action
        return reset_candidates(state, thresholds.fingerprint), IncidentAction()

    if value is None:
        return replace(state, last_ts=ts), IncidentAction()

    value = float(value)
    if state.threshold_fingerprint not in (None, thresholds.fingerprint):
        # Keep an open incident, but never carry old debounce/recovery evidence
        # across a threshold change.
        state = reset_candidates(state, thresholds.fingerprint)

    if state.status == NORMAL:
        direction = thresholds.breach_direction(value)
        if direction is None:
            return replace(
                state,
                last_value=value,
                last_ts=ts,
                violation_count=0,
                violation_since=None,
                direction=None,
                max_deviation=0.0,
                first_value=None,
            ), IncidentAction()

        if state.violation_count and state.direction != direction:
            state = replace(state, violation_count=0, violation_since=None,
                            max_deviation=0.0, first_value=None)

        count = state.violation_count + 1
        since = state.violation_since or ts
        max_deviation = max(state.max_deviation, thresholds.deviation(value, direction))
        first_value = state.first_value if state.first_value is not None else value
        candidate = replace(
            state,
            violation_count=count,
            violation_since=since,
            direction=direction,
            max_deviation=max_deviation,
            first_value=first_value,
            last_value=value,
            last_ts=ts,
        )
        if count >= thresholds.debounce_readings and \
                _seconds_since(since, ts) >= thresholds.debounce_seconds:
            abnormal = replace(candidate, status=ABNORMAL, recovery_count=0, recovery_since=None)
            return abnormal, IncidentAction(
                kind=OPEN,
                direction=direction,
                max_deviation=max_deviation,
                started_at=since,
                first_value=first_value,
            )
        return candidate, IncidentAction()

    # Abnormal: keep the incident open while any limit is still breached,
    # including inside the hysteresis band (no recovery yet).
    direction = thresholds.breach_direction(value)
    if direction is not None:
        max_deviation = max(state.max_deviation, thresholds.deviation(value, direction))
        updated = replace(
            state,
            max_deviation=max_deviation,
            recovery_count=0,
            recovery_since=None,
            last_value=value,
            last_ts=ts,
            direction=state.direction or direction,
        )
        return updated, IncidentAction(kind=UPDATE, direction=updated.direction,
                                       max_deviation=max_deviation)

    if thresholds.recovered(value):
        count = state.recovery_count + 1
        since = state.recovery_since or ts
        recovered_state = replace(
            state,
            recovery_count=count,
            recovery_since=since,
            last_value=value,
            last_ts=ts,
        )
        if count >= thresholds.recovery_readings and \
                _seconds_since(since, ts) >= thresholds.recovery_seconds:
            closed = MonitorState(
                status=NORMAL,
                threshold_fingerprint=thresholds.fingerprint,
                last_value=value,
                last_ts=ts,
            )
            return closed, IncidentAction(
                kind=CLOSE,
                direction=state.direction,
                max_deviation=state.max_deviation,
                ended_at=ts,
                reason="recovered",
            )
        return recovered_state, IncidentAction()

    # Inside the hysteresis band: still abnormal, but not a fresh violation and
    # not yet a recovery. A single normal-ish sample must not close the incident.
    return replace(
        state,
        recovery_count=0,
        recovery_since=None,
        last_value=value,
        last_ts=ts,
    ), IncidentAction()
