"""Catalogue routes — BLOCK 2 profiles/models and BLOCK 4 templates (data-driven)."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import fetch_all, fetch_one
from ..deps import get_current_user

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])


def _json(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


@router.get("/applications")
def list_applications(user: dict = Depends(get_current_user)) -> dict:
    return {"applications": fetch_all(
        "SELECT application_key, application_name, description, version, status "
        "FROM core.applications ORDER BY application_name")}


@router.get("/device-profiles")
def list_profiles(user: dict = Depends(get_current_user)) -> dict:
    return {"device_profiles": fetch_all(
        "SELECT profile_id, manufacturer, series, device_type, connector_type, description, status "
        "FROM core.device_profiles WHERE status = 'active' ORDER BY manufacturer, series")}


@router.get("/device-profiles/{profile_id}/models")
def models_for_profile(profile_id: str, user: dict = Depends(get_current_user)) -> dict:
    rows = fetch_all(
        "SELECT model_key, model_name, centre_distance, range_min, range_max, unit, resolution, spec "
        "FROM core.device_models WHERE profile_id = %s AND status = 'active' ORDER BY centre_distance",
        (profile_id,),
    )
    for r in rows:
        r["spec"] = _json(r.get("spec"), {})
    return {"models": rows}


@router.get("/device-models")
def list_models(
    search: Optional[str] = Query(default=None),
    user: dict = Depends(get_current_user),
) -> dict:
    sql = (
        "SELECT m.model_key, m.model_name, m.centre_distance, m.range_min, m.range_max, m.unit, m.resolution, "
        "p.profile_id, p.manufacturer, p.series, p.device_type, p.connector_type "
        "FROM core.device_models m JOIN core.device_profiles p ON p.profile_id = m.profile_id "
        "WHERE m.status = 'active'"
    )
    params: list = []
    if search:
        sql += " AND (m.model_name ILIKE %s OR p.manufacturer ILIKE %s OR p.series ILIKE %s OR m.model_key ILIKE %s)"
        like = f"%{search}%"
        params = [like, like, like, like]
    sql += " ORDER BY p.manufacturer, m.model_name"
    return {"device_models": fetch_all(sql, params)}


@router.get("/device-models/{model_key}")
def get_model(model_key: str, user: dict = Depends(get_current_user)) -> dict:
    row = fetch_one(
        "SELECT m.*, p.manufacturer, p.series, p.device_type, p.connector_type, p.decoding "
        "FROM core.device_models m JOIN core.device_profiles p ON p.profile_id = m.profile_id "
        "WHERE m.model_key = %s",
        (model_key,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Device model not found")
    row["spec"] = _json(row.get("spec"), {})
    row["decoding"] = _json(row.get("decoding"), {})
    return row


@router.get("/device-models/{model_key}/purposes")
def purposes_for_model(model_key: str, user: dict = Depends(get_current_user)) -> dict:
    rows = fetch_all(
        """
        SELECT p.purpose_key, p.name, p.description, p.icon, p.sort_order,
               (SELECT count(*) FROM core.application_templates t
                 WHERE t.purpose_key = p.purpose_key AND t.status = 'active') AS template_count
        FROM core.device_model_purposes mp
        JOIN core.purposes p ON p.purpose_key = mp.purpose_key
        WHERE mp.model_key = %s AND p.status = 'active'
        ORDER BY p.sort_order
        """,
        (model_key,),
    )
    return {"purposes": rows}


@router.get("/purposes/{purpose_key}/templates")
def templates_for_purpose(
    purpose_key: str,
    model_key: Optional[str] = Query(default=None),
    user: dict = Depends(get_current_user),
) -> dict:
    sql = (
        "SELECT template_id, name, version, category, sensor_layout, description, purpose_key, model_key, processor_key, "
        "inputs, dashboard, sort_order FROM core.application_templates "
        "WHERE purpose_key = %s AND status = 'active'"
    )
    params: list = [purpose_key]
    if model_key:
        sql += " AND (model_key IS NULL OR model_key = %s)"
        params.append(model_key)
    sql += " ORDER BY sort_order"
    rows = fetch_all(sql, params)
    for r in rows:
        r["inputs"] = _json(r.get("inputs"), [])
        r["dashboard"] = _json(r.get("dashboard"), {})
    return {"templates": rows}


@router.get("/templates/{template_id}")
def get_template(template_id: str, user: dict = Depends(get_current_user)) -> dict:
    row = fetch_one("SELECT * FROM core.application_templates WHERE template_id = %s", (template_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    row["inputs"] = _json(row.get("inputs"), [])
    row["dashboard"] = _json(row.get("dashboard"), {})
    row["fields"] = fetch_all(
        "SELECT field_key, source, data_type, unit FROM core.template_fields "
        "WHERE template_id = %s ORDER BY sort_order",
        (template_id,),
    )
    return row
