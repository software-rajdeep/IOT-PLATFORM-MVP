"""CSV export (streamed) and shift reports, generic over template field roles."""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, time, timedelta
from typing import Iterator, Optional
from zoneinfo import ZoneInfo

from ..db import _get_pool, fetch_all, fetch_one
from .instances import load_config

DEFAULT_SHIFTS = [{"name": "A", "start": "06:00", "end": "14:00"},
                  {"name": "B", "start": "14:00", "end": "22:00"},
                  {"name": "C", "start": "22:00", "end": "06:00"}]

# Guard the numeric cast the same way the dashboard does: one blank/textual value
# must not fail the whole shift report.
_NUMERIC_RE = r"^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$"


def export_csv(instance_id: str, template: dict, start: Optional[datetime], end: Optional[datetime],
               device_id: Optional[str] = None) -> Iterator[str]:
    keys = [c["key"] for c in (template.get("requires") or []) + (template.get("optional") or [])] + \
           [o["key"] for o in template.get("outputs") or []]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "device_id", *keys])
    yield buf.getvalue()
    sql = "SELECT ts, device_id, fields FROM metrics.measurements WHERE instance_id = %s AND fields IS NOT NULL"
    params: list = [instance_id]
    if start:
        sql += " AND ts >= %s"
        params.append(start)
    if end:
        sql += " AND ts < %s"
        params.append(end)
    if device_id:
        sql += " AND device_id = %s"
        params.append(device_id)
    sql += " ORDER BY ts"
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(name="export_cursor") as cur:  # server-side cursor: constant memory
            cur.itersize = 2000
            cur.execute(sql, params)
            for row in cur:
                fields = row["fields"] if isinstance(row["fields"], dict) else json.loads(row["fields"])
                buf.seek(0)
                buf.truncate()
                writer.writerow([row["ts"].isoformat(), row["device_id"], *[fields.get(k, "") for k in keys]])
                yield buf.getvalue()
        conn.commit()
    finally:
        pool.putconn(conn)


def _shift_window(day: date, shift: dict, tz: ZoneInfo) -> tuple[datetime, datetime]:
    sh, sm = map(int, shift["start"].split(":"))
    eh, em = map(int, shift["end"].split(":"))
    start = datetime.combine(day, time(sh, sm), tz)
    end = datetime.combine(day, time(eh, em), tz)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def shift_report(instance_id: str, template: dict, day: date) -> dict:
    config = load_config(instance_id)
    roles = template.get("field_roles") or {}
    tz = ZoneInfo(config.get("report.timezone") or "Asia/Kolkata")
    try:
        shifts = json.loads(config.get("report.shifts") or "null") or DEFAULT_SHIFTS
    except ValueError:
        shifts = DEFAULT_SHIFTS
    primary, status, group = roles.get("primary"), roles.get("status") or "quality.status", roles.get("group")
    lower = config.get(roles.get("lower")) if roles.get("lower") else None
    upper = config.get(roles.get("upper")) if roles.get("upper") else None
    target = config.get(roles.get("target")) if roles.get("target") else None

    out_shifts = []
    for shift in shifts:
        start, end = _shift_window(day, shift, tz)
        s = fetch_one(
            """
            SELECT count(*) AS n,
                   count(*) FILTER (WHERE fields->>%(st)s = 'PASS') AS pass_n,
                   count(*) FILTER (WHERE fields->>%(st)s = 'FAIL') AS fail_n,
                   avg(v) AS avg, min(v) AS min, max(v) AS max, stddev_samp(v) AS std
            FROM (SELECT fields, CASE WHEN (fields->>%(p)s) ~ %(num)s THEN (fields->>%(p)s)::float END AS v
                  FROM metrics.measurements
                  WHERE instance_id = %(i)s AND ts >= %(a)s AND ts < %(b)s AND fields IS NOT NULL) s
            """, {"st": status, "p": primary, "num": _NUMERIC_RE,
                  "i": instance_id, "a": start, "b": end}) or {}
        hourly = fetch_all(
            """
            SELECT date_trunc('hour', ts AT TIME ZONE %(tz)s) AS hour, count(*) AS n,
                   avg(v) AS avg,
                   count(*) FILTER (WHERE fields->>%(st)s = 'FAIL') AS fail_n
            FROM (SELECT ts, fields, CASE WHEN (fields->>%(p)s) ~ %(num)s THEN (fields->>%(p)s)::float END AS v
                  FROM metrics.measurements
                  WHERE instance_id = %(i)s AND ts >= %(a)s AND ts < %(b)s AND fields IS NOT NULL) s
            GROUP BY 1 ORDER BY 1
            """, {"tz": str(tz), "p": primary, "num": _NUMERIC_RE, "st": status,
                  "i": instance_id, "a": start, "b": end})
        groups = []
        if group:
            groups = fetch_all(
                """
                SELECT fields->>%(g)s AS grp, count(*) AS n, avg(v) AS avg,
                       count(*) FILTER (WHERE fields->>%(st)s = 'FAIL') AS fail_n
                FROM (SELECT fields, CASE WHEN (fields->>%(p)s) ~ %(num)s THEN (fields->>%(p)s)::float END AS v
                      FROM metrics.measurements
                      WHERE instance_id = %(i)s AND ts >= %(a)s AND ts < %(b)s AND fields ? %(g)s) s
                GROUP BY 1 ORDER BY 1
                """, {"g": group, "p": primary, "num": _NUMERIC_RE, "st": status,
                      "i": instance_id, "a": start, "b": end})
        alerts_n = fetch_one(
            "SELECT count(*) AS n FROM metrics.alert_events WHERE instance_id = %s AND created_at >= %s AND created_at < %s",
            (instance_id, start, end))
        n = int(s.get("n") or 0)
        judged = int(s.get("pass_n") or 0) + int(s.get("fail_n") or 0)
        out_shifts.append({
            "name": shift["name"], "start": start.isoformat(), "end": end.isoformat(),
            "samples": n, "pass_count": int(s.get("pass_n") or 0), "fail_count": int(s.get("fail_n") or 0),
            "pass_percentage": round(int(s.get("pass_n") or 0) / judged * 100, 2) if judged else None,
            "average": s.get("avg"), "minimum": s.get("min"), "maximum": s.get("max"), "std_dev": s.get("std"),
            "alerts": int(alerts_n["n"]) if alerts_n else 0,
            "hourly": [{"hour": h["hour"].strftime("%H:%M"), "samples": int(h["n"]), "average": h["avg"],
                        "fail_count": int(h["fail_n"])} for h in hourly],
            "groups": [{"group": g["grp"], "samples": int(g["n"]), "average": g["avg"], "fail_count": int(g["fail_n"])}
                       for g in groups],
        })
    return {"date": day.isoformat(), "timezone": str(tz), "label": roles.get("label") or "Value",
            "unit": roles.get("unit") or "", "target": target, "lower": lower, "upper": upper, "shifts": out_shifts}
