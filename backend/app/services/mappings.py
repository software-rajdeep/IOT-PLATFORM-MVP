"""Mapping versions (core.mapping_versions): which definition maps a device's raw payload
into an instance's template inputs. Every save is a new version; measurements are
stamped with the mapping_id that produced them."""
from __future__ import annotations

import json
from typing import Optional

from ..db import execute, fetch_one, get_cursor
from .devices import _json, load_device
from .mapping import preset_definition
from .templates import contract


def active_mapping(instance_id: str, device_id: str) -> Optional[dict]:
    row = fetch_one(
        "SELECT mapping_id, version, origin, definition, created_at FROM core.mapping_versions "
        "WHERE instance_id = %s AND device_id = %s AND active ORDER BY version DESC LIMIT 1",
        (instance_id, device_id),
    )
    if row:
        row["definition"] = _json(row["definition"], {})
        row["created_at"] = row["created_at"].isoformat() if row.get("created_at") else None
    return row


def save_mapping(instance_id: str, device_id: str, definition: dict, origin: str = "manual",
                 user_id: Optional[str] = None) -> dict:
    with get_cursor(commit=True) as cur:
        cur.execute(
            "SELECT coalesce(max(version), 0) AS v FROM core.mapping_versions WHERE instance_id = %s AND device_id = %s",
            (instance_id, device_id),
        )
        version = int(cur.fetchone()["v"]) + 1
        cur.execute("UPDATE core.mapping_versions SET active = false WHERE instance_id = %s AND device_id = %s",
                    (instance_id, device_id))
        cur.execute(
            "INSERT INTO core.mapping_versions (instance_id, device_id, version, origin, definition, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING mapping_id",
            (instance_id, device_id, version, origin, json.dumps(definition), user_id),
        )
        mapping_id = cur.fetchone()["mapping_id"]
    return {"mapping_id": mapping_id, "version": version, "origin": origin, "definition": definition}


def device_preset(device: dict, template: dict) -> Optional[dict]:
    profile = {"preset_mapping": _json(device.get("preset_mapping"), {})}
    model = {"resolution": float(device["resolution"]) if device.get("resolution") is not None else None}
    return preset_definition(profile, model, contract(template))


def initial_definition(device: dict, template: dict) -> Optional[dict]:
    """The mapping a new instance starts with.

    - Our own connector (RS-485 CD22, or any simulated source) emits the profile's raw
      fields, so the device preset applies directly.
    - A database/API source set up with the older "measurement_raw <- column" form gets
      the preset re-pointed at that column (raw counts), plus its timestamp column.
    - Anything else (HTTP push, unmapped customer data) starts empty: step 4 maps it.
    """
    params = _json(device.get("connection_parameters"), {})
    interface = str(device.get("interface") or "RS485").lower()
    preset = device_preset(device, template)
    if params.get("simulate", True) and interface != "http_push":
        return preset
    if interface == "rs485":
        return preset
    legacy = ((params.get("database") or {}).get("mapping") or {})
    if interface == "database_api" and preset and legacy.get("measurement_raw"):
        definition = json.loads(json.dumps(preset))
        definition["fields"][0]["path"] = legacy["measurement_raw"]
        if legacy.get("timestamp"):
            definition["timestamp"] = {"path": legacy["timestamp"], "format": "auto", "tz": "UTC"}
        return definition
    return None


def ensure_initial_mapping(instance_id: str, device_id: str, template: dict, user_id: Optional[str] = None) -> Optional[dict]:
    if active_mapping(instance_id, device_id):
        return None
    device = load_device(device_id)
    definition = initial_definition(device, template) if device else None
    if not definition:
        return None
    return save_mapping(instance_id, device_id, definition, origin="preset", user_id=user_id)


def mapped_targets(instance_id: str, device_id: Optional[str]) -> set:
    m = active_mapping(instance_id, device_id) if device_id else None
    if not m:
        return set()
    return {f["target"] for f in m["definition"].get("fields", []) if f.get("target")}


def delete_mappings_for(instance_id: str) -> None:
    execute("DELETE FROM core.mapping_versions WHERE instance_id = %s", (instance_id,))
