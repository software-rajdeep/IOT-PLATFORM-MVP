"""Gateway ingest — the machine front door (BLOCK 1 output -> BLOCK 5 pipeline).

A gateway/edge device posts raw connector measurements with the device's ingest
key. It never sends customer rules; the platform applies them.
"""
from __future__ import annotations

import hmac
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, Header, HTTPException
from pydantic import BaseModel

from ..services.devices import load_device
from ..services.processing import ingest_raw, process_measurement

router = APIRouter(prefix="/api/v1/gateway", tags=["gateway"])
push_router = APIRouter(prefix="/api/v1/ingest", tags=["gateway"])


def _authorize(device_id: str, key: Optional[str]) -> dict:
    device = load_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Unknown device")
    if not key or not hmac.compare_digest(key, device["ingest_key"]):
        raise HTTPException(status_code=401, detail="Invalid ingest key")
    return device


class MeasurementIn(BaseModel):
    device_id: str
    measurement_raw: Optional[float] = None
    timestamp: Optional[datetime] = None


@router.post("/measurements")
def ingest_measurement(body: MeasurementIn, x_ingest_key: Optional[str] = Header(default=None)) -> dict:
    _authorize(body.device_id, x_ingest_key)
    try:
        result = process_measurement(body.device_id, body.measurement_raw, body.timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **result}


@push_router.post("/{device_id}")
def push(device_id: str, payload: Any = Body(...), x_ingest_key: Optional[str] = Header(default=None)) -> dict:
    """HTTP push: the customer's system posts ANY JSON (one object, or a list of objects).

    Nothing about the shape is assumed here; the instance's mapping turns it into
    template inputs. Messages that fail mapping are logged in metrics.mapping_failures.
    """
    _authorize(device_id, x_ingest_key)
    messages = payload if isinstance(payload, list) else [payload]
    if not all(isinstance(m, dict) for m in messages) or len(messages) > 500:
        raise HTTPException(status_code=400, detail="Send a JSON object or a list of up to 500 objects")
    results = [ingest_raw(device_id, m) for m in messages]
    mapped = sum(1 for r in results for i in r["results"] if "errors" not in i)
    failed = [i for r in results for i in r["results"] if "errors" in i]
    return {"ok": True, "received": len(messages), "mapped": mapped,
            "errors": failed[0]["errors"] if failed else []}
