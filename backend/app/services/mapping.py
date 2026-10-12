"""BLOCK 3 — mapping engine (replaces the hard-coded normalization).

Turns ANY raw payload (CD22 connector output, a customer's API JSON, a DB row,
an HTTP push) into the canonical inputs a template declares:

    raw payload --(definition: path + transforms per field)--> {canonical_key: value}

Pure functions only — no database access — so every rule is unit-testable.

Definition format (stored per instance+device in core.mapping_versions):

    {
      "fields": [
        {"target": "displacement.value_mm", "path": "laser.value_um",
         "transforms": [{"type": "cast", "to": "decimal"},
                        {"type": "unit", "from": "um", "to": "mm"}]}
      ],
      "timestamp": {"path": "ts", "format": "iso", "tz": "Asia/Kolkata"}
    }

Contract format (template requires + optional):
    [{"key": "displacement.value_mm", "kind": "displacement", "unit": "mm",
      "required": true, "valid_min": -60, "valid_max": 60}]
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Optional
from zoneinfo import ZoneInfo

_MISSING = object()


class MappingError(Exception):
    def __init__(self, code: str, field: str, message: str):
        super().__init__(message)
        self.code, self.field, self.message = code, field, message

    def as_dict(self) -> dict:
        return {"code": self.code, "field": self.field, "message": self.message}


# --------------------------------------------------------------------- paths
def _tokens(path: str) -> list[str]:
    path = str(path or "").strip()
    if path.startswith("$"):
        path = path[1:]
    path = path.lstrip(".")
    # a[0].b -> a, 0, b
    return [t for t in re.split(r"\.|\[|\]", path) if t != ""]


def extract(payload: Any, path: str) -> Any:
    """Walk dicts/lists with dot paths ("laser.value_um", "items[0].v", "$.a.b"). Missing -> _MISSING."""
    current = payload
    for token in _tokens(path):
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError):
                return _MISSING
        elif isinstance(current, dict):
            if token not in current:
                return _MISSING
            current = current[token]
        else:
            return _MISSING
    return current


def _type_of(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        try:
            float(value)
            return "numeric_text"
        except ValueError:
            pass
        if _looks_like_datetime(value):
            return "datetime_text"
        return "text"
    if value is None:
        return "null"
    return type(value).__name__


def _looks_like_datetime(value: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value))


def flatten(payload: Any, prefix: str = "") -> list[dict]:
    """Every leaf path in a payload with its type and an example value."""
    out: list[dict] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            out.extend(flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(payload, list):
        if payload:
            out.extend(flatten(payload[0], f"{prefix}[0]"))
    else:
        out.append({"path": prefix, "type": _type_of(payload), "example": payload})
    return out


# --------------------------------------------------------------------- units
def convert_unit(value: float, from_unit: str, to_unit: str, units: dict) -> float:
    """units = {unit_key: {"dimension", "to_base_factor", "to_base_offset"}}."""
    if from_unit == to_unit:
        return value
    src, dst = units.get(from_unit), units.get(to_unit)
    if not src or not dst:
        raise MappingError("UNKNOWN_UNIT", "", f"Unknown unit {from_unit if not src else to_unit}")
    if src["dimension"] != dst["dimension"]:
        raise MappingError("INCOMPATIBLE_UNIT", "",
                           f"Cannot convert {from_unit} ({src['dimension']}) to {to_unit} ({dst['dimension']})")
    base = value * float(src["to_base_factor"]) + float(src["to_base_offset"])
    return (base - float(dst["to_base_offset"])) / float(dst["to_base_factor"])


# --------------------------------------------------------------------- transforms
def _resolve_param(value: Any, params: dict) -> Any:
    """"@model.resolution" style references (used by device presets)."""
    if isinstance(value, str) and value.startswith("@"):
        return params.get(value[1:])
    return value


def apply_transforms(value: Any, transforms: list[dict], field: str, units: dict, params: Optional[dict] = None) -> Any:
    params = params or {}
    for t in transforms or []:
        kind = t.get("type")
        if kind == "default":
            if value is None or value is _MISSING or value == "":
                value = t.get("value")
            continue
        if value is None or value is _MISSING:
            continue
        if kind == "cast":
            to = t.get("to", "decimal")
            try:
                if to == "decimal":
                    value = float(value)
                elif to == "integer":
                    value = int(float(value))
                elif to == "text":
                    value = str(value)
                elif to == "boolean":
                    value = value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes", "on", "ok"}
            except (TypeError, ValueError):
                raise MappingError("CAST_FAILED", field, f"Cannot convert {value!r} to {to}")
        elif kind == "scale":
            factor = _resolve_param(t.get("factor", 1), params)
            value = _num(value, field) * float(factor if factor is not None else 1)
        elif kind == "offset":
            value = _num(value, field) + float(_resolve_param(t.get("value", 0), params) or 0)
        elif kind == "unit":
            value = convert_unit(_num(value, field), t.get("from"), t.get("to"), units)
        elif kind == "enum_map":
            mapping = t.get("map") or {}
            value = mapping.get(str(value), t.get("else", value))
        elif kind == "valid_range":
            v = _num(value, field)
            lo, hi = t.get("min"), t.get("max")
            if (lo is not None and v < float(lo)) or (hi is not None and v > float(hi)):
                raise MappingError("OUT_OF_RANGE", field, f"{v} outside valid range [{lo}, {hi}]")
        else:
            raise MappingError("UNKNOWN_TRANSFORM", field, f"Unknown transform {kind!r}")
    return value


def _num(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise MappingError("NOT_A_NUMBER", field, f"{value!r} is not a number")


# --------------------------------------------------------------------- timestamps
def parse_timestamp(value: Any, spec: dict) -> datetime:
    fmt = (spec or {}).get("format") or "auto"
    tz = ZoneInfo((spec or {}).get("tz") or "UTC")
    if isinstance(value, (int, float)) or (fmt in ("epoch_s", "epoch_ms")):
        number = float(value)
        if fmt == "epoch_ms" or (fmt == "auto" and number > 1e11):
            number /= 1000.0
        return datetime.fromtimestamp(number, tz=timezone.utc)
    text = str(value).strip()
    if fmt in ("auto", "iso"):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise MappingError("BAD_TIMESTAMP", "timestamp", f"Cannot parse timestamp {text!r}")
    else:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            raise MappingError("BAD_TIMESTAMP", "timestamp", f"{text!r} does not match {fmt}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


# --------------------------------------------------------------------- the engine
def map_payload(payload: Any, definition: dict, contract: list[dict], units: dict,
                params: Optional[dict] = None) -> tuple[dict, dict, list[dict]]:
    """Apply a mapping definition to one raw payload.

    Returns (values, context, errors). values holds canonical keys; context holds
    the source timestamp. Required contract fields that end up missing, and any
    transform failure, are reported as errors (the caller must not process).
    """
    values: dict = {}
    errors: list[dict] = []
    context: dict = {}
    spec_by_key = {c["key"]: c for c in contract}

    for rule in (definition or {}).get("fields", []):
        target = rule.get("target")
        if not target:
            continue
        raw = extract(payload, rule.get("path", ""))
        try:
            if raw is _MISSING and not any(t.get("type") == "default" for t in rule.get("transforms") or []):
                if spec_by_key.get(target, {}).get("required"):
                    raise MappingError("MISSING_FIELD", target, f"Path {rule.get('path')!r} not found in payload")
                continue
            value = apply_transforms(raw, rule.get("transforms") or [], target, units, params)
            spec = spec_by_key.get(target) or {}
            if value is not None and value is not _MISSING and spec.get("kind") not in (None, "text"):
                v = _num(value, target)
                lo, hi = spec.get("valid_min"), spec.get("valid_max")
                if (lo is not None and v < float(lo)) or (hi is not None and v > float(hi)):
                    raise MappingError("OUT_OF_RANGE", target, f"{v} outside the template's valid range [{lo}, {hi}]")
                value = v
            if value is not _MISSING:
                values[target] = value
        except MappingError as exc:
            exc.field = exc.field or target
            errors.append(exc.as_dict())

    ts_spec = (definition or {}).get("timestamp")
    if ts_spec and ts_spec.get("path"):
        raw_ts = extract(payload, ts_spec["path"])
        if raw_ts is not _MISSING and raw_ts not in (None, ""):
            try:
                context["timestamp"] = parse_timestamp(raw_ts, ts_spec).isoformat()
            except MappingError as exc:
                errors.append(exc.as_dict())

    for spec in contract:
        if spec.get("required") and values.get(spec["key"]) is None and not any(e["field"] == spec["key"] for e in errors):
            errors.append({"code": "MISSING_FIELD", "field": spec["key"],
                           "message": f"Required field {spec.get('label') or spec['key']} is not mapped"})
    return values, context, errors


def unmapped_required(definition: dict, contract: list[dict]) -> list[str]:
    targets = {f.get("target") for f in (definition or {}).get("fields", []) if f.get("path") or
               any(t.get("type") == "default" for t in f.get("transforms") or [])}
    return [c["key"] for c in contract if c.get("required") and c["key"] not in targets]


# --------------------------------------------------------------------- presets + suggestions
def preset_definition(profile: dict, model: dict, template_contract: list[dict]) -> Optional[dict]:
    """Build the mapping a known device gives a template, by matching signal kinds.

    The device profile's preset says which raw path carries which signal; the
    template input with the same kind (or kind "number", which accepts any numeric
    signal) becomes the target. Model values (e.g. resolution) are inlined.
    """
    preset = profile.get("preset_mapping") or {}
    fields = []
    used: set = set()
    for rule in preset.get("fields", []):
        signal = rule.get("signal")
        target = next((c for c in template_contract if c.get("kind") == signal and c["key"] not in used), None) \
            or next((c for c in template_contract if c.get("kind") == "number" and c["key"] not in used), None)
        if not target:
            continue
        used.add(target["key"])
        transforms = []
        for t in rule.get("transforms") or []:
            t = dict(t)
            for k, v in list(t.items()):
                if isinstance(v, str) and v.startswith("@model."):
                    t[k] = float(model.get(v[len("@model."):]) or 0) or 1
            transforms.append(t)
        fields.append({"target": target["key"], "path": rule["path"], "transforms": transforms})
    if not fields:
        return None
    return {"fields": fields, "timestamp": preset.get("timestamp")}


_SYNONYMS = {
    "displacement": ["displacement", "distance", "disp", "laser", "value", "reading", "measurement", "raw", "val"],
    "thickness": ["thickness", "thk", "gauge", "value"],
    "speed": ["speed", "line_speed", "mpm", "velocity", "rate"],
    "text": ["product", "prod", "recipe", "sku", "item", "code", "batch", "order"],
    "number": ["value", "reading", "val", "measurement", "raw"],
}


def suggest(paths: list[dict], contract: list[dict]) -> dict:
    """Rule-based suggestion: name similarity + type compatibility. An engineer confirms."""
    result: dict = {}
    taken: set = set()
    for spec in contract:
        words = _SYNONYMS.get(spec.get("kind") or "number", []) + [spec["key"].split(".")[-1], spec["key"].split(".")[0]]
        accepted = ("text", "numeric_text") if spec.get("kind") == "text" else ("number", "numeric_text")
        best, best_score = None, 0.0
        for p in paths:
            if p["path"] in taken or p["type"] not in accepted:
                continue
            leaf = re.split(r"[.\[\]]", p["path"].lower())
            name = "_".join(x for x in leaf if x)
            score = max(SequenceMatcher(None, w, part).ratio() for w in words for part in name.split("_") + [name])
            if score > best_score:
                best, best_score = p, score
        if best and best_score >= 0.6:
            taken.add(best["path"])
            result[spec["key"]] = {"path": best["path"], "score": round(best_score, 2)}
    ts = next((p for p in paths if p["type"] == "datetime_text" or
               re.search(r"(^|[._])(ts|time|timestamp|date|created_at|reading_ts)$", p["path"].lower())), None)
    if ts:
        result["timestamp"] = {"path": ts["path"], "score": 1.0}
    return result


def unit_hint(path: str) -> Optional[str]:
    """Guess a unit from a field name suffix (laser_um -> um)."""
    leaf = re.split(r"[.\[\]]", path.lower())[-1] if path else ""
    for suffix, unit in (("_um", "um"), ("_mm", "mm"), ("_cm", "cm"), ("_in", "in"), ("_mpm", "m/min"), ("_m_min", "m/min")):
        if leaf.endswith(suffix):
            return unit
    return None
