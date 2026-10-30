"""Approved analytics functions for the AI assistant.

Only these functions are exposed as tools. They never accept SQL, table names
or arbitrary column names: machine/sensor fields are resolved from the
authenticated company's metadata and every value is passed as a SQL parameter.

Each result is returned as structured data and is treated as untrusted content
by the orchestrator (it is data, never instructions).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Union

from ...config import settings
from ...db import fetch_all, fetch_one
from ..devices import load_device
from ..instances import get_instance, instance_device_ids, load_config
from ..limits import bounds
from ..mappings import active_mapping
from ..opposite_calibration import limit_readings
from ..sbs_calibration import DEFAULT_INPUT_KEY, derived_key, parse_baselines, sensor_keys
from ..templates import load_template
from .context import AIContext
from .timeframes import TimeframeError, TimeRange, plant_timezone, resolve_range

NUMERIC_RE = r"^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$"

METRICS = ("thickness", "displacement", "deviation", "all")


class AnalyticsError(Exception):
    """A safe, user-facing analytics failure."""

    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


def _json(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _num(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value, digits: int = 6) -> Optional[float]:
    number = _num(value)
    return round(number, digits) if number is not None else None


def _normalise(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _path_tokens(path: str) -> list[str]:
    """Turn a mapping dot/bracket path into PostgreSQL jsonb #>> tokens."""
    text = str(path or "").strip()
    if text.startswith("$"):
        text = text[1:]
    text = text.lstrip(".")
    return [token for token in re.split(r"\.|\[|\]", text) if token != ""]


def _numeric_expr(field: str, param: str) -> str:
    return (
        f"CASE WHEN (fields->>%({param})s) ~ '{NUMERIC_RE}' "
        f"THEN (fields->>%({param})s)::float END"
    )


def _metric_value(meta: dict, metric: str, sensor_key: Optional[str]) -> tuple[str, str]:
    """Return ``(json_field, unit)`` for the requested measurement type."""
    roles = meta["roles"]
    input_key = meta["input_key"]
    if metric in ("displacement", "raw"):
        return sensor_key or input_key, roles.get("unit") or "mm"
    if metric == "deviation":
        return roles.get("deviation") or "thickness.deviation_mm", roles.get("unit") or "mm"
    if sensor_key:
        return derived_key("thickness.sensor_mm", sensor_key, input_key), roles.get("unit") or "mm"
    return roles.get("primary") or "thickness.actual_mm", roles.get("unit") or "mm"


