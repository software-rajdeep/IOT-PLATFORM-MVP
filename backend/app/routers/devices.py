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
from ..services.calibration import current_displacement
from ..services.devices import connector_config, list_devices, load_device, serialize_device
from ..services.normalization import decode_displacement

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


class ConnectionIn(BaseModel):
    interface: str = "RS485"
    baud_rate: int = 38400
    address: int = 1
    poll_interval_ms: int = 100
    simulate: bool = True
    port: Optional[str] = None


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
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO core.devices
                (device_id, company_id, plant_key, asset_id, profile_id, model_key,
                 gateway_id, device_address, serial_number, ingest_key, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
            """,
            (device_id, company_id, body.plant_key, body.asset_id, model["profile_id"], body.model_key,
             body.gateway_id, body.device_address or conn.address, body.serial_number, ingest_key),
        )
        cur.execute(
            """
            INSERT INTO core.device_connections
                (device_id, interface, baud_rate, address, poll_interval_ms, connection_parameters)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (device_id, conn.interface, conn.baud_rate, conn.address, conn.poll_interval_ms,
             json.dumps({"simulate": conn.simulate, "port": conn.port})),
        )
    return serialize_device(load_device(device_id))


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
    execute(
        """
        UPDATE core.device_connections SET
            interface = COALESCE(%s, interface),
            baud_rate = COALESCE(%s, baud_rate),
            address = COALESCE(%s, address),
            poll_interval_ms = COALESCE(%s, poll_interval_ms),
            connection_parameters = %s,
            updated_at = now()
        WHERE device_id = %s
        """,
        (body.interface, body.baud_rate, body.address, body.poll_interval_ms, json.dumps(params), device_id),
    )
    return serialize_device(load_device(device_id))


@router.post("/{device_id}/test")
def test_device(device_id: str, user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    device = load_device(device_id)
    if not device or str(device["company_id"]) != company_id:
        raise HTTPException(status_code=404, detail="Device not found")
    connector_cls = get_driver_class(device["connector_type"])
    if not connector_cls:
        raise HTTPException(status_code=400, detail=f"No connector for {device['connector_type']}")
    try:
        connector = connector_cls(connector_config(device), {"centre_distance": device.get("centre_distance")})
        connected = connector.checkConnection()
        reading = connector.readMeasurement() if connected else None
        connector.disconnect()
    except Exception as exc:
        return {"connected": False, "error": str(exc)}
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
