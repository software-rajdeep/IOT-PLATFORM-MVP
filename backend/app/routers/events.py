"""Event / incident APIs (BLOCK 5 rule engine, Phase 3).

All routes are company-scoped through the authenticated user. Incident history
is independent from measurement retention, so these reads stay available after
circular reading buffers overwrite raw telemetry.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_current_user, require_company
from ..services import incidents

router = APIRouter(prefix="/api/v1/events", tags=["events"])


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _range(start: Optional[datetime], end: Optional[datetime]) -> tuple[datetime, datetime]:
    start, end = _aware(start), _aware(end) or datetime.now(timezone.utc)
    start = start or (end - timedelta(days=30))
    if start >= end:
        raise HTTPException(status_code=400, detail="The start time must be before the end time")
    return start, end


@router.get("/active")
def active(instance_id: Optional[str] = None, device_id: Optional[str] = None,
           user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    rows = incidents.active_incidents(company_id, instance_id=instance_id, device_id=device_id)
    return {"incidents": rows, "count": len(rows)}


@router.get("/incidents")
def history(
    instance_id: Optional[str] = None,
    device_id: Optional[str] = None,
    status: Optional[str] = Query(default=None, pattern="^(open|closed)$"),
    incident_type: Optional[str] = Query(default=None, pattern="^(out_of_range|offline)$"),
    sensor_field: Optional[str] = None,
    start: Optional[datetime] = Query(default=None, alias="from"),
    end: Optional[datetime] = Query(default=None, alias="to"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_user),
) -> dict:
    company_id = require_company(user)
    rows = incidents.list_incidents(
        company_id, instance_id=instance_id, device_id=device_id, status=status,
        incident_type=incident_type, sensor_field=sensor_field,
        start=_aware(start), end=_aware(end), limit=limit, offset=offset,
    )
    return {"incidents": rows, "count": len(rows), "limit": limit, "offset": offset}


@router.get("/incidents/{incident_id}")
def detail(incident_id: int, user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    incident = incidents.get_incident(company_id, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"incident": incident}


@router.get("/counts")
def counts(
    instance_id: Optional[str] = None,
    device_id: Optional[str] = None,
    start: Optional[datetime] = Query(default=None, alias="from"),
    end: Optional[datetime] = Query(default=None, alias="to"),
    tz: str = Query(default="UTC", min_length=1, max_length=64),
    user: dict = Depends(get_current_user),
) -> dict:
    company_id = require_company(user)
    start, end = _range(start, end)
    try:
        data = incidents.incident_counts(company_id, instance_id=instance_id, device_id=device_id,
                                         start=start, end=end, tz=tz)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not build incident counts: {exc}") from exc
    return {"instance_id": instance_id, "device_id": device_id,
            "from": start.isoformat(), "to": end.isoformat(), **data}


@router.get("/connectivity")
def connectivity(user: dict = Depends(get_current_user)) -> dict:
    company_id = require_company(user)
    machines = incidents.machine_connectivity(company_id)
    offline = incidents.list_incidents(company_id, status="open", incident_type="offline", limit=500)
    return {"machines": machines, "offline_incidents": offline}


@router.post("/connectivity/check")
def check_connectivity(user: dict = Depends(get_current_user)) -> dict:
    """Run the freshness check now (the source runtime also does this periodically)."""
    company_id = require_company(user)
    return {"changes": incidents.check_connectivity(company_id)}
