"""Demo customer API — simulates THE CUSTOMER's own server + database.

In production, a real customer gives us an API URL, an API key and a secret key.
They do NOT give us database credentials. This router mimics that customer server
so the "Customer database (API)" connection mode can be exercised end-to-end:

    platform (via database_api driver, customer mode)
      -> GET /demo-customer/api/latest   (X-Api-Key, X-Api-Secret headers)
      -> queries the customer's own database (ucap_customer_corp)
      -> returns JSON; the platform maps a response field to measurement_raw

The mapping is: customer DB column -> returned JSON field -> platform field.
Here the customer DB keeps raw CD22 counts and the API exposes them as
measurement_raw; the platform then normalizes to displacement.value_mm.
"""
from __future__ import annotations

import os
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Header, HTTPException, Query

from ..config import settings

router = APIRouter(prefix="/api/v1/demo-customer", tags=["demo-customer"])

# Demo credentials — in reality these are issued per customer.
DEMO_API_KEY = os.environ.get("CUSTOMER_DEMO_API_KEY", "demo-customer-key")
DEMO_API_SECRET = os.environ.get("CUSTOMER_DEMO_API_SECRET", "demo-customer-secret")


def _customer_conn():
    return psycopg2.connect(settings.customer_demo_db, cursor_factory=psycopg2.extras.RealDictCursor)


def _require_auth(api_key: Optional[str], api_secret: Optional[str]) -> None:
    if api_key != DEMO_API_KEY or api_secret != DEMO_API_SECRET:
        raise HTTPException(status_code=401, detail="Invalid API key or secret")


@router.get("/api/latest")  # noqa: A002
def latest(
    device: str = Query(default="LASER_CD22_01"),
    x_api_key: Optional[str] = Header(default=None),
    x_api_secret: Optional[str] = Header(default=None),
) -> dict:
    """Return the latest measurement row from the customer's database.

    The column set is the customer's business choice; the platform only maps
    `measurement_raw` out of it. This is the ONLY endpoint we need for V1.
    """
    _require_auth(x_api_key, x_api_secret)
    conn = _customer_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT device_code, reading_ts, raw_count, value_mm, quality_flag
                FROM shopfloor.measurement_readings
                WHERE device_code = %s
                ORDER BY reading_ts DESC
                LIMIT 1
                """,
                (device,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="No readings for device")
    return {
        "device_code": row["device_code"],
        "timestamp": row["reading_ts"].isoformat(),
        "measurement_raw": float(row["raw_count"]),
        "value_mm": float(row["value_mm"]) if row["value_mm"] is not None else None,
        "quality_flag": row["quality_flag"],
    }


@router.get("/api/devices")
def devices(
    x_api_key: Optional[str] = Header(default=None),
    x_api_secret: Optional[str] = Header(default=None),
) -> dict:
    """List the sensor devices the customer exposes through their API."""
    _require_auth(x_api_key, x_api_secret)
    conn = _customer_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT device_code FROM shopfloor.measurement_readings ORDER BY device_code"
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return {"devices": [r["device_code"] for r in rows]}