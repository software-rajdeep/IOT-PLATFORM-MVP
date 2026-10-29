"""Onboarding steps for an instance: data mapping (step 4), graph options + layout (step 7),
activation checklist (step 8)."""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import execute, fetch_all, fetch_one
from ..deps import get_current_user, require_company
from ..drivers import get_driver_class
from ..processors import get_processor
from ..services import audit
from ..services.compat import resolve_widgets, unlocks_by_input
from ..services.dashboard import get_layout
from ..services.devices import _json, connector_config, connector_driver_key, load_device
from ..services.instances import get_instance, load_config
from ..services.mapping import flatten, map_payload, suggest, unit_hint, unmapped_required
from ..services.mappings import active_mapping, device_preset, mapped_targets, save_mapping
from ..services.templates import contract, load_template
from ..services.units import units

router = APIRouter(prefix="/api/v1/instances", tags=["instance setup"])


def _owned(instance_id: str, user: dict) -> tuple[dict, dict]:
    company_id = require_company(user)
    row = get_instance(instance_id, company_id)
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")
    return row, load_template(row["template_id"])


def _widget_types() -> list[dict]:
    rows = fetch_all("SELECT * FROM core.widget_types ORDER BY sort_order")
    for r in rows:
        r["requires"] = _json(r.get("requires"), [])
    return rows


# ------------------------------------------------------------------ step 4: mapping
@router.get("/{instance_id}/mapping")
def get_mapping(instance_id: str, user: dict = Depends(get_current_user)) -> dict:
    row, template = _owned(instance_id, user)
    device = load_device(row["device_id"]) if row.get("device_id") else None
    current = active_mapping(instance_id, row["device_id"]) if device else None
    preset = device_preset(device, template) if device else None
    versions = fetch_all(
        "SELECT mapping_id, version, origin, active, created_at FROM core.mapping_versions "
        "WHERE instance_id = %s ORDER BY version DESC LIMIT 20", (instance_id,))
    for v in versions:
        v["created_at"] = v["created_at"].isoformat()
    failures = fetch_one(
        "SELECT count(*) AS n, max(created_at) AS last FROM metrics.mapping_failures "
        "WHERE instance_id = %s AND created_at > now() - interval '1 hour'", (instance_id,))
    return {
        "instance_id": instance_id,
        "device_id": row.get("device_id"),
        "interface": (device or {}).get("interface"),
        "simulate": _json((device or {}).get("connection_parameters"), {}).get("simulate", False),
        "contract": contract(template),
        "current": current,
        "preset": preset,
        "versions": versions,
        "unlocks": unlocks_by_input(_widget_types(), template),
        "units": list(units().values()),
        "failures_last_hour": int(failures["n"]) if failures else 0,
    }


@router.post("/{instance_id}/mapping/samples")
def capture_samples(instance_id: str, user: dict = Depends(get_current_user)) -> dict:
    """Live sample from the source (pull sources) plus the latest stored raw payloads."""
    row, template = _owned(instance_id, user)
    device = load_device(row["device_id"])
    samples: list[dict] = []
    live_error = None
    key = connector_driver_key(device)
    cls = get_driver_class(key) if key != "http_push" else None
    if cls is not None:
        try:
            connector = cls(connector_config(device), {"centre_distance": device.get("centre_distance")})
            if connector.simulate:
                payload = {"measurement_raw": connector.readMeasurement().get("measurement_raw")}
            elif getattr(connector, "replay", False) and hasattr(connector, "read_replay_payload"):
                result = connector.read_replay_payload(None)
                payload = result[0] if result else None
            elif hasattr(connector, "read_payload"):
                payload = connector.read_payload()
            else:
                payload = {"measurement_raw": connector.readMeasurement().get("measurement_raw")}
            connector.disconnect()
            if payload:
                samples.append({"source": "live", "payload": payload})
        except Exception as exc:
            live_error = str(exc)
    stored = fetch_all(
        "SELECT ts, raw_payload FROM metrics.raw_device_messages WHERE device_id = %s ORDER BY ts DESC LIMIT 20",
        (row["device_id"],))
    samples.extend({"source": "stored", "ts": r["ts"].isoformat(), "payload": _json(r["raw_payload"], {})} for r in stored)
    paths = flatten(samples[0]["payload"]) if samples else []
    for p in paths:
        p["unit_hint"] = unit_hint(p["path"])
    return {
        "samples": samples[:20],
        "paths": paths,
        "suggestions": suggest(paths, contract(template)) if paths else {},
        "live_error": live_error,
    }


