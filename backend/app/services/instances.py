"""Template-instance loading / serialization / configuration (BLOCK 5)."""
from __future__ import annotations

import json
from typing import Any, Optional

from ..db import fetch_all, fetch_one, get_cursor

INSTANCE_SELECT = """
SELECT
    t.instance_id, t.template_id, t.company_id, t.plant_key, t.asset_id,
    t.device_id, t.name, t.status, t.created_at, t.updated_at,
    tpl.name AS template_name, tpl.version AS template_version,
    tpl.category, tpl.processor_key, tpl.inputs, tpl.dashboard,
    d.model_key, d.status AS device_status, d.last_seen
FROM core.template_instances t
JOIN core.application_templates tpl ON tpl.template_id = t.template_id
LEFT JOIN core.devices d ON d.device_id = t.device_id
"""


def _json(value: Any, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def load_config(instance_id: str) -> dict:
    rows = fetch_all(
        "SELECT config_key, value FROM core.template_config WHERE instance_id = %s",
        (instance_id,),
    )
    return {r["config_key"]: r["value"] for r in rows}


def set_config(instance_id: str, values: dict) -> None:
    with get_cursor(commit=True) as cur:
        for key, value in values.items():
            cur.execute(
                """
                INSERT INTO core.template_config (instance_id, config_key, value, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (instance_id, config_key)
                DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                """,
                (instance_id, key, None if value is None else str(value)),
            )
        cur.execute("UPDATE core.template_instances SET updated_at = now() WHERE instance_id = %s", (instance_id,))


def _steps(config: dict, status: str) -> dict:
    return {
        "connection": True,
        "calibrated": config.get("calibration.scale") not in (None, "") and config.get("calibration.offset") not in (None, ""),
        "limits": all(config.get(k) not in (None, "") for k in ("thickness.target_mm", "thickness.lower_limit_mm", "thickness.upper_limit_mm")),
        "active": status == "active",
    }


def serialize_instance(row: dict, config: Optional[dict] = None) -> dict:
    config = config if config is not None else load_config(row["instance_id"])
    return {
        "instance_id": row["instance_id"],
        "name": row["name"],
        "status": row["status"],
        "plant": row.get("plant_key"),
        "asset_id": row.get("asset_id"),
        "device_id": row.get("device_id"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "config": config,
        "steps": _steps(config, row["status"]),
        "template": {
            "template_id": row.get("template_id"),
            "name": row.get("template_name"),
            "version": row.get("template_version"),
            "category": row.get("category"),
            "processor_key": row.get("processor_key"),
            "inputs": _json(row.get("inputs"), []),
            "dashboard": _json(row.get("dashboard"), {}),
        },
    }


def get_instance(instance_id: str, company_id: Optional[str] = None) -> Optional[dict]:
    sql = INSTANCE_SELECT + " WHERE t.instance_id = %s"
    params: list[Any] = [instance_id]
    if company_id is not None:
        sql += " AND t.company_id = %s"
        params.append(company_id)
    return fetch_one(sql, params)


def list_instances(company_id: str) -> list[dict]:
    rows = fetch_all(INSTANCE_SELECT + " WHERE t.company_id = %s ORDER BY t.created_at DESC", (company_id,))
    return [serialize_instance(r) for r in rows]


def instances_for_device(device_id: str) -> list[dict]:
    return fetch_all(
        """
        SELECT t.instance_id, t.company_id, tpl.processor_key
        FROM core.template_instances t
        JOIN core.application_templates tpl ON tpl.template_id = t.template_id
        WHERE t.device_id = %s
        UNION
        SELECT t.instance_id, t.company_id, tpl.processor_key
        FROM core.data_mappings m
        JOIN core.template_instances t ON t.instance_id = m.instance_id
        JOIN core.application_templates tpl ON tpl.template_id = t.template_id
        WHERE m.source_device_id = %s
        """,
        (device_id, device_id),
    )


def instance_device_ids(instance_id: str) -> list[str]:
    """Every source device for an instance: the primary device plus mapped devices."""
    rows = fetch_all(
        """
        SELECT device_id FROM (
            SELECT t.device_id AS device_id
            FROM core.template_instances t
            WHERE t.instance_id = %s AND t.device_id IS NOT NULL
            UNION
            SELECT m.source_device_id AS device_id
            FROM core.data_mappings m
            WHERE m.instance_id = %s AND m.source_device_id IS NOT NULL
        ) s
        """,
        (instance_id, instance_id),
    )
    seen, result = set(), []
    for row in rows:
        did = row["device_id"]
        if did not in seen:
            seen.add(did)
            result.append(did)
    return result