class AnalyticsService:
    # ------------------------------------------------------------ machines
    def _machine_rows(self, ctx: AIContext) -> list[dict]:
        return fetch_all(
            """
            SELECT t.instance_id, t.name, t.asset_id, t.plant_key, t.status,
                   t.template_id, tpl.name AS template_name, tpl.processor_key,
                   tpl.field_roles, tpl.requires
            FROM core.template_instances t
            JOIN core.application_templates tpl ON tpl.template_id = t.template_id
            WHERE t.company_id = %s
            ORDER BY t.name
            """,
            (ctx.company_id,),
        )

    def _meta(self, ctx: AIContext, instance_id: str) -> dict:
        row = get_instance(instance_id, ctx.company_id)
        if not row:
            raise AnalyticsError("machine_not_found", f"I couldn't find machine '{instance_id}' in your account.")
        template = load_template(row["template_id"])
        if not template:
            raise AnalyticsError("machine_not_found", "That machine's application template is not available.")
        config = load_config(instance_id)
        roles = _json(template.get("field_roles"), {}) or {}
        requires = _json(template.get("requires"), []) or []
        input_key = roles.get("input") or next(
            (item.get("key") for item in requires if item.get("key")), DEFAULT_INPUT_KEY)
        return {
            "instance_id": instance_id,
            "name": row["name"],
            "asset_id": row.get("asset_id"),
            "plant": row.get("plant_key"),
            "status": row.get("status"),
            "template_id": row.get("template_id"),
            "template_name": row.get("template_name"),
            "template": template,
            "processor_key": row.get("processor_key"),
            "roles": roles,
            "input_key": input_key,
            "config": config,
            "unit": roles.get("unit") or "mm",
            "_latest_fields": self._latest_fields(ctx, instance_id),
        }

    def _latest_fields(self, ctx: AIContext, instance_id: str) -> dict:
        row = fetch_one(
            "SELECT fields FROM metrics.measurements WHERE company_id = %s AND instance_id = %s "
            "AND fields IS NOT NULL ORDER BY ts DESC LIMIT 1",
            (ctx.company_id, instance_id),
        )
        return _json((row or {}).get("fields"), {})

    def _sensors(self, meta: dict, fields: Optional[dict] = None) -> list[dict]:
        fields = fields if fields is not None else {}
        input_key = meta["input_key"]
        keys = sensor_keys(fields, input_key)
        if meta.get("processor_key") == "opposite_thickness":
            # Opposite-side is a two-sensor layout: A + B only.
            keys = limit_readings(keys)
        if not keys:
            keys = [key for key in parse_baselines(meta["config"]) if key == input_key or key.startswith(input_key)]
        if not keys:
            keys = [input_key]
        configured_ref = str(meta["config"].get("reference.sensor_key") or "").strip()
        if not configured_ref and str(meta["config"].get("reference.enabled") or "").lower() in {
                "1", "true", "yes", "on"} and len(keys) > 1:
            configured_ref = keys[-1]
        out = []
        for index, key in enumerate(keys):
            suffix = key[len(input_key):] if key.startswith(input_key) else ""
            out.append({
                "key": key,
                "label": f"Sensor {index + 1}" + (" (reference)" if key == configured_ref else ""),
                "index": index + 1,
                "is_reference": key == configured_ref,
                "displacement_field": key,
                "thickness_field": f"thickness.sensor_mm{suffix}",
                "unit": meta["unit"],
            })
        return out

    def list_machines(self, ctx: AIContext) -> dict:
        rows = self._machine_rows(ctx)
        ids = [row["instance_id"] for row in rows]
        latest = {}
        if ids:
            latest_rows = fetch_all(
                """
                SELECT DISTINCT ON (instance_id) instance_id, ts, fields
                FROM metrics.measurements
                WHERE company_id = %s AND instance_id = ANY(%s)
                ORDER BY instance_id, ts DESC
                """,
                (ctx.company_id, ids),
            )
            latest = {row["instance_id"]: row for row in latest_rows}
        machines = []
        for row in rows:
            meta = {
                "instance_id": row["instance_id"],
                "name": row["name"],
                "asset_id": row.get("asset_id"),
                "plant": row.get("plant_key"),
                "status": row.get("status"),
                "template_id": row.get("template_id"),
                "template_name": row.get("template_name"),
                "roles": _json(row.get("field_roles"), {}) or {},
                "input_key": (_json(row.get("field_roles"), {}) or {}).get("input")
                or next((item.get("key") for item in (_json(row.get("requires"), []) or [])
                         if item.get("key")), DEFAULT_INPUT_KEY),
                "config": {},
                "unit": (_json(row.get("field_roles"), {}) or {}).get("unit") or "mm",
            }
            latest_row = latest.get(row["instance_id"]) or {}
            fields = _json(latest_row.get("fields"), {})
            machines.append({
                "machine_id": row["instance_id"],
                "name": row["name"],
                "asset_id": row.get("asset_id"),
                "plant": row.get("plant_key"),
                "status": row.get("status"),
                "application": row.get("template_name"),
                "last_reading": latest_row.get("ts").isoformat() if latest_row.get("ts") else None,
                "sensors": self._sensors(meta, fields),
                "measurement_types": ["thickness", "displacement", "deviation"],
                "units": {"thickness": meta["unit"], "displacement": meta["unit"],
                          "deviation": meta["unit"]},
            })
        return {
            "source": "core.template_instances",
            "count": len(machines),
            "machines": machines,
        }

    def machine_inventory(self, ctx: AIContext, limit: int = 40) -> list[dict]:
        data = self.list_machines(ctx)
        return data["machines"][:limit]

    def _resolve_machine(self, ctx: AIContext, machine_id: Optional[str],
                         machine_name: Optional[str]) -> dict:
        rows = self._machine_rows(ctx)
        if not rows:
            raise AnalyticsError("no_machines", "No machines are configured for your account yet.")
        wanted_id = str(machine_id or "").strip()
        wanted_name = str(machine_name or "").strip()

        if wanted_id:
            match = next((row for row in rows if str(row["instance_id"]) == wanted_id), None)
            if match:
                return self._meta(ctx, match["instance_id"])
            wanted_name = wanted_name or wanted_id

        if wanted_name:
            needle = _normalise(wanted_name)
            exact = [
                row for row in rows
                if _normalise(row["instance_id"]) == needle
                or _normalise(row["name"]) == needle
                or _normalise(row.get("asset_id")) == needle
            ]
            matches = exact or [
                row for row in rows
                if needle and (needle in _normalise(row["name"])
                               or needle in _normalise(row.get("asset_id"))
                               or needle in _normalise(row["instance_id"]))
            ]
            if len(matches) == 1:
                return self._meta(ctx, matches[0]["instance_id"])
            if len(matches) > 1:
                raise AnalyticsError(
                    "ambiguous_machine",
                    "Which machine do you mean? " + ", ".join(
                        f"{row['name']} ({row['instance_id']})" for row in matches[:8]),
                    {"candidates": [row["instance_id"] for row in matches[:8]]},
                )
            raise AnalyticsError(
                "machine_not_found",
                f"I couldn't find a machine matching '{wanted_name}'. "
                "Ask me to list your machines if you are unsure.",
            )

        if len(rows) == 1:
            return self._meta(ctx, rows[0]["instance_id"])
        raise AnalyticsError(
            "ambiguous_machine",
            "Which machine should I check? " + ", ".join(
                f"{row['name']} ({row['instance_id']})" for row in rows[:8]),
            {"candidates": [row["instance_id"] for row in rows[:8]]},
        )

    def _resolve_sensor(self, meta: dict, sensor: Optional[str], fields: Optional[dict] = None):
        wanted = str(sensor or "").strip()
        catalog = self._sensors(
            meta, fields if fields is not None else (meta.get("_latest_fields") or {}))
        if not wanted:
            return None
        needle = _normalise(wanted)
        matches = [
            item for item in catalog
            if _normalise(item["key"]) == needle
            or _normalise(item["label"]) == needle
            or _normalise(f"sensor {item['index']}") == needle
            or needle == str(item["index"])
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AnalyticsError(
                "ambiguous_sensor",
                "Which sensor do you mean? " + ", ".join(item["label"] for item in matches),
                {"sensors": [item["key"] for item in matches]},
            )
        if needle in ("sensor", "sensors", "all"):
            if len(catalog) == 1:
                return catalog[0]
            raise AnalyticsError(
                "ambiguous_sensor",
                "Which sensor do you mean? " + ", ".join(item["label"] for item in catalog),
                {"sensors": [item["key"] for item in catalog]},
            )
        raise AnalyticsError(
            "invalid_sensor",
            f"'{wanted}' is not a sensor on this machine. Available: "
            + ", ".join(item["label"] for item in catalog),
            {"sensors": [item["key"] for item in catalog]},
        )

    # ------------------------------------------------------------ ranges/stats
    def _range(self, meta: dict, args: dict) -> TimeRange:
        try:
            return resolve_range(
                meta["config"],
                period=args.get("period"),
                date=args.get("date"),
                shift=args.get("shift"),
                start=args.get("start"),
                end=args.get("end"),
                max_days=settings.ai_max_range_days,
            )
        except TimeframeError as exc:
            raise AnalyticsError(exc.code, exc.message, exc.details) from exc

    def _stats(self, ctx: AIContext, meta: dict, timeframe: TimeRange,
               value_field: str, status_field: Optional[str]) -> dict:
        params = {
            "c": ctx.company_id,
            "i": meta["instance_id"],
            "a": timeframe.start,
            "b": timeframe.end,
            "field": value_field,
            "status_field": status_field,
        }
        status_expr = "CASE WHEN %(status_field)s IS NULL THEN NULL ELSE fields->>%(status_field)s END"
        sql = f"""
            SELECT count(*) AS samples,
                   count(v) AS values_count,
                   avg(v) AS average, min(v) AS minimum, max(v) AS maximum,
                   stddev_samp(v) AS std_dev,
                   count(*) FILTER (WHERE status = 'PASS') AS pass_count,
                   count(*) FILTER (WHERE status = 'FAIL') AS fail_count
            FROM (
                SELECT {_numeric_expr(value_field, "field")} AS v,
                       {status_expr} AS status
                FROM metrics.measurements
                WHERE company_id = %(c)s AND instance_id = %(i)s
                  AND ts >= %(a)s AND ts < %(b)s AND fields IS NOT NULL
            ) s
        """
        row = fetch_one(sql, params) or {}
        pass_count = int(row.get("pass_count") or 0)
        fail_count = int(row.get("fail_count") or 0)
        return {
            "samples": int(row.get("samples") or 0),
            "values_count": int(row.get("values_count") or 0),
            "average": _round(row.get("average")),
            "minimum": _round(row.get("minimum")),
            "maximum": _round(row.get("maximum")),
            "std_dev": _round(row.get("std_dev")),
            "pass_count": pass_count,
            "fail_count": fail_count,
            "pass_percentage": round(pass_count / (pass_count + fail_count) * 100, 2)
            if (pass_count + fail_count) else None,
        }

    def _limits(self, meta: dict) -> tuple[Optional[float], Optional[float], bool]:
        roles = meta["roles"]
        active_key = "thickness.limit_active"
        lower = meta["config"].get(roles.get("lower") or "thickness.lower_limit_mm")
        upper = meta["config"].get(roles.get("upper") or "thickness.upper_limit_mm")
        lower_num, upper_num, active = bounds(
            meta["config"], active_key,
            roles.get("lower") or "thickness.lower_limit_mm",
            roles.get("upper") or "thickness.upper_limit_mm",
        )
        return lower_num, upper_num, active

    def _base_result(self, meta: dict, timeframe: Union[TimeRange, dict, None], *, metric: str,
                     sensor: Optional[dict], data: dict) -> dict:
        time_range = (
            timeframe.as_dict() if hasattr(timeframe, "as_dict") else timeframe
        )
        result = {
            "source": "metrics.measurements",
            "data_source": "metrics.measurements",
            "machine": {"machine_id": meta["instance_id"], "name": meta["name"],
                        "asset_id": meta.get("asset_id"), "plant": meta.get("plant")},
            "metric": metric,
            "measurement_type": metric,
            "unit": meta["unit"],
            "sensor": sensor,
            "time_range": time_range,
            "units": {metric: meta["unit"]},
        }
        result.update(data)
        return result

    # ------------------------------------------------------------ tools
    def get_reading_count(self, ctx: AIContext, machine_id: Optional[str] = None,
                          machine_name: Optional[str] = None, sensor: Optional[str] = None,
                          metric: str = "thickness", period: Optional[str] = None,
                          date: Optional[str] = None, shift: Optional[str] = None,
                          start: Optional[str] = None, end: Optional[str] = None) -> dict:
        meta = self._resolve_machine(ctx, machine_id, machine_name)
        sensor_def = self._resolve_sensor(meta, sensor)
        timeframe = self._range(meta, {"period": period, "date": date, "shift": shift,
                                       "start": start, "end": end})
        if metric not in METRICS:
            metric = "thickness"
        value_field, _unit = _metric_value(meta, metric, (sensor_def or {}).get("key"))
        stats = self._stats(ctx, meta, timeframe, value_field, meta["roles"].get("status"))
        return self._base_result(
            meta, timeframe, metric=metric, sensor=sensor_def,
            data={"reading_count": stats["samples"], "readings": stats["samples"],
                  "with_values": stats["values_count"]},
        )

    def get_thickness_statistics(self, ctx: AIContext, machine_id: Optional[str] = None,
                                 machine_name: Optional[str] = None, sensor: Optional[str] = None,
                                 metric: str = "thickness", period: Optional[str] = None,
                                 date: Optional[str] = None, shift: Optional[str] = None,
                                 start: Optional[str] = None, end: Optional[str] = None) -> dict:
        meta = self._resolve_machine(ctx, machine_id, machine_name)
        sensor_def = self._resolve_sensor(meta, sensor)
        timeframe = self._range(meta, {"period": period, "date": date, "shift": shift,
                                       "start": start, "end": end})
        if metric not in METRICS:
            metric = "thickness"
        value_field, _unit = _metric_value(meta, metric, (sensor_def or {}).get("key"))
        stats = self._stats(ctx, meta, timeframe, value_field, meta["roles"].get("status"))
        lower, upper, active = self._limits(meta)
        return self._base_result(
            meta, timeframe, metric=metric, sensor=sensor_def,
            data={"statistics": stats, "has_data": stats["values_count"] > 0,
                  "limits": {"lower": lower, "upper": upper, "active": active},
                  "target": _round(meta["config"].get(meta["roles"].get("target") or "thickness.target_mm"))},
        )

    def get_latest_measurement(self, ctx: AIContext, machine_id: Optional[str] = None,
                               machine_name: Optional[str] = None, sensor: Optional[str] = None,
                               metric: str = "thickness", period: Optional[str] = None,
                               date: Optional[str] = None, shift: Optional[str] = None,
                               start: Optional[str] = None, end: Optional[str] = None) -> dict:
        """Latest non-NULL sensor sample, optionally constrained to a time range.

        No range is required: "latest" means the most recent stored measurement.
        If the caller supplies a range, the same plant-local/inclusive-exclusive
        rules as every other tool apply.
        """
        meta = self._resolve_machine(ctx, machine_id, machine_name)
        sensor_def = self._resolve_sensor(meta, sensor)
        if metric not in METRICS:
            metric = "thickness"
        value_field, _unit = _metric_value(meta, metric, (sensor_def or {}).get("key"))

        timeframe = None
        if any((period, date, shift, start, end)):
            timeframe = self._range(meta, {"period": period, "date": date, "shift": shift,
                                           "start": start, "end": end})

        params = {
            "c": ctx.company_id,
            "i": meta["instance_id"],
            "field": value_field,
            "status_field": meta["roles"].get("status"),
        }
        where = ["company_id = %(c)s", "instance_id = %(i)s", "fields IS NOT NULL"]
        if timeframe is not None:
            where.extend(["ts >= %(a)s", "ts < %(b)s"])
            params["a"] = timeframe.start
            params["b"] = timeframe.end
        sql = f"""
            SELECT ts, value, status
            FROM (
                SELECT ts,
                       {_numeric_expr(value_field, "field")} AS value,
                       CASE WHEN %(status_field)s IS NULL THEN NULL
                            ELSE fields->>%(status_field)s END AS status
                FROM metrics.measurements
                WHERE {' AND '.join(where)}
            ) s
            WHERE value IS NOT NULL
            ORDER BY ts DESC
            LIMIT 1
        """
        row = fetch_one(sql, params) or {}
        ts = row.get("ts")
        tz = plant_timezone(meta["config"])
        if timeframe is not None:
            time_range = timeframe.as_dict()
        elif ts is not None:
            local = ts.astimezone(tz)
            time_range = {
                "start": local.isoformat(),
                "end": local.isoformat(),
                "start_local": local.isoformat(),
                "end_local": local.isoformat(),
                "timezone": str(tz),
                "label": "latest reading",
            }
        else:
            time_range = {"start": None, "end": None, "timezone": str(tz),
                          "label": "latest reading"}

        if not row:
            return self._base_result(
                meta, time_range, metric=metric, sensor=sensor_def,
                data={"latest": None, "value": None, "timestamp": None,
                      "timestamp_utc": None, "quality_status": None,
                      "database_rows": 0, "sensor_samples": 0,
                      "has_data": False, "empty": True},
            )

        local = ts.astimezone(tz)
        value = _round(row.get("value"))
        return self._base_result(
            meta, time_range, metric=metric, sensor=sensor_def,
            data={
                "latest": {
                    "timestamp": local.isoformat(),
                    "timestamp_utc": ts.astimezone(timezone.utc).isoformat(),
                    "value": value,
                    "quality_status": row.get("status"),
                    "sensor": sensor_def,
                },
                "value": value,
                "timestamp": local.isoformat(),
                "timestamp_utc": ts.astimezone(timezone.utc).isoformat(),
                "quality_status": row.get("status"),
                # One measurement row = one sensor sample for the selected field.
                "database_rows": 1,
                "sensor_samples": 1,
                "has_data": True,
                "empty": False,
            },
        )

    def compare_filtered_unfiltered(self, ctx: AIContext, machine_id: Optional[str] = None,
                                    machine_name: Optional[str] = None,
                                    period: Optional[str] = None, date: Optional[str] = None,
                                    shift: Optional[str] = None, start: Optional[str] = None,
                                    end: Optional[str] = None) -> dict:
        meta = self._resolve_machine(ctx, machine_id, machine_name)
        timeframe = self._range(meta, {"period": period, "date": date, "shift": shift,
                                       "start": start, "end": end})
        device_ids = instance_device_ids(meta["instance_id"])
        if not device_ids:
            device_ids = [meta["instance_id"]]
        raw_counts = fetch_one(
            """
            SELECT count(*) AS raw_count, min(ts) AS first_ts, max(ts) AS last_ts
            FROM metrics.raw_device_messages
            WHERE company_id = %s AND device_id = ANY(%s) AND ts >= %s AND ts < %s
            """,
            (ctx.company_id, device_ids, timeframe.start, timeframe.end),
        ) or {}
        processed = fetch_one(
            """
            SELECT count(*) AS processed_count
            FROM metrics.measurements
            WHERE company_id = %s AND instance_id = %s AND ts >= %s AND ts < %s
              AND fields IS NOT NULL
            """,
            (ctx.company_id, meta["instance_id"], timeframe.start, timeframe.end),
        ) or {}
        raw_count = int(raw_counts.get("raw_count") or 0)
        processed_count = int(processed.get("processed_count") or 0)
        try:
            window = int(float(meta["config"].get("filter.window") or 1))
        except (TypeError, ValueError):
            window = 1

        # Filtered statistics come from the processed measurement fields.
        primary_field = meta["roles"].get("primary") or "thickness.actual_mm"
        processed_stats = self._stats(
            ctx, meta, timeframe, primary_field, meta["roles"].get("status"))

        # Unfiltered statistics come from the raw payload. The numeric source path
        # is the active mapping's path for this machine's primary input/field, so
        # no customer-specific payload key is hard-coded.
        raw_path = None
        targets = {primary_field, meta.get("input_key"), "displacement.value_mm"}
        for device_id in device_ids:
            mapping = active_mapping(meta["instance_id"], device_id)
            if not mapping:
                continue
            for rule in (mapping.get("definition") or {}).get("fields", []) or []:
                if rule.get("target") in targets and rule.get("path"):
                    raw_path = rule["path"]
                    break
            if raw_path:
                break

        raw_stats = None
        raw_note = None
        if raw_path:
            tokens = _path_tokens(raw_path)
            lower, upper, _active = self._limits(meta)
            raw_where = [
                "company_id = %(c)s", "device_id = ANY(%(devices)s)",
                "ts >= %(a)s", "ts < %(b)s", "raw_payload IS NOT NULL",
            ]
            raw_params = {
                "c": ctx.company_id, "devices": device_ids,
                "a": timeframe.start, "b": timeframe.end, "path": tokens,
            }
            select_parts = [
                "count(*) AS messages",
                "count(value) AS values_count",
                "avg(value) AS average",
                "min(value) AS minimum",
                "max(value) AS maximum",
            ]
            if lower is not None:
                select_parts.append(
                    "count(*) FILTER (WHERE value < %(lower)s) AS below_count")
                raw_params["lower"] = lower
            else:
                select_parts.append("NULL AS below_count")
            if upper is not None:
                select_parts.append(
                    "count(*) FILTER (WHERE value > %(upper)s) AS above_count")
                raw_params["upper"] = upper
            else:
                select_parts.append("NULL AS above_count")
            raw_sql = f"""
                SELECT {', '.join(select_parts)}
                FROM (
                    SELECT CASE
                             WHEN (raw_payload #>> %(path)s) ~ '{NUMERIC_RE}'
                             THEN (raw_payload #>> %(path)s)::float
                           END AS value
                    FROM metrics.raw_device_messages
                    WHERE {' AND '.join(raw_where)}
                ) s
            """
            raw_row = fetch_one(raw_sql, raw_params) or {}
            raw_stats = {
                "messages": int(raw_row.get("messages") or 0),
                "values_count": int(raw_row.get("values_count") or 0),
                "average": _round(raw_row.get("average")),
                "minimum": _round(raw_row.get("minimum")),
                "maximum": _round(raw_row.get("maximum")),
                "below_count": int(raw_row["below_count"]) if raw_row.get("below_count") is not None else None,
                "above_count": int(raw_row["above_count"]) if raw_row.get("above_count") is not None else None,
                "path": raw_path,
                "note": "Numeric values extracted from raw_payload using the active mapping path.",
            }
        else:
            raw_note = ("No active mapping path was found for the raw payload, so raw value "
                        "statistics are unavailable. Raw message counts are still reported.")

        result = self._base_result(
            meta, timeframe, metric="readings", sensor=None,
            data={
                "raw_readings": raw_count,
                "unfiltered_readings": raw_count,
                "processed_readings": processed_count,
                "filtered_readings": processed_count,
                "difference": raw_count - processed_count,
                "raw_statistics": raw_stats,
                "unfiltered_statistics": raw_stats,
                "processed_statistics": processed_stats,
                "filtered_statistics": processed_stats,
                "filter": {"window": window, "trim_pct": _num(meta["config"].get("filter.trim_pct")) or 0,
                           "active": window > 1},
                "note": "Raw/unfiltered counts come from metrics.raw_device_messages; processed/filtered "
                        "counts are stored measurements. A raw message may feed more than one application."
                        + (f" {raw_note}" if raw_note else ""),
            },
        )
        result["source"] = "metrics.raw_device_messages + metrics.measurements"
        result["data_source"] = result["source"]
        return result

    def get_out_of_range_readings(self, ctx: AIContext, machine_id: Optional[str] = None,
                                  machine_name: Optional[str] = None, sensor: Optional[str] = None,
                                  metric: str = "thickness", period: Optional[str] = None,
                                  date: Optional[str] = None, shift: Optional[str] = None,
                                  start: Optional[str] = None, end: Optional[str] = None) -> dict:
        meta = self._resolve_machine(ctx, machine_id, machine_name)
        sensor_def = self._resolve_sensor(meta, sensor)
        timeframe = self._range(meta, {"period": period, "date": date, "shift": shift,
                                       "start": start, "end": end})
        if metric not in METRICS:
            metric = "thickness"
        value_field, _unit = _metric_value(meta, metric, (sensor_def or {}).get("key"))
        lower, upper, limit_active = self._limits(meta)
        params = {
            "c": ctx.company_id, "i": meta["instance_id"], "a": timeframe.start,
            "b": timeframe.end, "field": value_field,
            "status_field": meta["roles"].get("status"),
        }
        below_condition = None
        above_condition = None
        if lower is not None:
            below_condition = "v < %(lower)s"
            params["lower"] = lower
        if upper is not None:
            above_condition = "v > %(upper)s"
            params["upper"] = upper
        conditions = [c for c in (below_condition, above_condition) if c]
        condition = " OR ".join(conditions) if conditions else "FALSE"
        below = below_condition or "FALSE"
        above = above_condition or "FALSE"
        if lower is not None and upper is not None:
            in_range = "v >= %(lower)s AND v <= %(upper)s"
        elif lower is not None:
            in_range = "v >= %(lower)s"
        elif upper is not None:
            in_range = "v <= %(upper)s"
        else:
            in_range = "FALSE"
        sql = f"""
            SELECT count(v) AS values_count,
                   count(*) FILTER (WHERE {below}) AS below_count,
                   count(*) FILTER (WHERE {above}) AS above_count,
                   count(*) FILTER (WHERE {in_range}) AS in_range_count,
                   count(*) FILTER (WHERE {condition}) AS out_count,
                   min(v) FILTER (WHERE {condition}) AS out_min,
                   max(v) FILTER (WHERE {condition}) AS out_max,
                   count(*) FILTER (WHERE status = 'FAIL') AS fail_count
            FROM (
                SELECT {_numeric_expr(value_field, "field")} AS v,
                       CASE WHEN %(status_field)s IS NULL THEN NULL
                            ELSE fields->>%(status_field)s END AS status
                FROM metrics.measurements
                WHERE company_id = %(c)s AND instance_id = %(i)s
                  AND ts >= %(a)s AND ts < %(b)s AND fields IS NOT NULL
            ) s
        """
        row = fetch_one(sql, params) or {}
        samples = []
        if conditions:
            sample_sql = f"""
                SELECT ts, v AS value
                FROM (
                    SELECT ts, {_numeric_expr(value_field, "field")} AS v
                    FROM metrics.measurements
                    WHERE company_id = %(c)s AND instance_id = %(i)s
                      AND ts >= %(a)s AND ts < %(b)s AND fields IS NOT NULL
                ) s
                WHERE {condition}
                ORDER BY ts DESC
                LIMIT %(limit)s
            """
            sample_params = dict(params)
            sample_params["limit"] = min(20, settings.ai_max_tool_rows)
            samples = [
                {"ts": item["ts"].isoformat(), "value": _round(item["value"])}
                for item in fetch_all(sample_sql, sample_params)
            ]
        values_count = int(row.get("values_count") or 0)
        out_count = int(row.get("out_count") or 0)
        below_count = int(row.get("below_count") or 0) if lower is not None else None
        above_count = int(row.get("above_count") or 0) if upper is not None else None
        in_range_count = int(row.get("in_range_count") or 0) if conditions else None
        return self._base_result(
            meta, timeframe, metric=metric, sensor=sensor_def,
            data={
                "configured": bool(conditions),
                "limits_active": limit_active,
                "limits": {"lower": lower, "upper": upper},
                "thresholds": {"lower": lower, "upper": upper,
                               "configured": bool(conditions), "active": limit_active},
                "values_count": values_count,
                "below_count": below_count,
                "above_count": above_count,
                "in_range_count": in_range_count,
                "counts": {"below": below_count, "above": above_count,
                           "in_range": in_range_count},
                "out_of_range_count": out_count,
                "out_of_range_percentage": round(out_count / values_count * 100, 2) if values_count else None,
                "out_of_range_minimum": _round(row.get("out_min")),
                "out_of_range_maximum": _round(row.get("out_max")),
                "threshold_fail_count": int(row.get("fail_count") or 0),
                "samples": samples,
                "note": "Out-of-range uses the configured lower/upper limits; threshold_fail_count "
                        "uses the stored PASS/FAIL status."
                        if conditions else "No lower/upper limits are configured for this machine.",
            },
        )

    def get_threshold_violation_incidents(
            self, ctx: AIContext, machine_id: Optional[str] = None,
            machine_name: Optional[str] = None, sensor: Optional[str] = None,
            metric: str = "thickness", period: Optional[str] = None,
            date: Optional[str] = None, shift: Optional[str] = None,
            start: Optional[str] = None, end: Optional[str] = None) -> dict:
        meta = self._resolve_machine(ctx, machine_id, machine_name)
        sensor_def = self._resolve_sensor(meta, sensor)
        timeframe = self._range(meta, {"period": period, "date": date, "shift": shift,
                                       "start": start, "end": end})
        if metric not in METRICS:
            metric = "thickness"
        value_field, _unit = _metric_value(meta, metric, (sensor_def or {}).get("key"))
        lower, upper, limit_active = self._limits(meta)
        params = {"c": ctx.company_id, "i": meta["instance_id"], "a": timeframe.start,
                  "b": timeframe.end, "field": value_field,
                  "status_field": meta["roles"].get("status"),
                  "limit": min(settings.ai_max_tool_rows, 100) + 1}
        conditions = []
        if lower is not None:
            conditions.append("v < %(lower)s")
            params["lower"] = lower
        if upper is not None:
            conditions.append("v > %(upper)s")
            params["upper"] = upper
        if conditions:
            violation = "(" + " OR ".join(conditions) + ")"
            basis = "configured limits"
        elif meta["roles"].get("status"):
            violation = "(fields->>%(status_field)s = 'FAIL')"
            basis = "PASS/FAIL status"
        else:
            violation = "FALSE"
            basis = "no thresholds configured"
        sql = f"""
            WITH base AS (
                SELECT ts, {_numeric_expr(value_field, "field")} AS v, fields
                FROM metrics.measurements
                WHERE company_id = %(c)s AND instance_id = %(i)s
                  AND ts >= %(a)s AND ts < %(b)s AND fields IS NOT NULL
            ), readings AS (
                SELECT ts, v, {violation} AS violation,
                       lag({violation}) OVER (ORDER BY ts) AS previous
                FROM base
                WHERE v IS NOT NULL
            ), marked AS (
                SELECT *, CASE WHEN violation AND previous IS DISTINCT FROM TRUE
                          THEN 1 ELSE 0 END AS starts
                FROM readings
            ), grouped AS (
                SELECT *, sum(starts) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING
                                             AND CURRENT ROW) AS grp
                FROM marked
            )
            SELECT grp, min(ts) AS started_at, max(ts) AS ended_at, count(*) AS readings,
                   avg(v) AS average, min(v) AS minimum, max(v) AS maximum
            FROM grouped
            WHERE violation
            GROUP BY grp
            ORDER BY started_at DESC
            LIMIT %(limit)s
        """
        rows = fetch_all(sql, params)
        truncated = len(rows) > settings.ai_max_tool_rows
        rows = rows[:settings.ai_max_tool_rows]
        incidents = []
        for row in rows:
            started = row["started_at"]
            ended = row["ended_at"]
            incidents.append({
                "started_at": started.isoformat() if started else None,
                "ended_at": ended.isoformat() if ended else None,
                "duration_seconds": round((ended - started).total_seconds(), 2) if started and ended else None,
                "readings": int(row["readings"] or 0),
                "average": _round(row.get("average")),
                "minimum": _round(row.get("minimum")),
                "maximum": _round(row.get("maximum")),
            })
        return self._base_result(
            meta, timeframe, metric=metric, sensor=sensor_def,
            data={
                "incident_count": len(incidents),
                "incidents": incidents,
                "truncated": truncated,
                "basis": basis,
                "limits": {"lower": lower, "upper": upper, "active": limit_active},
                "note": "An incident is a continuous run of violating readings, separated by in-range readings.",
            },
        )

    def get_machine_connectivity(self, ctx: AIContext, machine_id: Optional[str] = None,
                                 machine_name: Optional[str] = None) -> dict:
        meta = self._resolve_machine(ctx, machine_id, machine_name)
        device_ids = instance_device_ids(meta["instance_id"])
        if not device_ids and meta.get("device_id"):
            device_ids = [meta["device_id"]]
        now = datetime.now(timezone.utc)
        devices = []
        for device_id in device_ids:
            device = load_device(device_id)
            if not device:
                continue
            last_seen = device.get("last_seen")
            aware = last_seen if last_seen and last_seen.tzinfo else (
                last_seen.replace(tzinfo=timezone.utc) if last_seen else None)
            age = round((now - aware).total_seconds(), 2) if aware else None
            params = _json(device.get("connection_parameters"), {})
            replay = bool(((params.get("database") or {}).get("replay") or {}).get("enabled"))
            online_within = 60 if replay else 10
            online = age is not None and age <= online_within
            devices.append({
                "device_id": device_id,
                "name": (device.get("asset_id") or "").strip() or device_id,
                "model": device.get("model_name"),
                "interface": device.get("interface"),
                "status": device.get("status"),
                "online": online,
                "communication": "ONLINE" if online else "OFFLINE",
                "last_seen": aware.isoformat() if aware else None,
                "seconds_since_last_seen": age,
                "replay": replay,
                "last_error": device.get("last_error"),
            })
        online_count = sum(1 for item in devices if item["online"])
        return {
            "source": "core.devices + metrics.measurements",
            "machine": {"machine_id": meta["instance_id"], "name": meta["name"],
                        "asset_id": meta.get("asset_id"), "plant": meta.get("plant")},
            "machine_status": meta["status"],
            "device_count": len(devices),
            "online_count": online_count,
            "offline_count": len(devices) - online_count,
            "devices": devices,
            "time_range": None,
            "units": {},
        }

    def get_daily_summary(self, ctx: AIContext, machine_id: Optional[str] = None,
                          machine_name: Optional[str] = None, sensor: Optional[str] = None,
                          metric: str = "thickness", period: Optional[str] = None,
                          date: Optional[str] = None, shift: Optional[str] = None,
                          start: Optional[str] = None, end: Optional[str] = None,
                          include_shifts: bool = False) -> dict:
        meta = self._resolve_machine(ctx, machine_id, machine_name)
        sensor_def = self._resolve_sensor(meta, sensor)
        timeframe = self._range(meta, {"period": period, "date": date, "shift": shift,
                                       "start": start, "end": end})
        if metric not in METRICS:
            metric = "thickness"
        value_field, _unit = _metric_value(meta, metric, (sensor_def or {}).get("key"))
        params = {"c": ctx.company_id, "i": meta["instance_id"], "a": timeframe.start,
                  "b": timeframe.end, "field": value_field,
                  "status_field": meta["roles"].get("status"), "tz": timeframe.timezone}
        rows = fetch_all(
            f"""
            SELECT (ts AT TIME ZONE %(tz)s)::date AS day,
                   count(*) AS samples, count(v) AS values_count,
                   avg(v) AS average, min(v) AS minimum, max(v) AS maximum,
                   count(*) FILTER (WHERE status = 'PASS') AS pass_count,
                   count(*) FILTER (WHERE status = 'FAIL') AS fail_count
            FROM (
                SELECT ts, {_numeric_expr(value_field, "field")} AS v,
                       CASE WHEN %(status_field)s IS NULL THEN NULL
                            ELSE fields->>%(status_field)s END AS status
                FROM metrics.measurements
                WHERE company_id = %(c)s AND instance_id = %(i)s
                  AND ts >= %(a)s AND ts < %(b)s AND fields IS NOT NULL
            ) s
            GROUP BY 1 ORDER BY 1
            """,
            params,
        )
        alerts = fetch_all(
            """
            SELECT (created_at AT TIME ZONE %s)::date AS day, count(*) AS n
            FROM metrics.alert_events
            WHERE instance_id = %s AND created_at >= %s AND created_at < %s
            GROUP BY 1
            """,
            (timeframe.timezone, meta["instance_id"], timeframe.start, timeframe.end),
        )
        alert_map = {row["day"]: int(row["n"] or 0) for row in alerts}
        by_day = {row["day"]: row for row in rows}
        first = timeframe.start_local.date()
        last = (timeframe.end_local - timedelta(microseconds=1)).date()
        days = []
        cursor = first
        while cursor <= last:
            row = by_day.get(cursor) or {}
            pass_count = int(row.get("pass_count") or 0)
            fail_count = int(row.get("fail_count") or 0)
            days.append({
                "date": cursor.isoformat(),
                "samples": int(row.get("samples") or 0),
                "values_count": int(row.get("values_count") or 0),
                "average": _round(row.get("average")),
                "minimum": _round(row.get("minimum")),
                "maximum": _round(row.get("maximum")),
                "pass_count": pass_count,
                "fail_count": fail_count,
                "pass_percentage": round(pass_count / (pass_count + fail_count) * 100, 2)
                if (pass_count + fail_count) else None,
                "alerts": alert_map.get(cursor, 0),
            })
            cursor += timedelta(days=1)
        data = {"daily_summaries": days, "day_count": len(days)}
        if include_shifts and len(days) <= settings.ai_max_range_days:
            from ..reports import shift_report
            shift_days = []
            for item in days:
                day_value = datetime.strptime(item["date"], "%Y-%m-%d").date()
                report = shift_report(meta["instance_id"], meta["template"], day_value)
                shift_days.append({"date": item["date"], "timezone": report.get("timezone"),
                                   "shifts": report.get("shifts", [])})
            data["shifts"] = shift_days
        return self._base_result(meta, timeframe, metric=metric, sensor=sensor_def, data=data)

    def compare_periods(self, ctx: AIContext, machine_id: Optional[str] = None,
                        machine_name: Optional[str] = None, sensor: Optional[str] = None,
                        metric: str = "thickness", period_a: Optional[str] = None,
                        date_a: Optional[str] = None, shift_a: Optional[str] = None,
                        start_a: Optional[str] = None, end_a: Optional[str] = None,
                        period_b: Optional[str] = None, date_b: Optional[str] = None,
                        shift_b: Optional[str] = None, start_b: Optional[str] = None,
                        end_b: Optional[str] = None) -> dict:
        meta = self._resolve_machine(ctx, machine_id, machine_name)
        sensor_def = self._resolve_sensor(meta, sensor)
        if metric not in METRICS:
            metric = "thickness"
        value_field, _unit = _metric_value(meta, metric, (sensor_def or {}).get("key"))
        if not (period_a or date_a or shift_a or start_a or end_a):
            raise AnalyticsError("ambiguous_date", "Which two dates or shifts should I compare?")
        if not (period_b or date_b or shift_b or start_b or end_b):
            raise AnalyticsError("ambiguous_date", "What should I compare the first period with?")
        timeframe_a = self._range(meta, {"period": period_a, "date": date_a, "shift": shift_a,
                                         "start": start_a, "end": end_a})
        timeframe_b = self._range(meta, {"period": period_b, "date": date_b, "shift": shift_b,
                                         "start": start_b, "end": end_b})
        stats_a = self._stats(ctx, meta, timeframe_a, value_field, meta["roles"].get("status"))
        stats_b = self._stats(ctx, meta, timeframe_b, value_field, meta["roles"].get("status"))

        def delta(key):
            left, right = stats_a.get(key), stats_b.get(key)
            if left is None or right is None:
                return None
            return round(right - left, 6)

        return {
            "source": "metrics.measurements",
            "machine": {"machine_id": meta["instance_id"], "name": meta["name"],
                        "asset_id": meta.get("asset_id"), "plant": meta.get("plant")},
            "metric": metric,
            "sensor": sensor_def,
            "time_range": timeframe_b.as_dict(),
            "time_ranges": [timeframe_a.as_dict(), timeframe_b.as_dict()],
            "units": {metric: meta["unit"]},
            "period_a": {"label": timeframe_a.label, "statistics": stats_a},
            "period_b": {"label": timeframe_b.label, "statistics": stats_b},
            "delta_b_minus_a": {
                "average": delta("average"), "minimum": delta("minimum"),
                "maximum": delta("maximum"), "samples": delta("samples"),
                "pass_percentage": delta("pass_percentage"),
            },
        }
