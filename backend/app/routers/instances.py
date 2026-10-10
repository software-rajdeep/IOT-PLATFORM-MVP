"""Template-instance routes — BLOCK 5 onboarding (create, calibrate, limits, activate)."""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..db import execute, fetch_all, fetch_one, get_cursor
from ..deps import get_current_user, require_company
from ..services.calibration import compute_linear, current_displacement
from ..services.dashboard import build_dashboard
from ..services.instances import get_instance, list_instances, load_config, serialize_instance, set_config
from ..services.devices import load_device

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
            (instance_id, body.template_id, company_id, body.plant_key or device.get("plant_key"),
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
        set_config(instance_id, body.config)
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

    scale, offset = compute_linear(
        body.reference_1_thickness, d1, body.reference_2_thickness, d2,
    )
    set_config(instance_id, {
        "calibration.scale": scale,
        "calibration.offset": offset,
        "calibration.reference_1_thickness": body.reference_1_thickness,
        "calibration.reference_1_displacement": d1,
        "calibration.reference_2_thickness": body.reference_2_thickness,
        "calibration.reference_2_displacement": d2,
        "calibration.calibrated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "calibration.calibrated_by": user["username"],
    })
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
    company_id = require_company(user)
    _owned(instance_id, user)
    execute("UPDATE core.template_instances SET status = 'active', updated_at = now() "
            "WHERE instance_id = %s AND company_id = %s", (instance_id, company_id))
    return serialize_instance(get_instance(instance_id, company_id))


@router.get("/{instance_id}/dashboard")
def dashboard(instance_id: str, user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    data = build_dashboard(instance_id, company_id)
    if not data:
        raise HTTPException(status_code=404, detail="Instance not found")
    return data


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