class PreviewRequest(BaseModel):
    definition: dict
    payload: Optional[Any] = None


@router.post("/{instance_id}/mapping/preview")
def preview_mapping(instance_id: str, body: PreviewRequest, user: dict = Depends(get_current_user)) -> dict:
    """Run one sample through mapping + processor without storing anything."""
    row, template = _owned(instance_id, user)
    payload = body.payload
    if payload is None:
        last = fetch_one("SELECT raw_payload FROM metrics.raw_device_messages WHERE device_id = %s "
                         "ORDER BY ts DESC LIMIT 1", (row["device_id"],))
        payload = _json(last["raw_payload"], {}) if last else {}
    values, context, errors = map_payload(payload, body.definition, contract(template), units())
    outputs = {}
    if not errors:
        outputs = get_processor(template["processor_key"])(load_config(instance_id), values)
    return {"payload": payload, "values": values, "context": context, "errors": errors, "outputs": outputs,
            "unmapped_required": unmapped_required(body.definition, contract(template))}


class SaveMappingRequest(BaseModel):
    definition: dict


@router.post("/{instance_id}/mapping")
def save(instance_id: str, body: SaveMappingRequest, user: dict = Depends(get_current_user)) -> dict:
    row, template = _owned(instance_id, user)
    missing = unmapped_required(body.definition, contract(template))
    if missing:
        raise HTTPException(status_code=400, detail=f"Map the required fields first: {', '.join(missing)}")
    known = {c["key"] for c in contract(template)}
    # Extra sensors are stored as "<field_key>.<n>" (e.g. displacement.value_mm.2), so
    # several sensor columns can feed the same application input.
    unknown = [f.get("target") for f in body.definition.get("fields", [])
               if f.get("target") and f.get("target") not in known
               and not any(str(f.get("target")).startswith(k + ".") for k in known)]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown template fields: {', '.join(map(str, unknown))}")
    old = active_mapping(instance_id, row["device_id"])
    saved = save_mapping(instance_id, row["device_id"], body.definition, "manual", user["id"])
    audit.log(user, "mapping.save", "instance", instance_id, old and old["definition"], body.definition)
    return saved


@router.get("/{instance_id}/mapping/failures")
def failures(instance_id: str, limit: int = 50, user: dict = Depends(get_current_user)) -> dict:
    _owned(instance_id, user)
    rows = fetch_all(
        "SELECT f.failure_id, f.device_id, f.mapping_id, f.errors, f.created_at, r.raw_payload "
        "FROM metrics.mapping_failures f LEFT JOIN metrics.raw_device_messages r ON r.message_id = f.raw_message_id "
        "WHERE f.instance_id = %s ORDER BY f.created_at DESC LIMIT %s", (instance_id, min(limit, 200)))
    return {"failures": [{**r, "errors": _json(r["errors"], []), "raw_payload": _json(r["raw_payload"], None),
                          "created_at": r["created_at"].isoformat()} for r in rows]}


# ------------------------------------------------------------------ step 7: graph options
@router.get("/{instance_id}/widgets")
def widget_options(instance_id: str, user: dict = Depends(get_current_user)) -> dict:
    row, template = _owned(instance_id, user)
    targets = mapped_targets(instance_id, row.get("device_id"))
    return {"widgets": resolve_widgets(_widget_types(), template, targets),
            "layout": get_layout(instance_id, template, load_config(instance_id))}


