"""Analytics tool registry exposed to the AI provider.

The model can only choose from this allow-list. It cannot name a SQL statement,
a table or a column: arguments are validated against the JSON schema, and every
field used by the analytics layer comes from the authenticated company's own
template metadata.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .analytics import AnalyticsError, AnalyticsService, METRICS
from .context import AIContext

logger = logging.getLogger(__name__)

MACHINE_PROPS = {
    "machine_id": {
        "type": "string",
        "description": "Machine/application id, for example THICKNESS001. Prefer this when known.",
    },
    "machine_name": {
        "type": "string",
        "description": "Machine name or asset id as the user said it, for example 'Line 2'.",
    },
}
TIME_PROPS = {
    "period": {
        "type": "string",
        "description": "Relative plant-local period: today, yesterday, last_7_days, last_24_hours, "
                       "this_week, last_week, this_month, last_month, this_shift, previous_shift, "
                       "or last_N_days / last_N_hours.",
    },
    "date": {
        "type": "string",
        "description": "A single local date in YYYY-MM-DD form.",
    },
    "shift": {
        "type": "string",
        "description": "Shift name (usually A, B or C) when the question is about a shift.",
    },
    "start": {"type": "string", "description": "ISO start timestamp; use only when the user gave explicit times."},
    "end": {"type": "string", "description": "ISO end timestamp (exclusive)."},
}
SENSOR_METRIC_PROPS = {
    "sensor": {
        "type": "string",
        "description": "Sensor key or label, for example 'Sensor 2' or 'displacement.value_mm.2'. "
                       "Omit for the machine's aggregate value.",
    },
    "metric": {
        "type": "string",
        "enum": list(METRICS),
        "description": "Which measurement: thickness (default), displacement, deviation, or all.",
    },
}

_TOOL_SPECS = [
    {
        "name": "list_machines",
        "description": "List the machines and sensors the authenticated customer can access. "
                       "Use this to resolve machine/sensor names before asking the user.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": "list_machines",
    },
    {
        "name": "get_reading_count",
        "description": "Count readings for a machine in a plant-local date/time range.",
        "parameters": {
            "type": "object",
            "properties": {**MACHINE_PROPS, **TIME_PROPS, **SENSOR_METRIC_PROPS},
            "additionalProperties": False,
        },
        "handler": "get_reading_count",
    },
    {
        "name": "get_thickness_statistics",
        "description": "Average, minimum, maximum, standard deviation, sample and pass/fail counts "
                       "for thickness (or displacement/deviation) in a plant-local time range.",
        "parameters": {
            "type": "object",
            "properties": {**MACHINE_PROPS, **TIME_PROPS, **SENSOR_METRIC_PROPS},
            "additionalProperties": False,
        },
        "handler": "get_thickness_statistics",
    },
    {
        "name": "get_latest_measurement",
        "description": "Return the most recent non-empty sensor measurement for a machine, "
                       "optionally restricted to a plant-local time range.",
        "parameters": {
            "type": "object",
            "properties": {**MACHINE_PROPS, **TIME_PROPS, **SENSOR_METRIC_PROPS},
            "additionalProperties": False,
        },
        "handler": "get_latest_measurement",
    },
    {
        "name": "compare_filtered_unfiltered",
        "description": "Compare raw/unfiltered ingested readings with processed/filtered stored readings.",
        "parameters": {
            "type": "object",
            "properties": {**MACHINE_PROPS, **TIME_PROPS},
            "additionalProperties": False,
        },
        "handler": "compare_filtered_unfiltered",
    },
    {
        "name": "get_out_of_range_readings",
        "description": "Count readings above and below the machine's configured lower/upper limits, "
                       "with separate above/below/in-range counts and samples.",
        "parameters": {
            "type": "object",
            "properties": {**MACHINE_PROPS, **TIME_PROPS, **SENSOR_METRIC_PROPS},
            "additionalProperties": False,
        },
        "handler": "get_out_of_range_readings",
    },
    {
        "name": "get_threshold_violation_incidents",
        "description": "Return separate threshold violation incidents (continuous runs of out-of-range "
                       "or FAIL readings), not just a raw reading count.",
        "parameters": {
            "type": "object",
            "properties": {**MACHINE_PROPS, **TIME_PROPS, **SENSOR_METRIC_PROPS},
            "additionalProperties": False,
        },
        "handler": "get_threshold_violation_incidents",
    },
    {
        "name": "get_machine_connectivity",
        "description": "Check whether the machine's sensor devices are online, including last seen time.",
        "parameters": {
            "type": "object",
            "properties": {**MACHINE_PROPS},
            "additionalProperties": False,
        },
        "handler": "get_machine_connectivity",
    },
    {
        "name": "get_daily_summary",
        "description": "Daily production/readings summary for a date or range: counts, averages, "
                       "min/max, pass/fail and alerts. Optionally include per-shift detail.",
        "parameters": {
            "type": "object",
            "properties": {
                **MACHINE_PROPS, **TIME_PROPS, **SENSOR_METRIC_PROPS,
                "include_shifts": {"type": "boolean",
                                   "description": "Include the configured shift breakdown."},
            },
            "additionalProperties": False,
        },
        "handler": "get_daily_summary",
    },
    {
        "name": "compare_periods",
        "description": "Compare two dates, periods or shifts for the same machine. Supply A and B "
                       "using period/date/shift (or explicit start/end).",
        "parameters": {
            "type": "object",
            "properties": {
                **MACHINE_PROPS, **SENSOR_METRIC_PROPS,
                "period_a": {"type": "string", "description": "First period keyword."},
                "date_a": {"type": "string", "description": "First date YYYY-MM-DD."},
                "shift_a": {"type": "string", "description": "First shift name."},
                "start_a": {"type": "string", "description": "First ISO start timestamp."},
                "end_a": {"type": "string", "description": "First ISO end timestamp."},
                "period_b": {"type": "string", "description": "Second period keyword."},
                "date_b": {"type": "string", "description": "Second date YYYY-MM-DD."},
                "shift_b": {"type": "string", "description": "Second shift name."},
                "start_b": {"type": "string", "description": "Second ISO start timestamp."},
                "end_b": {"type": "string", "description": "Second ISO end timestamp."},
            },
            "additionalProperties": False,
        },
        "handler": "compare_periods",
    },
]

_TYPE_MAP = {
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "object": dict,
}


class ToolValidationError(AnalyticsError):
    pass


def _sanitize(value: Any, depth: int = 0) -> Any:
    """Keep tool output JSON-safe and bounded; it is untrusted data."""
    if depth > 6:
        return "..."
    if isinstance(value, dict):
        return {str(key)[:200]: _sanitize(item, depth + 1) for key, item in list(value.items())[:200]}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth + 1) for item in list(value)[:200]]
    if isinstance(value, str):
        cleaned = "".join(ch for ch in value if ch == "\n" or ch == "\t" or ord(ch) >= 32)
        return cleaned[:2000]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    try:
        return _sanitize(float(value), depth + 1)
    except (TypeError, ValueError):
        return str(value)[:2000]


class ToolRegistry:
    def __init__(self, analytics: Optional[AnalyticsService] = None):
        self.analytics = analytics or AnalyticsService()
        self._by_name = {spec["name"]: spec for spec in _TOOL_SPECS}

    def schemas(self) -> list[dict]:
        return [
            {"name": spec["name"], "description": spec["description"],
             "parameters": spec["parameters"]}
            for spec in _TOOL_SPECS
        ]

    def tool_names(self) -> list[str]:
        return list(self._by_name)

    def _validate(self, spec: dict, arguments: dict) -> dict:
        if not isinstance(arguments, dict):
            raise ToolValidationError("invalid_arguments", "Tool arguments must be an object.")
        parameters = spec["parameters"]
        properties = parameters.get("properties") or {}
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            raise ToolValidationError(
                "invalid_arguments",
                "Unsupported tool argument(s): " + ", ".join(unknown),
                {"allowed": sorted(properties)},
            )
        required = parameters.get("required") or []
        missing = [key for key in required if arguments.get(key) in (None, "")]
        if missing:
            raise ToolValidationError(
                "invalid_arguments",
                "Missing required argument(s): " + ", ".join(missing),
            )
        cleaned = {}
        for key, value in arguments.items():
            if value is None:
                continue
            rule = properties.get(key) or {}
            expected = _TYPE_MAP.get(rule.get("type"))
            if expected and not isinstance(value, expected):
                raise ToolValidationError("invalid_arguments", f"Argument '{key}' has the wrong type.")
            if rule.get("enum") and value not in rule["enum"]:
                raise ToolValidationError(
                    "invalid_arguments",
                    f"Argument '{key}' must be one of: " + ", ".join(map(str, rule["enum"])),
                )
            if isinstance(value, str):
                value = value.strip()[:500]
            cleaned[key] = value
        return cleaned

    def execute(self, ctx: AIContext, name: str, arguments: Any,
                default_machine_id: Optional[str] = None) -> dict:
        spec = self._by_name.get(str(name or ""))
        if not spec:
            return {
                "ok": False,
                "tool": str(name or ""),
                "error": {
                    "code": "unknown_tool",
                    "message": "That tool is not available. Only approved analytics tools can be used.",
                },
            }
        try:
            args = arguments if isinstance(arguments, dict) else {}
            args = self._validate(spec, args)
            if default_machine_id and "machine_id" in spec["parameters"]["properties"]:
                if not args.get("machine_id") and not args.get("machine_name"):
                    args["machine_id"] = default_machine_id
            handler = getattr(self.analytics, spec["handler"])
            data = handler(ctx, **args)
            return {"ok": True, "tool": spec["name"], "data": _sanitize(data)}
        except AnalyticsError as exc:
            return {"ok": False, "tool": spec["name"], "error": exc.as_dict()}
        except Exception:  # noqa: BLE001 - never expose driver/DB internals to the model
            logger.exception("AI analytics tool failed: %s", spec["name"])
            return {
                "ok": False,
                "tool": spec["name"],
                "error": {
                    "code": "analytics_unavailable",
                    "message": "I couldn't read the measurement database right now. Please try again shortly.",
                },
            }
