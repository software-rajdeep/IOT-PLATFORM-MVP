"""Template-instance routes — BLOCK 5 onboarding (create, calibrate, limits, activate)."""
from __future__ import annotations

import json
from queue import Empty
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..db import execute, fetch_all, fetch_one, get_cursor
from ..deps import get_current_user, require_company
from ..services.calibration import compute_linear, current_displacement
from ..services.dashboard import build_dashboard
from ..services.devices import DEVICE_SELECT, load_device, resolve_plant, serialize_device
from ..services.instances import get_instance, instance_device_ids, list_instances, load_config, serialize_instance, set_config
from ..services import alerts, audit
from ..services.mappings import ensure_initial_mapping
from ..services.templates import load_template
from ..services.live_updates import subscribe

router = APIRouter(prefix="/api/v1/instances", tags=["instances"])


def _json(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class CreateInstanceRequest(BaseModel):
    name: str
    template_id: str
    device_id: str
    plant_key: Optional[str] = None
    asset_id: Optional[str] = None


class UpdateInstanceRequest(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    config: Optional[dict] = None


class CalibrateRequest(BaseModel):
    reference_1_thickness: float
    reference_1_displacement: Optional[float] = None
    reference_2_thickness: float
    reference_2_displacement: Optional[float] = None


def _next_instance_id(template_id: str) -> str:
    prefix = "".join(ch for ch in template_id.split("_")[0] if ch.isalnum()).upper() or "INST"
    n = fetch_one("SELECT count(*) AS c FROM core.template_instances WHERE template_id = %s", (template_id,))["c"]
    for i in range(int(n) + 1, int(n) + 500):
        candidate = f"{prefix}{i:03d}"
        if not fetch_one("SELECT 1 FROM core.template_instances WHERE instance_id = %s", (candidate,)):
            return candidate
    return f"{prefix}XXX"


def _owned(instance_id: str, user: dict) -> dict:
    company_id = require_company(user)
    row = get_instance(instance_id, company_id)
    if not row:
        raise HTTPException(status_code=404, detail="Instance not found")
    return row


class AddInstanceDeviceRequest(BaseModel):
    device_id: str


def _seconds_since(last_seen) -> Optional[float]:
    if not last_seen:
        return None
    aware = last_seen if last_seen.tzinfo else last_seen.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - aware).total_seconds(), 2)


def _device_summary(device: dict) -> dict:
    summary = serialize_device(device)
    age = _seconds_since(device.get("last_seen"))
    summary["online"] = age is not None and age <= 5
    return summary


@router.get("/{instance_id}/devices")
def list_instance_devices(instance_id: str, user: dict = Depends(get_current_user)) -> dict:
    instance = _owned(instance_id, user)
    company_id = require_company(user)
    mapped_ids = instance_device_ids(instance_id)
    mapped = []
    for did in mapped_ids:
        dev = load_device(did)
        if dev:
            mapped.append(_device_summary(dev))

    available = []
    model_key = instance.get("model_key")
    if model_key:
        rows = fetch_all(
            DEVICE_SELECT + " WHERE d.company_id = %s AND d.status = 'active' AND d.model_key = %s "
            "ORDER BY d.created_at",
            (company_id, model_key),
        )
        for r in rows:
            if r["device_id"] in mapped_ids:
                continue
            available.append(_device_summary(r))
    return {"instance_id": instance_id, "mapped": mapped, "available": available}


