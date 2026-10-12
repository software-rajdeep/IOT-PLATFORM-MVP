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

import math
import os
import random
import threading
import time
from typing import Optional
from zoneinfo import ZoneInfo

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


# ---------------------------------------------------------------- live feeder
# The customer's own line system writes a reading every second into THEIR database.
# Displacement wanders around 1.5 mm (≈ 8.5 mm thickness at the default calibration)
# with occasional excursions, a product change every few minutes and a varying line speed.
_feeder: Optional[threading.Thread] = None
_feeder_stop = threading.Event()
PRODUCTS = ("RUB-EXTR-1", "RUB-EXTR-2", "RUB-EXTR-3")


def _feed_loop() -> None:
    t0 = time.time()
    conn = None
    while not _feeder_stop.is_set():
        try:
            if conn is None or conn.closed:
                conn = psycopg2.connect(settings.customer_demo_db)
                conn.autocommit = True
            t = time.time() - t0
            product = PRODUCTS[int(t // 180) % len(PRODUCTS)]
            bias = {"RUB-EXTR-1": 0.0, "RUB-EXTR-2": 0.05, "RUB-EXTR-3": -0.04}[product]
            excursion = 0.25 if random.random() < 0.03 else 0.0
            disp = 1.5 + bias + 0.1 * math.sin(t / 9.0) + random.uniform(-0.03, 0.03) + excursion
            speed = 12 + 3 * math.sin(t / 40.0) + random.uniform(-0.3, 0.3)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO shopfloor.measurement_readings "
                    "(device_code, order_no, reading_ts, raw_count, value_mm, quality_flag, product_code, line_speed_mpm) "
                    "VALUES ('LASER_CD22_01', 'PO-LIVE', now(), %s, %s, %s, %s, %s)",
                    (int(round(disp / 0.01)), round(disp, 6), "OK" if excursion == 0 else "WARN", product, round(speed, 2)),
                )
        except Exception as exc:  # customer DB not initialised yet, etc.
            print(f"[demo-customer] feeder: {exc}")
            conn = None
            _feeder_stop.wait(5)
        _feeder_stop.wait(1.0)
    if conn is not None:
        conn.close()


def start_feeder() -> None:
    global _feeder
    if _feeder and _feeder.is_alive():
        return
    _feeder_stop.clear()
    _feeder = threading.Thread(target=_feed_loop, name="demo-customer-feeder", daemon=True)
    _feeder.start()


def stop_feeder() -> None:
    _feeder_stop.set()


@router.get("/api/v2/latest")
def latest_v2(
    line: str = Query(default="EXT-07"),
    x_api_key: Optional[str] = Header(default=None),
    x_api_secret: Optional[str] = Header(default=None),
) -> dict:
    """The customer's newer line API: their own field names, micrometres, product and speed.

    The platform does NOT know this shape in advance; it is mapped in onboarding step 4.
    """
    _require_auth(x_api_key, x_api_secret)
    conn = _customer_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reading_id, reading_ts, value_mm, product_code, line_speed_mpm, quality_flag "
                "FROM shopfloor.measurement_readings WHERE device_code = 'LASER_CD22_01' "
                "ORDER BY reading_ts DESC LIMIT 1"
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="No readings")
    return {
        "machine": line,
        "seq": row["reading_id"],
        # plant-local time without an offset, as many shop-floor systems send it
        "ts": row["reading_ts"].astimezone(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S.%f"),
        "laser": {"value_um": round(float(row["value_mm"]) * 1000, 1), "status": row["quality_flag"]},
        "prod": row["product_code"],
        "speed_mpm": float(row["line_speed_mpm"]) if row["line_speed_mpm"] is not None else None,
    }


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