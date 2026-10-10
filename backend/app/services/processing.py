"""BLOCK 5 — processing pipeline + rule engine.

    raw connector output
      -> decode displacement (BLOCK 3)
      -> calibration (BLOCK 4)
      -> thickness / deviation / PASS-FAIL (rule engine)
      -> metrics.measurements (+ metrics.raw_device_messages)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from ..db import execute
from ..processors import get_processor
from .devices import load_device
from .instances import instances_for_device, load_config
from .normalization import normalize


def _now() -> datetime:
    return datetime.now(timezone.utc)


def process_measurement(device_id: str, measurement_raw: Optional[float], ts: Optional[datetime] = None) -> dict:
    ts = ts or _now()
    device = load_device(device_id)
    if not device:
        raise ValueError(f"Unknown device {device_id}")

    resolution = float(device["resolution"]) if device.get("resolution") is not None else 0.01
    fields = normalize(measurement_raw, resolution)
    displacement = fields.get("displacement.value_mm")
    company_id = device["company_id"]

    # raw message kept from day 1
    execute(
        "INSERT INTO metrics.raw_device_messages (company_id, device_id, ts, raw_payload) VALUES (%s, %s, %s, %s)",
        (company_id, device_id, ts, json.dumps({"measurement_raw": measurement_raw})),
    )
    execute("UPDATE core.devices SET last_seen = now(), status = 'active' WHERE device_id = %s", (device_id,))

    results = []
    for inst in instances_for_device(device_id):
        config = load_config(inst["instance_id"])
        processor = get_processor(inst["processor_key"])
        out = processor(config, fields)
        actual = out.get("thickness.actual_mm")
        deviation = out.get("thickness.deviation_mm")
        quality = out.get("quality.status")
        execute(
            """
            INSERT INTO metrics.measurements
                (company_id, instance_id, device_id, ts, raw_displacement_mm,
                 displacement_value_mm, thickness_mm, deviation_mm, quality_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (company_id, inst["instance_id"], device_id, ts, displacement, displacement, actual, deviation, quality),
        )
        results.append({
            "instance_id": inst["instance_id"],
            "displacement.value_mm": displacement,
            "thickness.actual_mm": actual,
            "thickness.deviation_mm": deviation,
            "quality.status": quality,
        })

    return {"device_id": device_id, "displacement.value_mm": displacement, "results": results}
