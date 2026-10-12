"""Application template loading (BLOCK 4 metadata)."""
from __future__ import annotations

import json
from typing import Any, Optional

from ..db import fetch_one

JSON_COLUMNS = ("inputs", "dashboard", "requires", "optional", "outputs", "field_roles", "commissioning", "alert_defaults")


def _json(value: Any, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def parse_template(row: dict) -> dict:
    out = dict(row)
    for col in JSON_COLUMNS:
        if col in out:
            out[col] = _json(out[col], {} if col in ("dashboard", "field_roles", "commissioning") else [])
    return out


def load_template(template_id: str) -> Optional[dict]:
    row = fetch_one("SELECT * FROM core.application_templates WHERE template_id = %s", (template_id,))
    return parse_template(row) if row else None


def contract(template: dict) -> list[dict]:
    """Template input contract: required inputs first, then optional ones."""
    req = [{**c, "required": c.get("required", True)} for c in template.get("requires") or []]
    opt = [{**c, "required": False} for c in template.get("optional") or []]
    return req + opt


def role_field(template: dict, role: str) -> Optional[str]:
    return (template.get("field_roles") or {}).get(role)
