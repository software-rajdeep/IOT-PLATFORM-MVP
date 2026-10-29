"""BLOCK 5 — processing pipeline + rule engine.

    raw payload (any shape: CD22 connector, customer API/DB row, HTTP push)
      -> metrics.raw_device_messages                      (kept from day 1)
      -> per bound instance (core.data_mappings):
           mapping engine  (active core.mapping_versions)  -> canonical template inputs
           filter          (filter.window / filter.trim_pct)
           processor       (template processor_key)        -> outputs (thickness, PASS/FAIL ...)
           metrics.measurements (fields JSONB + legacy columns)
           alert rules
      -> mapping errors go to metrics.mapping_failures, never silently dropped
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from ..db import execute, execute_returning, fetch_all
from ..processors import get_processor
from . import alerts, filters, incidents
from .devices import load_device
from .instances import instances_for_device, load_config
from .mapping import map_payload
from .mappings import active_mapping, device_preset
from .templates import contract, load_template
from .units import units
from . import live_updates


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _target_instances(device_id: str) -> list[dict]:
    """Resolve which template instances consume this device's readings.

    Uses core.data_mappings (source device -> instance). Falls back to every
    instance attached to the device.
    """
    mappings = fetch_all(
        """
        SELECT m.instance_id, t.template_id, t.status, tpl.processor_key
        FROM core.data_mappings m
        JOIN core.template_instances t ON t.instance_id = m.instance_id
        JOIN core.application_templates tpl ON tpl.template_id = t.template_id
        WHERE m.source_device_id = %s
        """,
        (device_id,),
    )
    if mappings:
        return mappings
    return [{"instance_id": i["instance_id"], "processor_key": i["processor_key"]} for i in instances_for_device(device_id)]


def _ts(value) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def ingest_raw(device_id: str, payload: dict, source_ts=None) -> dict:
    device = load_device(device_id)
    if not device:
        raise ValueError(f"Unknown device {device_id}")
    company_id = device["company_id"]
    received = _now()
    raw = execute_returning(
        "INSERT INTO metrics.raw_device_messages (company_id, device_id, ts, source_ts, raw_payload) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING message_id",
        (company_id, device_id, received, _ts(source_ts), json.dumps(payload, default=str)),
    )
    raw_id = raw["message_id"] if raw else None
    execute("UPDATE core.devices SET last_seen = now(), status = 'active' WHERE device_id = %s", (device_id,))

    results = []
    for binding in _target_instances(device_id):
        instance_id = binding["instance_id"]
        template = load_template(binding.get("template_id")) if binding.get("template_id") else None
        if not template:
            continue
        rule_set = alerts.rules(instance_id)
        mapping = active_mapping(instance_id, device_id)
        definition = mapping["definition"] if mapping else device_preset(device, template)
        mapping_id = mapping["mapping_id"] if mapping else None
        if not definition:
            errors = [{"code": "NO_MAPPING", "field": "", "message": "No mapping saved for this source yet"}]
            values, context = {}, {}
        else:
            values, context, errors = map_payload(payload, definition, contract(template), units())
        if errors:
            execute(
                "INSERT INTO metrics.mapping_failures (company_id, instance_id, device_id, mapping_id, raw_message_id, errors) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (company_id, instance_id, device_id, mapping_id, raw_id, json.dumps(errors)),
            )
            if binding.get("status") == "active":
                alerts.on_mapping_failure(company_id, instance_id, device_id, errors, rule_set)
            results.append({"instance_id": instance_id, "errors": errors})
            continue

        config = load_config(instance_id)
        filter_field = (template.get("commissioning") or {}).get("filter_field")
        if filter_field and values.get(filter_field) is not None:
            values[filter_field] = filters.apply((instance_id, device_id), float(values[filter_field]), config)

        processor = get_processor(template.get("processor_key") or binding.get("processor_key"))
        outputs = processor(config, values)
        fields = {**values, **outputs}
        ts = _ts(context.get("timestamp")) or _ts(source_ts) or received
        measurement = execute_returning(
            """
            INSERT INTO metrics.measurements
                (company_id, instance_id, device_id, ts, raw_displacement_mm, displacement_value_mm,
                 thickness_mm, deviation_mm, quality_status, fields, mapping_id, raw_message_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING measurement_id
            """,
            (company_id, instance_id, device_id, ts, values.get("displacement.value_mm"),
             values.get("displacement.value_mm"), outputs.get("thickness.actual_mm"),
             outputs.get("thickness.deviation_mm"), outputs.get("quality.status"),
             json.dumps(fields, default=str), mapping_id, raw_id),
        )
        measurement_id = measurement["measurement_id"] if measurement else None
        if binding.get("status") == "active":
            alerts.on_measurement(company_id, instance_id, device_id, outputs.get("quality.status"), rule_set)
        live_updates.publish(instance_id, device_id, ts.isoformat())
        if binding.get("status") == "active":
            try:
                incidents.observe(
                    company_id=company_id,
                    instance_id=instance_id,
                    device_id=device_id,
                    machine_id=device.get("asset_id") or instance_id,
                    ts=ts,
                    fields=fields,
                    config=config,
                    template=template,
                    measurement_id=measurement_id,
                )
            except Exception as exc:
                # Event detection must never interrupt acquisition/processing.
                print(f"[events] incident processing error for {instance_id}/{device_id}: {exc}")
        results.append({"instance_id": instance_id, **fields})

    return {"device_id": device_id, "raw_message_id": raw_id, "results": results}


def process_measurement(device_id: str, measurement_raw: Optional[float], ts: Optional[datetime] = None) -> dict:
    """Original CD22 entry point (gateway + simulator): a raw count payload."""
    out = ingest_raw(device_id, {"measurement_raw": measurement_raw}, ts)
    first = next((r for r in out["results"] if "errors" not in r), {})
    return {"device_id": device_id, "displacement.value_mm": first.get("displacement.value_mm"), "results": out["results"]}
