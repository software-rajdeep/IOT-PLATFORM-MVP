"""Device loading / serialization (BLOCK 2 + connection rows)."""
from __future__ import annotations

import json
from typing import Any, Optional

from ..db import fetch_all, fetch_one

DEVICE_SELECT = """
SELECT
    d.device_id, d.company_id, d.plant_key, d.asset_id,
    d.profile_id, d.model_key, d.gateway_id, d.device_address,
    d.serial_number, d.ingest_key, d.status, d.last_seen, d.created_at,
    p.manufacturer, p.series, p.device_type, p.connector_type, p.decoding,
    m.model_name, m.centre_distance, m.range_min, m.range_max, m.unit,
    m.resolution, m.spec,
    c.interface, c.baud_rate, c.address AS conn_address,
    c.poll_interval_ms, c.connection_parameters
FROM core.devices d
JOIN core.device_profiles p ON p.profile_id = d.profile_id
JOIN core.device_models m ON m.model_key = d.model_key
LEFT JOIN core.device_connections c ON c.device_id = d.device_id
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


def load_device(device_id: str) -> Optional[dict]:
    return fetch_one(DEVICE_SELECT + " WHERE d.device_id = %s", (device_id,))


def list_devices(company_id: str) -> list[dict]:
    return fetch_all(DEVICE_SELECT + " WHERE d.company_id = %s ORDER BY d.created_at DESC", (company_id,))


def connector_config(device: dict) -> dict:
    """Merge model + connection params into the connector's constructor input."""
    params = _json(device.get("connection_parameters"), {})
    config = {
        "device_id": device.get("device_id"),
        "port": params.get("port"),
        "simulate": params.get("simulate", True),
        "baud_rate": device.get("baud_rate"),
        "address": device.get("conn_address") or device.get("device_address"),
        "timeout": params.get("timeout", 0.5),
    }
    config.update({k: v for k, v in params.items() if k not in config})
    return config


def serialize_device(row: dict) -> dict:
    return {
        "device_id": row["device_id"],
        "status": row["status"],
        "plant": row.get("plant_key"),
        "asset_id": row.get("asset_id"),
        "gateway_id": row.get("gateway_id"),
        "device_address": row.get("device_address"),
        "serial_number": row.get("serial_number"),
        "last_seen": row["last_seen"].isoformat() if row.get("last_seen") else None,
        "profile": {
            "profile_id": row.get("profile_id"),
            "manufacturer": row.get("manufacturer"),
            "series": row.get("series"),
            "device_type": row.get("device_type"),
            "connector_type": row.get("connector_type"),
        },
        "model": {
            "model_key": row.get("model_key"),
            "model_name": row.get("model_name"),
            "centre_distance": float(row["centre_distance"]) if row.get("centre_distance") is not None else None,
            "range_min": float(row["range_min"]) if row.get("range_min") is not None else None,
            "range_max": float(row["range_max"]) if row.get("range_max") is not None else None,
            "unit": row.get("unit"),
            "resolution": float(row["resolution"]) if row.get("resolution") is not None else None,
        },
        "connection": {
            "interface": row.get("interface"),
            "baud_rate": row.get("baud_rate"),
            "address": row.get("conn_address"),
            "poll_interval_ms": row.get("poll_interval_ms"),
            "parameters": _json(row.get("connection_parameters"), {}),
        },
    }
