"""Device routes — BLOCK 2 installed devices + connection test."""
from __future__ import annotations

import json
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import execute, fetch_one, get_cursor
from ..deps import get_current_user, require_company
from ..drivers import get_driver_class
from ..drivers.database_api import DatabaseApiConnector
from ..services.calibration import current_displacement
from ..config import settings
from ..db import fetch_all
from ..services.devices import (
    _json, connector_config, connector_driver_key, list_devices, load_device, resolve_plant, serialize_device,
    split_secrets,
)
from ..services.discovery import scope_discovery
from ..services.normalization import decode_displacement
from ..services.templates import contract, load_template

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


class DatabaseApiIn(BaseModel):
    """Connection settings for a database source.

    Two modes:
      - internal  : database we reach directly (our DB, or supplied credentials).
      - customer  : the customer's own server, reachable via an API URL + API key
                    (an additional secret key is optional).

    `mapping` maps the platform's TARGET fields onto the customer's source:
        {"measurement_raw": "<column or json.path>",
         "timestamp": "<column or json.path>",        # optional
         "device_id": "<column or json.path>"}        # optional
    """
    mode: str = "internal"  # internal | customer

    # internal mode (direct database)
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    table: Optional[str] = None
    order_by: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    sslmode: Optional[str] = None  # e.g. "require" for managed Postgres (Neon, RDS, etc.)
    column: Optional[str] = None  # legacy single-field mapping

    # customer mode (their API)
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    value_field: Optional[str] = None  # legacy single-field mapping

    # optional JWT refresh (e.g. Neon Auth): sign in with a service account
    auth_url: Optional[str] = None
    auth_email: Optional[str] = None
    auth_password: Optional[str] = None
    auth_origin: Optional[str] = None

    # target-field mapping
    mapping: Optional[dict] = None

    # replay historical rows while the sensor is offline:
    #   {"enabled": true, "from": "...", "to": "...", "step": 1,
    #    "loop": true, "timestamp": "now"}
    replay: Optional[dict] = None


class ConnectionIn(BaseModel):
    interface: str = "RS485"  # RS485 | database_api | http_push
    baud_rate: Optional[int] = None
    address: Optional[int] = None
    poll_interval_ms: Optional[int] = None
    simulate: bool = True
    port: Optional[str] = None
    database: Optional[DatabaseApiIn] = None


class CreateDeviceRequest(BaseModel):
    model_key: str
    plant_key: Optional[str] = None
    asset_id: Optional[str] = None
    gateway_id: Optional[str] = None
    serial_number: Optional[str] = None
    device_address: Optional[int] = None
    connection: ConnectionIn = ConnectionIn()


def _next_device_id() -> str:
    n = fetch_one("SELECT count(*) AS c FROM core.devices")["c"]
    for i in range(int(n) + 1, int(n) + 500):
        candidate = f"DEV{i:03d}"
        if not fetch_one("SELECT 1 FROM core.devices WHERE device_id = %s", (candidate,)):
            return candidate
    return f"DEV{secrets.token_hex(3)}"


def _as_dict(model: BaseModel) -> dict:
    dump = model.model_dump() if hasattr(model, "model_dump") else model.dict()
    return {k: v for k, v in dump.items() if v is not None}


