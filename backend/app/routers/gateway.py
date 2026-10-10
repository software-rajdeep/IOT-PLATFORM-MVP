"""Gateway ingest — the machine front door (BLOCK 1 output -> BLOCK 5 pipeline).

A gateway/edge device posts raw connector measurements with the device's ingest
key. It never sends customer rules; the platform applies them.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ..services.devices import load_device
from ..services.processing import process_measurement

router = APIRouter(prefix="/api/v1/gateway", tags=["gateway"])


class MeasurementIn(BaseModel):
    device_id: str
    measurement_raw: Optional[float] = None
    timestamp: Optional[datetime] = None


@router.post("/measurements")
def ingest_measurement(body: MeasurementIn, x_ingest_key: Optional[str] = Header(default=None)) -> dict:
    device = load_device(body.device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Unknown device")
    if not x_ingest_key or x_ingest_key != device["ingest_key"]:
        raise HTTPException(status_code=401, detail="Invalid ingest key")
    try:
        result = process_measurement(body.device_id, body.measurement_raw, body.timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **result}