class LayoutRequest(BaseModel):
    widgets: list[dict]


@router.put("/{instance_id}/layout")
def save_layout(instance_id: str, body: LayoutRequest, user: dict = Depends(get_current_user)) -> dict:
    row, template = _owned(instance_id, user)
    targets = mapped_targets(instance_id, row.get("device_id"))
    allowed = {w["widget_type"] for w in resolve_widgets(_widget_types(), template, targets) if w["status"] == "available"}
    clean = []
    for i, w in enumerate(body.widgets):
        if w.get("type") not in allowed:
            raise HTTPException(status_code=400, detail=f"Widget {w.get('type')!r} is not available for this application")
        clean.append({"id": str(w.get("id") or f"w{i + 1}"), "type": w["type"],
                      **{k: int(w.get(k) or 0) for k in ("x", "y", "w", "h")},
                      **({"title": str(w["title"])[:80]} if w.get("title") else {})})
    layout = {"widgets": clean}
    execute(
        "INSERT INTO core.instance_dashboards (instance_id, layout, updated_by, updated_at) VALUES (%s, %s, %s, now()) "
        "ON CONFLICT (instance_id) DO UPDATE SET layout = EXCLUDED.layout, updated_by = EXCLUDED.updated_by, updated_at = now()",
        (instance_id, json.dumps(layout), user["id"]))
    return layout


@router.delete("/{instance_id}/layout")
def reset_layout(instance_id: str, user: dict = Depends(get_current_user)) -> dict:
    _, template = _owned(instance_id, user)
    execute("DELETE FROM core.instance_dashboards WHERE instance_id = %s", (instance_id,))
    return get_layout(instance_id, template, load_config(instance_id))


# ------------------------------------------------------------------ step 8: activation checklist
def checklist(instance_id: str, row: dict, template: dict) -> list[dict]:
    config = load_config(instance_id)
    roles = template.get("field_roles") or {}
    mapping = active_mapping(instance_id, row["device_id"]) if row.get("device_id") else None
    definition = mapping["definition"] if mapping else None
    missing = unmapped_required(definition or {}, contract(template))
    recent = fetch_one(
        "SELECT count(*) AS n FROM metrics.measurements WHERE instance_id = %s AND ts > now() - interval '10 minutes'",
        (instance_id,))
    items = [
        {"key": "mapping", "label": "Required data fields mapped", "ok": bool(definition) and not missing, "required": True,
         "detail": "" if definition and not missing else f"Unmapped: {', '.join(missing) or 'no mapping saved'}"},
        {"key": "data", "label": "Data flowing (readings in the last 10 min)", "ok": bool(recent and recent["n"]),
         "required": False, "detail": f"{recent['n'] if recent else 0} readings"},
    ]
    if (template.get("commissioning") or {}).get("calibration") in ("two_point", "baseline", "gap", "auto_gap"):
        items.append({"key": "calibration", "label": "Calibrated with reference pieces", "required": True,
                      "ok": config.get("calibration.calibrated_at") not in (None, ""),
                      "detail": f"by {config.get('calibration.calibrated_by')} at {config.get('calibration.calibrated_at')}"
                      if config.get("calibration.calibrated_at") else "Not calibrated yet"})
    limit_keys = [roles[k] for k in ("lower", "upper") if roles.get(k)]
    if limit_keys:
        items.append({"key": "limits", "label": "Limits set", "required": True,
                      "ok": all(config.get(k) not in (None, "") for k in limit_keys),
                      "detail": " / ".join(f"{k.split('.')[-1]}={config.get(k)}" for k in limit_keys)})
    return items


@router.get("/{instance_id}/checklist")
def get_checklist(instance_id: str, user: dict = Depends(get_current_user)) -> dict:
    row, template = _owned(instance_id, user)
    items = checklist(instance_id, row, template)
    return {"items": items, "ready": all(i["ok"] for i in items if i["required"]), "status": row["status"]}