@router.post("/{instance_id}/devices")
def add_instance_device(instance_id: str, body: AddInstanceDeviceRequest,
                        user: dict = Depends(get_current_user)) -> dict:
    instance = _owned(instance_id, user)
    company_id = require_company(user)
    dev = load_device(body.device_id)
    if not dev or str(dev["company_id"]) != company_id:
        raise HTTPException(status_code=404, detail="Device not found")
    model_key = instance.get("model_key")
    if model_key and dev.get("model_key") != model_key:
        raise HTTPException(status_code=400, detail="Device model must match this application's device model")
    if body.device_id in instance_device_ids(instance_id):
        return list_instance_devices(instance_id, user)
    execute(
        "INSERT INTO core.data_mappings (instance_id, source_device_id, source_field, target_field) "
        "VALUES (%s, %s, 'displacement.value_mm', 'displacement.value_mm')",
        (instance_id, body.device_id),
    )
    return list_instance_devices(instance_id, user)


@router.post("")
def create_instance(body: CreateInstanceRequest, user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    template = fetch_one(
        "SELECT * FROM core.application_templates WHERE template_id = %s AND status = 'active'",
        (body.template_id,),
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    device = load_device(body.device_id)
    if not device or str(device["company_id"]) != company_id:
        raise HTTPException(status_code=404, detail="Device not found")

    instance_id = _next_instance_id(body.template_id)
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO core.template_instances
                (instance_id, template_id, company_id, plant_key, asset_id, device_id, name, status, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'draft', %s)
            """,
            (instance_id, body.template_id, company_id, resolve_plant(company_id, body.plant_key) or device.get("plant_key"),
             body.asset_id or device.get("asset_id"), body.device_id, body.name, user["id"]),
        )
        cur.execute(
            "INSERT INTO core.data_mappings (instance_id, source_device_id, source_field, target_field) "
            "VALUES (%s, %s, 'displacement.value_mm', 'displacement.value_mm')",
            (instance_id, body.device_id),
        )

    # seed configuration defaults from the template's input definitions
    defaults: dict[str, Any] = {}
    for field in _json(template.get("inputs"), []):
        if field.get("default") is not None:
            defaults[field["key"]] = field["default"]
    if defaults:
        set_config(instance_id, defaults)

    tpl = load_template(body.template_id)
    ensure_initial_mapping(instance_id, body.device_id, tpl, user["id"])
    alerts.ensure_rules(instance_id, tpl)
    audit.log(user, "instance.create", "instance", instance_id, None, {"template": body.template_id, "device": body.device_id})
    return serialize_instance(get_instance(instance_id, company_id))


@router.get("")
def get_instances(user: dict = Depends(get_current_user)) -> dict:
    return {"instances": list_instances(require_company(user))}


@router.get("/{instance_id}")
def get_one(instance_id: str, user: dict = Depends(get_current_user)) -> dict:
    return serialize_instance(_owned(instance_id, user))


@router.patch("/{instance_id}")
def update_instance(instance_id: str, body: UpdateInstanceRequest, user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    _owned(instance_id, user)
    if body.name is not None or body.status is not None:
        execute(
            "UPDATE core.template_instances SET name = COALESCE(%s, name), status = COALESCE(%s, status), "
            "updated_at = now() WHERE instance_id = %s AND company_id = %s",
            (body.name, body.status, instance_id, company_id),
        )
    if body.config:
        before = load_config(instance_id)
        set_config(instance_id, body.config)
        changed = {k: v for k, v in body.config.items() if not k.startswith("ui.") and str(before.get(k)) != str(v)}
        if changed:
            audit.log(user, "config.update", "instance", instance_id, {k: before.get(k) for k in changed}, changed)
    return serialize_instance(get_instance(instance_id, company_id))


@router.post("/{instance_id}/calibrate")
def calibrate(instance_id: str, body: CalibrateRequest, user: dict = Depends(get_current_user)) -> dict:
    row = _owned(instance_id, user)
    device_id = row.get("device_id")

    d1 = body.reference_1_displacement
    d2 = body.reference_2_displacement
    if d1 is None:
        current = current_displacement(device_id) if device_id else None
        d1 = current and current.get("displacement.value_mm")
    if d2 is None:
        current = current_displacement(device_id) if device_id else None
        d2 = current and current.get("displacement.value_mm")
    if d1 is None or d2 is None:
        raise HTTPException(status_code=400, detail="Reference displacements are required (or the device must have live data).")

    try:
        scale, offset = compute_linear(
            body.reference_1_thickness, d1, body.reference_2_thickness, d2,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    before = load_config(instance_id)
    set_config(instance_id, {
        "calibration.scale": scale,
        "calibration.offset": offset,
        "calibration.reference_1_thickness": body.reference_1_thickness,
        "calibration.reference_1_displacement": d1,
        "calibration.reference_2_thickness": body.reference_2_thickness,
        "calibration.reference_2_displacement": d2,
        "calibration.calibrated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "calibration.calibrated_by": user["username"],
    })
    audit.log(user, "calibration.save", "instance", instance_id,
              {"scale": before.get("calibration.scale"), "offset": before.get("calibration.offset"),
               "calibrated_at": before.get("calibration.calibrated_at")},
              {"scale": scale, "offset": offset, "references": [[body.reference_1_thickness, d1], [body.reference_2_thickness, d2]]})
    return {"calibration.scale": scale, "calibration.offset": offset,
            "reference_1_displacement": d1, "reference_2_displacement": d2}


class VerifyCalibrationRequest(BaseModel):
    known_thickness: float
    displacement: Optional[float] = None


@router.post("/{instance_id}/calibration/verify")
def verify_calibration(instance_id: str, body: VerifyCalibrationRequest, user: dict = Depends(get_current_user)) -> dict:
    row = _owned(instance_id, user)
    config = load_config(instance_id)
    try:
        scale = float(config.get("calibration.scale"))
        offset = float(config.get("calibration.offset"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Instance is not calibrated yet.")
    displacement = body.displacement
    if displacement is None:
        current = current_displacement(row.get("device_id"))
        displacement = current and current.get("displacement.value_mm")
    if displacement is None:
        raise HTTPException(status_code=400, detail="No live displacement available to verify.")
    calculated = scale * displacement + offset
    return {
        "known_thickness": body.known_thickness,
        "displacement": displacement,
        "calculated_thickness": round(calculated, 6),
        "error_mm": round(calculated - body.known_thickness, 6),
    }


@router.post("/{instance_id}/activate")
def activate(instance_id: str, user: dict = Depends(get_current_user)) -> dict:
    from .instance_setup import checklist
    company_id = require_company(user)
    row = _owned(instance_id, user)
    items = checklist(instance_id, row, load_template(row["template_id"]))
    blocking = [i["label"] for i in items if i["required"] and not i["ok"]]
    if blocking:
        raise HTTPException(status_code=400, detail="Not ready to activate: " + "; ".join(blocking))
    audit.log(user, "instance.activate", "instance", instance_id)
    execute("UPDATE core.template_instances SET status = 'active', updated_at = now() "
            "WHERE instance_id = %s AND company_id = %s", (instance_id, company_id))
    return serialize_instance(get_instance(instance_id, company_id))


@router.get("/{instance_id}/dashboard")
def dashboard(instance_id: str, device_id: Optional[str] = Query(default=None),
              user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    data = build_dashboard(instance_id, company_id, device_id=device_id)
    if not data:
        raise HTTPException(status_code=404, detail="Instance not found")
    return data


@router.get("/{instance_id}/live")
def live_updates(instance_id: str, user: dict = Depends(get_current_user)) -> StreamingResponse:
    _owned(instance_id, user)

    def stream():
        with subscribe(instance_id) as events:
            yield ": connected\n\n"
            while True:
                try:
                    event = events.get(timeout=15)
                    yield f"event: measurement\ndata: {event}\n\n"
                except Empty:
                    yield ": keepalive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{instance_id}/measurements")
def measurements(
    instance_id: str,
    limit: int = Query(default=100, ge=1, le=2000),
    user: dict = Depends(get_current_user),
) -> dict:
    _owned(instance_id, user)
    rows = fetch_all(
        "SELECT ts, displacement_value_mm, thickness_mm, deviation_mm, quality_status "
        "FROM metrics.measurements WHERE instance_id = %s ORDER BY ts DESC LIMIT %s",
        (instance_id, limit),
    )
    rows.reverse()
    for r in rows:
        if r.get("ts"):
            r["ts"] = r["ts"].isoformat()
        for k in ("displacement_value_mm", "thickness_mm", "deviation_mm"):
            if r.get(k) is not None:
                r[k] = float(r[k])
    return {"measurements": rows}


# ------------------------------------------------------------------ alerts
@router.get("/{instance_id}/alerts")
def list_alerts(instance_id: str, user: dict = Depends(get_current_user)) -> dict:
    _owned(instance_id, user)
    return {"alerts": alerts.list_events(instance_id, 100), "rules": alerts.rules(instance_id)}


@router.post("/{instance_id}/alerts/{event_id}/ack")
def ack_alert(instance_id: str, event_id: int, user: dict = Depends(get_current_user)) -> dict:
    _owned(instance_id, user)
    alerts.acknowledge(instance_id, event_id, user["username"])
    return {"ok": True}


class AlertRuleRequest(BaseModel):
    enabled: Optional[bool] = None
    params: Optional[dict] = None


@router.put("/{instance_id}/alert-rules/{rule_type}")
def update_alert_rule(instance_id: str, rule_type: str, body: AlertRuleRequest,
                      user: dict = Depends(get_current_user)) -> dict:
    _owned(instance_id, user)
    if rule_type not in ("fail_streak", "offline", "mapping_failure"):
        raise HTTPException(status_code=400, detail="Unknown alert rule")
    execute(
        "INSERT INTO core.alert_rules (instance_id, rule_type, params, enabled) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (instance_id, rule_type) DO UPDATE SET "
        "params = COALESCE(%s, core.alert_rules.params), enabled = COALESCE(%s, core.alert_rules.enabled)",
        (instance_id, rule_type, json.dumps(body.params or {}), body.enabled if body.enabled is not None else True,
         json.dumps(body.params) if body.params is not None else None, body.enabled),
    )
    audit.log(user, "alert_rule.update", "instance", instance_id, None, {rule_type: body.model_dump()})
    return {"rules": alerts.rules(instance_id)}


# ------------------------------------------------------------------ export + reports
@router.get("/{instance_id}/export.csv")
def export_csv(instance_id: str, start: Optional[datetime] = None, end: Optional[datetime] = None,
               device_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    from fastapi.responses import StreamingResponse
    from ..services.reports import export_csv as rows
    row = _owned(instance_id, user)
    template = load_template(row["template_id"])
    name = f"{instance_id}_{(start or datetime.now()).date()}.csv"
    return StreamingResponse(rows(instance_id, template, start, end, device_id), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/{instance_id}/report")
def shift_report(instance_id: str, day: Optional[str] = Query(default=None, alias="date"),
                 user: dict = Depends(get_current_user)) -> dict:
    from datetime import date as date_cls
    from ..services.reports import shift_report as build
    row = _owned(instance_id, user)
    try:
        when = date_cls.fromisoformat(day) if day else datetime.now().date()
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    report = build(instance_id, load_template(row["template_id"]), when)
    report["instance"] = {"instance_id": instance_id, "name": row["name"], "plant": row.get("plant_key"),
                          "asset_id": row.get("asset_id"), "template": row.get("template_name")}
    return report


@router.get("/{instance_id}/history")
def change_history(instance_id: str, user: dict = Depends(get_current_user)) -> dict:
    """Audit trail: calibration, limits, mapping, activation (who / when / before / after)."""
    _owned(instance_id, user)
    rows = fetch_all(
        "SELECT a.action, a.old_value, a.new_value, a.created_at, u.username FROM core.audit_logs a "
        "LEFT JOIN core.users u ON u.id = a.user_id WHERE a.entity_type = 'instance' AND a.entity_id = %s "
        "ORDER BY a.created_at DESC LIMIT 100", (instance_id,))
    return {"history": [{**r, "created_at": r["created_at"].isoformat(),
                         "old_value": _json(r["old_value"], None), "new_value": _json(r["new_value"], None)} for r in rows]}


# ------------------------------------------------------------------ clone a line
class CloneRequest(BaseModel):
    name: str
    asset_id: Optional[str] = None
    port: Optional[str] = None


@router.post("/{instance_id}/clone")
def clone_instance(instance_id: str, body: CloneRequest, user: dict = Depends(get_current_user)) -> dict:
    """Copy a working line to another: new source (same model + connection type), same mapping,
    rules, limits and layout. Calibration is NOT copied — every sensor is recalibrated."""
    import secrets as _secrets
    from .devices import _next_device_id
    from ..services.mappings import active_mapping, save_mapping
    company_id = require_company(user)
    row = _owned(instance_id, user)
    src = load_device(row["device_id"])
    if not src:
        raise HTTPException(status_code=400, detail="Source instance has no device")
    new_device = _next_device_id()
    params = _json(src.get("connection_parameters"), {})
    if body.port is not None:
        params["port"] = body.port or None
    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO core.devices (device_id, company_id, plant_key, asset_id, profile_id, model_key, gateway_id, "
            "device_address, ingest_key, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')",
            (new_device, company_id, src.get("plant_key"), body.asset_id, src["profile_id"], src["model_key"],
             src.get("gateway_id"), src.get("device_address"), _secrets.token_urlsafe(24)))
        cur.execute(
            "INSERT INTO core.device_connections (device_id, interface, baud_rate, address, poll_interval_ms, "
            "connection_parameters, secrets) SELECT %s, interface, baud_rate, address, poll_interval_ms, %s, secrets "
            "FROM core.device_connections WHERE device_id = %s",
            (new_device, json.dumps(params), src["device_id"]))
    new_id = _next_instance_id(row["template_id"])
    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO core.template_instances (instance_id, template_id, company_id, plant_key, asset_id, device_id, "
            "name, status, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, 'draft', %s)",
            (new_id, row["template_id"], company_id, row.get("plant_key"), body.asset_id, new_device, body.name, user["id"]))
        cur.execute("INSERT INTO core.data_mappings (instance_id, source_device_id, source_field, target_field) "
                    "VALUES (%s, %s, 'displacement.value_mm', 'displacement.value_mm')", (new_id, new_device))
        cur.execute(
            "INSERT INTO core.template_config (instance_id, config_key, value) SELECT %s, config_key, value "
            "FROM core.template_config WHERE instance_id = %s AND config_key NOT LIKE 'calibration.%%'",
            (new_id, instance_id))
        cur.execute("INSERT INTO core.alert_rules (instance_id, rule_type, params, enabled) "
                    "SELECT %s, rule_type, params, enabled FROM core.alert_rules WHERE instance_id = %s", (new_id, instance_id))
        cur.execute("INSERT INTO core.instance_dashboards (instance_id, layout, updated_by) "
                    "SELECT %s, layout, %s FROM core.instance_dashboards WHERE instance_id = %s", (new_id, user["id"], instance_id))
    template = load_template(row["template_id"])
    defaults = {f["key"]: f["default"] for f in template.get("inputs") or []
                if f["key"].startswith("calibration.") and f.get("default") is not None}
    if defaults:
        set_config(new_id, defaults)
    mapping = active_mapping(instance_id, row["device_id"])
    if mapping:
        save_mapping(new_id, new_device, mapping["definition"], "clone", user["id"])
    audit.log(user, "instance.clone", "instance", new_id, None, {"from": instance_id, "device": new_device})
    return serialize_instance(get_instance(new_id, company_id))
