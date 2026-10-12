"""BLOCK 5 — processing pipeline + rule engine.

    raw connector output
      -> decode displacement (BLOCK 3)
      -> calibration (BLOCK 4)
      -> thickness / deviation / PASS-FAIL (rule engine)
      -> metrics.measurements (+ metrics.raw_device_messages)

Field routing is driven by core.data_mappings: one row links a device's source
field (e.g. displacement.value_mm) to a template instance's target input. This
is the explicit "device -> application" mapping (BLOCK 11 of the blueprint).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from ..db import execute, fetch_all
from ..processors import get_processor
from .devices import load_device
from .instances import instances_for_device, load_config
from .normalization import normalize


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _target_instances(device_id: str) -> list[dict]:
    """Resolve which template instances consume this device's readings.

    Uses core.data_mappings when present (source device -> instance, with an
    optional field remap). Falls back to every instance attached to the device.
    """
    mappings = fetch_all(
        """
        SELECT m.instance_id, m.source_field, m.target_field, tpl.processor_key
        FROM core.data_mappings m
        LEFT JOIN core.template_instances t ON t.instance_id = m.instance_id
        LEFT JOIN core.application_templates tpl ON tpl.template_id = t.template_id
        WHERE m.source_device_id = %s
        """,
        (device_id,),
    )
    if mappings:
        return mappings
    return [
        {
            "instance_id": i["instance_id"],
            "source_field": "displacement.value_mm",
            "target_field": "displacement.value_mm",
            "processor_key": i["processor_key"],
        }
        for i in instances_for_device(device_id)
    ]


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
    for mapping in _target_instances(device_id):
        instance_id = mapping["instance_id"]
        source_field = mapping.get("source_field") or "displacement.value_mm"
        target_field = mapping.get("target_field") or source_field
        # remap the standardized field into the instance's input field
        instance_fields = {target_field: fields.get(source_field)}
        config = load_config(instance_id)
        processor = get_processor(mapping.get("processor_key") or "rubber_thickness")
        out = processor(config, instance_fields)
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
            (company_id, instance_id, device_id, ts, displacement, displacement, actual, deviation, quality),
        )
        results.append({
            "instance_id": instance_id,
            "displacement.value_mm": displacement,
            "thickness.actual_mm": actual,
            "thickness.deviation_mm": deviation,
            "quality.status": quality,
        })

    return {"device_id": device_id, "displacement.value_mm": displacement, "results": results}