@router.post("")
def create_device(body: CreateDeviceRequest, user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    model = fetch_one(
        "SELECT m.*, p.connector_type FROM core.device_models m "
        "JOIN core.device_profiles p ON p.profile_id = m.profile_id WHERE m.model_key = %s",
        (body.model_key,),
    )
    if not model:
        raise HTTPException(status_code=404, detail="Device model not found")

    device_id = _next_device_id()
    ingest_key = secrets.token_urlsafe(24)
    conn = body.connection
    if conn.interface not in ("RS485", "database_api", "http_push"):
        raise HTTPException(status_code=400, detail="Unknown interface")
    params = {"simulate": conn.simulate if conn.interface != "http_push" else False, "port": conn.port}
    secret_values: dict = {}
    if conn.database is not None:
        params["database"], secret_values = split_secrets(_as_dict(conn.database))
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO core.devices
                (device_id, company_id, plant_key, asset_id, profile_id, model_key,
                 gateway_id, device_address, serial_number, ingest_key, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
            """,
            (device_id, company_id, resolve_plant(company_id, body.plant_key), body.asset_id, model["profile_id"], body.model_key,
             body.gateway_id, body.device_address or conn.address, body.serial_number, ingest_key),
        )
        cur.execute(
            """
            INSERT INTO core.device_connections
                (device_id, interface, baud_rate, address, poll_interval_ms, connection_parameters, secrets)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (device_id, conn.interface, conn.baud_rate, conn.address,
             conn.poll_interval_ms if conn.poll_interval_ms is not None else (1000 if conn.interface != "RS485" else 500),
             json.dumps(params), json.dumps(secret_values)),
        )
    return serialize_device(load_device(device_id))


class DiscoverRequest(BaseModel):
    connection: ConnectionIn
    template_id: Optional[str] = None  # scope the listing to this application


@router.post("/discover-source")
def discover_source(body: DiscoverRequest, user: dict = Depends(get_current_user)) -> dict:
    """Introspect an unknown customer source so target fields can be mapped.

    Returns the tables/columns (internal mode) or the JSON keys/sample
    (customer mode) that are available to map onto the application's standard
    fields. When `template_id` is given, the listing is scoped to that
    application: only the tables/columns/fields that can feed its inputs are
    returned (see services/discovery.py).
    """
    require_company(user)
    conn = body.connection
    if conn.interface != "database_api" or conn.database is None:
        raise HTTPException(status_code=400, detail="Discovery requires a database_api connection.")
    template = load_template(body.template_id) if body.template_id else None
    field_contract = contract(template) if template else None
    config = {"simulate": False, "database": _as_dict(conn.database)}
    try:
        driver = DatabaseApiConnector(config, {})
        result = driver.discover()
        if result.get("mode") == "internal" and driver.table:
            result["sample"] = driver.read_payload()
        result = scope_discovery(result, field_contract)
        if template:
            result["application"] = {"template_id": template["template_id"], "name": template["name"]}
            result["contract"] = field_contract
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Discovery failed: {exc}")


@router.get("/plants")
def plants(user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    return {"plants": fetch_all("SELECT plant_key, plant_name, location FROM core.plants WHERE company_id = %s "
                                "ORDER BY plant_name", (company_id,))}


@router.get("")
def get_devices(user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    return {"devices": [serialize_device(d) for d in list_devices(company_id)]}


@router.get("/{device_id}")
def get_device(device_id: str, user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    device = load_device(device_id)
    if not device or str(device["company_id"]) != company_id:
        raise HTTPException(status_code=404, detail="Device not found")
    return serialize_device(device)


class ConnectionPatch(BaseModel):
    interface: Optional[str] = None
    baud_rate: Optional[int] = None
    address: Optional[int] = None
    poll_interval_ms: Optional[int] = None
    simulate: Optional[bool] = None
    port: Optional[str] = None
    database: Optional[DatabaseApiIn] = None


class DevicePatch(BaseModel):
    name: Optional[str] = None  # display name -> asset_id
    ip: Optional[str] = None    # LAN IP / host:port -> connection_parameters.port


@router.patch("/{device_id}")
def update_device(device_id: str, body: DevicePatch, user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    device = load_device(device_id)
    if not device or str(device["company_id"]) != company_id:
        raise HTTPException(status_code=404, detail="Device not found")
    if body.name is not None and body.name.strip():
        execute("UPDATE core.devices SET asset_id = %s, updated_at = now() WHERE device_id = %s",
                (body.name.strip(), device_id))
    if body.ip is not None:
        params = dict(device.get("connection_parameters") or {})
        if isinstance(params, str):
            params = json.loads(params)
        params["port"] = body.ip.strip() or None
        execute(
            "UPDATE core.device_connections SET connection_parameters = %s, updated_at = now() WHERE device_id = %s",
            (json.dumps(params), device_id),
        )
    return serialize_device(load_device(device_id))


@router.patch("/{device_id}/connection")
def update_connection(device_id: str, body: ConnectionPatch, user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    device = load_device(device_id)
    if not device or str(device["company_id"]) != company_id:
        raise HTTPException(status_code=404, detail="Device not found")
    params = dict(device.get("connection_parameters") or {})
    if isinstance(params, str):
        params = json.loads(params)
    if body.simulate is not None:
        params["simulate"] = body.simulate
    if body.port is not None:
        params["port"] = body.port
    stored_secrets = _json(device.get("secrets"), {})
    if body.database is not None:
        params["database"], new_secrets = split_secrets(_as_dict(body.database))
        stored_secrets.update(new_secrets)  # blank secret fields keep the stored value
    execute(
        """
        UPDATE core.device_connections SET
            interface = COALESCE(%s, interface),
            baud_rate = COALESCE(%s, baud_rate),
            address = COALESCE(%s, address),
            poll_interval_ms = COALESCE(%s, poll_interval_ms),
            connection_parameters = %s,
            secrets = %s,
            updated_at = now()
        WHERE device_id = %s
        """,
        (body.interface, body.baud_rate, body.address, body.poll_interval_ms, json.dumps(params),
         json.dumps(stored_secrets), device_id),
    )
    return serialize_device(load_device(device_id))


@router.post("/{device_id}/test")
def test_device(device_id: str, user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    device = load_device(device_id)
    if not device or str(device["company_id"]) != company_id:
        raise HTTPException(status_code=404, detail="Device not found")
    driver_key = connector_driver_key(device)
    if driver_key == "http_push":
        last = fetch_one(
            "SELECT ts, raw_payload FROM metrics.raw_device_messages WHERE device_id = %s ORDER BY ts DESC LIMIT 1",
            (device_id,))
        return {"connected": bool(last), "interface": "http_push",
                "communication_status": "online" if last else "waiting",
                "payload": _json(last["raw_payload"], {}) if last else None,
                "last_message_at": last["ts"].isoformat() if last else None}
    connector_cls = get_driver_class(driver_key)
    if not connector_cls:
        raise HTTPException(status_code=400, detail=f"No connector for {driver_key}")
    payload = None
    replay = False
    try:
        connector = connector_cls(connector_config(device), {"centre_distance": device.get("centre_distance")})
        connected = connector.checkConnection()
        reading = None
        replay = bool(getattr(connector, "replay", False))
        if connected:
            if replay and hasattr(connector, "read_replay_payload"):
                result = connector.read_replay_payload(None)
                payload = result[0] if result else None
            elif not connector.simulate and hasattr(connector, "read_payload"):
                payload = connector.read_payload()
            else:
                reading = connector.readMeasurement()
        connector.disconnect()
    except Exception as exc:
        return {"connected": False, "error": str(exc)}
    if payload is not None:
        return {"connected": True, "interface": driver_key, "communication_status": "online",
                "replay": replay, "payload": payload}
    resolution = float(device["resolution"]) if device.get("resolution") is not None else 0.01
    raw = reading.get("measurement_raw") if reading else None
    return {
        "connected": bool(connected),
        "communication_status": reading.get("communication_status") if reading else "offline",
        "measurement_raw": raw,
        "displacement.value_mm": decode_displacement(raw, resolution),
    }


@router.get("/{device_id}/displacement")
def device_displacement(device_id: str, user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    device = load_device(device_id)
    if not device or str(device["company_id"]) != company_id:
        raise HTTPException(status_code=404, detail="Device not found")
    return current_displacement(device_id) or {"displacement.value_mm": None, "ts": None}


@router.get("/{device_id}/ingest-info")
def ingest_info(device_id: str, user: dict = Depends(get_current_user)) -> dict:
    """HTTP push details for the customer's IT team (owner only)."""
    company_id = require_company(user)
    device = load_device(device_id)
    if not device or str(device["company_id"]) != company_id:
        raise HTTPException(status_code=404, detail="Device not found")
    url = f"{settings.public_base_url.rstrip('/')}/api/v1/ingest/{device_id}"
    return {"url": url, "ingest_key": device["ingest_key"], "header": "X-Ingest-Key",
            "example": f"curl -X POST {url} -H 'X-Ingest-Key: {device['ingest_key']}' "
                       "-H 'Content-Type: application/json' -d '{\"value\": 1.502}'"}


@router.get("/{device_id}/samples")
def device_samples(device_id: str, limit: int = 20, user: dict = Depends(get_current_user)) -> dict:
    """Recent raw payloads from a source (sample capture for mapping)."""
    company_id = require_company(user)
    device = load_device(device_id)
    if not device or str(device["company_id"]) != company_id:
        raise HTTPException(status_code=404, detail="Device not found")
    rows = fetch_all(
        "SELECT message_id, ts, raw_payload FROM metrics.raw_device_messages WHERE device_id = %s "
        "ORDER BY ts DESC LIMIT %s", (device_id, min(max(limit, 1), 100)))
    return {"samples": [{"message_id": r["message_id"], "ts": r["ts"].isoformat(),
                         "payload": _json(r["raw_payload"], {})} for r in rows]}
