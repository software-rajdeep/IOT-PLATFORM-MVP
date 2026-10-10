"""Platform meta routes: health, registries (drivers/processors)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..db import fetch_one
from ..deps import get_current_user
from ..drivers import registered_keys as driver_keys
from ..processors import registered_keys as processor_keys

router = APIRouter(prefix="/api/v1", tags=["meta"])


@router.get("/health")
def health() -> dict:
    db_ok = False
    try:
        db_ok = bool(fetch_one("SELECT 1 AS ok"))
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok}


@router.get("/meta/registries")
def registries(user: dict = Depends(get_current_user)) -> dict:
    return {"drivers": driver_keys(), "processors": processor_keys()}
