"""Plant-local time range resolution for the AI analytics tools.

Relative expressions (today, yesterday, last 7 days, this shift) are resolved
against the plant's configured timezone, never the server's or the browser's.
The LLM may pass a period keyword or explicit ISO timestamps; both end up in
this module for validation and range limiting.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from ...config import settings

DEFAULT_SHIFTS = [
    {"name": "A", "start": "06:00", "end": "14:00"},
    {"name": "B", "start": "14:00", "end": "22:00"},
    {"name": "C", "start": "22:00", "end": "06:00"},
]


class TimeframeError(ValueError):
    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime
    timezone: str
    label: str
    start_local: datetime
    end_local: datetime

    def as_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "start_local": self.start_local.isoformat(),
            "end_local": self.end_local.isoformat(),
            "timezone": self.timezone,
            "label": self.label,
        }


def plant_timezone(config: dict) -> ZoneInfo:
    name = str((config or {}).get("report.timezone") or settings.ai_default_timezone).strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(settings.ai_default_timezone or "UTC")


def shifts(config: dict) -> list[dict]:
    raw = (config or {}).get("report.shifts")
    parsed = None
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
    elif isinstance(raw, list):
        parsed = raw
    return parsed or DEFAULT_SHIFTS


def _parse_iso(value: str, tz: ZoneInfo) -> datetime:
    text = str(value).strip()
    if not text:
        raise TimeframeError("invalid_time", "The requested time is empty.")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TimeframeError(
            "invalid_time",
            "I couldn't understand that date/time. Please use YYYY-MM-DD or an ISO timestamp.",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(timezone.utc)


def _parse_date(value: str) -> date_cls:
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%b %d %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise TimeframeError(
        "invalid_date",
        "I couldn't understand that date. Please use YYYY-MM-DD.",
    )


def _shift_window(day: date_cls, shift: dict, tz: ZoneInfo) -> tuple[datetime, datetime]:
    sh, sm = map(int, str(shift["start"]).split(":"))
    eh, em = map(int, str(shift["end"]).split(":"))
    start = datetime.combine(day, time(sh, sm), tz)
    end = datetime.combine(day, time(eh, em), tz)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _named_shift(config: dict, name: str) -> Optional[dict]:
    wanted = str(name or "").strip().upper()
    wanted = re.sub(r"^SHIFT[\s_-]*", "", wanted)
    for shift in shifts(config):
        if str(shift.get("name", "")).strip().upper() == wanted:
            return shift
    return None


def _current_shift(config: dict, now_local: datetime) -> Optional[dict]:
    for shift in shifts(config):
        start, end = _shift_window(now_local.date(), shift, now_local.tzinfo)
        if start <= now_local < end:
            return shift
    return None


def _local_midnight(day: date_cls, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tz)


def _finish(start_local: datetime, end_local: datetime, label: str, tz: ZoneInfo,
            max_days: int) -> TimeRange:
    if end_local <= start_local:
        raise TimeframeError("invalid_range", "The end of the time range must be after the start.")
    span = end_local - start_local
    if span > timedelta(days=max_days):
        raise TimeframeError(
            "range_too_large",
            f"That range is too large. I can look at up to {max_days} days at a time; please narrow it.",
            {"max_days": max_days},
        )
    return TimeRange(
        start=start_local.astimezone(timezone.utc),
        end=end_local.astimezone(timezone.utc),
        timezone=str(tz),
        label=label,
        start_local=start_local,
        end_local=end_local,
    )


def resolve_range(
    config: dict,
    *,
    period: Optional[str] = None,
    date: Optional[str] = None,
    shift: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    now: Optional[datetime] = None,
    max_days: Optional[int] = None,
) -> TimeRange:
    tz = plant_timezone(config)
    max_days = int(max_days or settings.ai_max_range_days)
    now_utc = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)

    if start or end:
        start_dt = _parse_iso(start, tz) if start else None
        end_dt = _parse_iso(end, tz) if end else None
        if start_dt and not end_dt:
            end_dt = now_utc
        if end_dt and not start_dt:
            start_dt = end_dt - timedelta(days=1)
        if not start_dt or not end_dt:
            raise TimeframeError("ambiguous_date", "Which date range should I use?")
        return _finish(start_dt.astimezone(tz), end_dt.astimezone(tz), "custom range", tz, max_days)

    key = (period or "").strip().lower().replace("-", "_").replace(" ", "_")
    day: Optional[date_cls] = _parse_date(date) if date else None

    if shift:
        chosen = _named_shift(config, shift)
        if not chosen:
            names = ", ".join(str(s.get("name")) for s in shifts(config))
            raise TimeframeError(
                "ambiguous_shift",
                f"Which shift do you mean? Available shifts: {names}.",
                {"shifts": [str(s.get("name")) for s in shifts(config)]},
            )
        target_day = day or now_local.date()
        start_local, end_local = _shift_window(target_day, chosen, tz)
        label = f"Shift {chosen.get('name')} on {target_day.isoformat()}"
        if key in ("today", "current", "current_shift", "this_shift") and not date:
            if start_local > now_local:
                start_local -= timedelta(days=1)
                end_local -= timedelta(days=1)
                label = f"Shift {chosen.get('name')} (previous day)"
        return _finish(start_local, end_local, label, tz, max_days)

    if day is not None:
        start_local = _local_midnight(day, tz)
        end_local = start_local + timedelta(days=1)
        return _finish(start_local, end_local, day.isoformat(), tz, max_days)

    if not key:
        raise TimeframeError(
            "ambiguous_date",
            "Which date or period should I use? For example: today, yesterday, last 7 days, or a date.",
        )

    if key in ("today", "current_day"):
        start_local = _local_midnight(now_local.date(), tz)
        return _finish(start_local, now_local, "today", tz, max_days)
    if key in ("yesterday", "previous_day"):
        target = now_local.date() - timedelta(days=1)
        start_local = _local_midnight(target, tz)
        return _finish(start_local, start_local + timedelta(days=1), "yesterday", tz, max_days)
    if key in ("last_24_hours", "past_24_hours"):
        return _finish(now_local - timedelta(hours=24), now_local, "last 24 hours", tz, max_days)
    if key in ("this_week", "current_week"):
        target = now_local.date() - timedelta(days=now_local.weekday())
        return _finish(_local_midnight(target, tz), now_local, "this week", tz, max_days)
    if key in ("last_week", "previous_week"):
        target = now_local.date() - timedelta(days=now_local.weekday() + 7)
        start_local = _local_midnight(target, tz)
        return _finish(start_local, start_local + timedelta(days=7), "last week", tz, max_days)
    if key in ("this_month", "current_month"):
        return _finish(_local_midnight(now_local.date().replace(day=1), tz), now_local,
                       "this month", tz, max_days)
    if key in ("last_month", "previous_month"):
        first = now_local.date().replace(day=1)
        end = first
        start = (first - timedelta(days=1)).replace(day=1)
        return _finish(_local_midnight(start, tz), _local_midnight(end, tz), "last month", tz, max_days)
    if key in ("this_shift", "current_shift"):
        chosen = _current_shift(config, now_local)
        if not chosen:
            raise TimeframeError("ambiguous_shift", "Which shift should I use?", {})
        start_local, end_local = _shift_window(now_local.date(), chosen, tz)
        if start_local > now_local:
            start_local -= timedelta(days=1)
            end_local -= timedelta(days=1)
        return _finish(start_local, min(end_local, now_local), f"current shift ({chosen.get('name')})",
                       tz, max_days)
    if key in ("previous_shift", "last_shift"):
        current = _current_shift(config, now_local)
        ordered = shifts(config)
        names = [str(s.get("name")) for s in ordered]
        index = names.index(str(current.get("name"))) if current and str(current.get("name")) in names else 0
        previous = ordered[(index - 1) % len(ordered)]
        target_day = now_local.date()
        start_local, end_local = _shift_window(target_day, previous, tz)
        if start_local > now_local:
            start_local -= timedelta(days=1)
            end_local -= timedelta(days=1)
        return _finish(start_local, end_local, f"previous shift ({previous.get('name')})", tz, max_days)

    match = re.match(r"^last_(\d+)_(hours?|days?|weeks?)$", key)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        delta = timedelta(hours=amount) if unit.startswith("hour") else (
            timedelta(days=amount * 7) if unit.startswith("week") else timedelta(days=amount))
        return _finish(now_local - delta, now_local, f"last {amount} {unit}", tz, max_days)

    raise TimeframeError(
        "ambiguous_date",
        "I couldn't match that period. Please say today, yesterday, last 7 days, a date, or a shift.",
    )
