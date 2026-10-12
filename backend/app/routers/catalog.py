"""Catalogue routes — BLOCK 2 profiles/models and BLOCK 4 templates (data-driven)."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import fetch_all, fetch_one
from ..deps import get_current_user
from ..services.compat import compatible
from ..services.templates import parse_template

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


def _model_signals(model_key: str) -> Optional[list]:
    row = fetch_one(
        "SELECT p.signals FROM core.device_models m JOIN core.device_profiles p ON p.profile_id = m.profile_id "
        "WHERE m.model_key = %s", (model_key,))
    return _json(row["signals"], []) if row else None


def _compatible_templates(model_key: str) -> list[dict]:
    signals = _model_signals(model_key) or []
    rows = fetch_all(
        "SELECT * FROM core.application_templates WHERE status = 'active' AND (model_key IS NULL OR model_key = %s) "
        "ORDER BY sort_order", (model_key,))
    return [t for t in (parse_template(r) for r in rows) if compatible(signals, t)]


@router.get("/device-models/{model_key}/purposes")
def purposes_for_model(model_key: str, user: dict = Depends(get_current_user)) -> dict:
    """Purposes that have at least one application compatible with this product's signals."""
    counts: dict = {}
    for t in _compatible_templates(model_key):
        counts[t["purpose_key"]] = counts.get(t["purpose_key"], 0) + 1
    if not counts:
        return {"purposes": []}
    rows = fetch_all(
        "SELECT purpose_key, name, description, icon, sort_order FROM core.purposes "
        "WHERE status = 'active' AND purpose_key = ANY(%s) ORDER BY sort_order",
        (list(counts.keys()),),
    )
    for r in rows:
        r["template_count"] = counts[r["purpose_key"]]
    return {"purposes": rows}


@router.get("/solutions")
def solutions(user: dict = Depends(get_current_user)) -> dict:
    """The solution catalog: every compatible (product, application) pair, plus curated listings.

    Pairs are computed from device signals x application inputs; core.solution_listings only
    adds presentation (title, subtitle, icon, order) and "coming soon" cards.
    """
    listings = fetch_all("SELECT * FROM core.solution_listings WHERE status <> 'hidden' ORDER BY sort_order")
    by_pair = {(l["model_key"], l["template_id"]): l for l in listings if l["model_key"] and l["template_id"]}
    models = fetch_all(
        "SELECT m.model_key, m.model_name, m.centre_distance, m.range_min, m.range_max, m.unit, m.resolution, "
        "p.profile_id, p.manufacturer, p.series, p.device_type, p.connector_type, p.signals "
        "FROM core.device_models m JOIN core.device_profiles p ON p.profile_id = m.profile_id "
        "WHERE m.status = 'active' AND p.status = 'active' ORDER BY p.manufacturer, m.centre_distance")
    templates = [parse_template(r) for r in fetch_all(
        "SELECT * FROM core.application_templates WHERE status = 'active' ORDER BY sort_order")]
    out = []
    for m in models:
        signals = _json(m.pop("signals"), [])
        m["signals"] = signals
        for t in templates:
            if t.get("model_key") and t["model_key"] != m["model_key"]:
                continue
            if not compatible(signals, t):
                continue
            listing = by_pair.get((m["model_key"], t["template_id"])) or {}
            out.append({
                "solution_id": f"{m['model_key']}:{t['template_id']}",
                "status": "available",
                "title": listing.get("title") or t["name"],
                "subtitle": listing.get("subtitle") or t.get("description"),
                "icon": listing.get("icon") or (t.get("category") == "monitoring" and "activity") or "layers",
                "featured": bool(listing),
                "sort_order": listing.get("sort_order", 500 + (t.get("sort_order") or 0)),
                "model": m,
                "template": {k: t.get(k) for k in ("template_id", "name", "version", "category", "purpose_key",
                                                  "processor_key", "description", "requires", "optional",
                                                  "outputs", "commissioning", "sensor_layout")},
            })
    for l in listings:
        if l["status"] == "coming_soon":
            out.append({"solution_id": l["listing_id"], "status": "coming_soon", "title": l["title"],
                        "subtitle": l["subtitle"], "icon": l["icon"], "featured": False,
                        "sort_order": 1000 + l["sort_order"],
                        "model": {"model_name": l["product"]}, "template": {"name": l["application"]}})
    out.sort(key=lambda x: (x["status"] != "available", not x["featured"], x["sort_order"], x["model"].get("model_name") or ""))
    return {"solutions": out}


@router.get("/widget-types")
def widget_types(user: dict = Depends(get_current_user)) -> dict:
    rows = fetch_all("SELECT * FROM core.widget_types ORDER BY sort_order")
    for r in rows:
        r["requires"] = _json(r.get("requires"), [])
    return {"widget_types": rows}


@router.get("/units")
def list_units(user: dict = Depends(get_current_user)) -> dict:
    rows = fetch_all("SELECT unit_key, label, dimension FROM core.units ORDER BY dimension, to_base_factor")
    return {"units": rows}


@router.get("/purposes/{purpose_key}/templates")
def templates_for_purpose(
    purpose_key: str,
    model_key: Optional[str] = Query(default=None),
    user: dict = Depends(get_current_user),
) -> dict:
    sql = "SELECT * FROM core.application_templates WHERE purpose_key = %s AND status = 'active'"
    params: list = [purpose_key]
    if model_key:
        sql += " AND (model_key IS NULL OR model_key = %s)"
        params.append(model_key)
    sql += " ORDER BY sort_order"
    rows = [parse_template(r) for r in fetch_all(sql, params)]
    if model_key:
        signals = _model_signals(model_key) or []
        rows = [r for r in rows if compatible(signals, r)]
    return {"templates": rows}


@router.get("/templates/{template_id}")
def get_template(template_id: str, user: dict = Depends(get_current_user)) -> dict:
    row = fetch_one("SELECT * FROM core.application_templates WHERE template_id = %s", (template_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    row = parse_template(row)
    row["fields"] = fetch_all(
        "SELECT field_key, source, data_type, unit FROM core.template_fields "
        "WHERE template_id = %s ORDER BY sort_order",
        (template_id,),
    )
    return row
