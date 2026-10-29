"""Authenticated sensor-analytics HTTP endpoints (Phase 2).

These are thin adapters over ``AnalyticsService``. They reuse the platform's
existing Bearer-token dependency and the same tenant context as the AI
assistant; the request can never supply a company id. The AI tools and these
HTTP routes therefore return identical numbers for the same machine/time range.

Canonical prefix is ``/api/v1/analytics``. ``/api/analytics`` aliases are also
registered for clients that follow the endpoint names in the Phase-2 spec.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from ..deps import get_current_user
from ..services.ai_assistant.analytics import AnalyticsError, AnalyticsService
from ..services.ai_assistant.context import build_context

_STATUS = {
    "machine_not_found": 404,
    "no_machines": 404,
    "ambiguous_machine": 400,
    "ambiguous_sensor": 400,
    "invalid_sensor": 400,
    "ambiguous_date": 400,
    "invalid_time": 400,
    "invalid_date": 400,
    "invalid_range": 400,
    "range_too_large": 400,
    "ambiguous_shift": 400,
}


def get_analytics_service() -> AnalyticsService:
    """Dependency hook (overridable in tests)."""
    return AnalyticsService()


def _raise(exc: AnalyticsError):
    from fastapi import HTTPException

    status = _STATUS.get(exc.code, 400)
    raise HTTPException(status_code=status, detail=exc.as_dict())


def _call(func, *args, **kwargs) -> dict:
    try:
        return func(*args, **kwargs)
    except AnalyticsError as exc:
        _raise(exc)


def _time_kwargs(period, date, shift, start, end) -> dict:
    return {"period": period, "date": date, "shift": shift, "start": start, "end": end}


def _shape_statistics(result: dict) -> dict:
    stats = result.get("statistics") or {}
    metric = result.get("metric") or "thickness"
    minimum = stats.get("minimum")
    maximum = stats.get("maximum")
    values = int(stats.get("values_count") or 0)
    result.update({
        "average": stats.get("average"),
        "average_thickness": stats.get("average") if metric == "thickness" else None,
        "minimum": minimum,
        "maximum": maximum,
        "min": minimum,
        "max": maximum,
        "range": round(maximum - minimum, 6) if minimum is not None and maximum is not None else None,
        "samples": int(stats.get("samples") or 0),
        "values_count": values,
        "has_data": values > 0,
        "empty": values == 0,
    })
    return result


def _build_router(prefix: str) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["analytics"])

    @router.get("/readings/count")
    def reading_count(
        machine_id: Optional[str] = Query(default=None, max_length=128),
        machine_name: Optional[str] = Query(default=None, max_length=255),
        sensor: Optional[str] = Query(default=None, max_length=128),
        metric: str = Query(default="thickness"),
        period: Optional[str] = Query(default=None),
        date: Optional[str] = Query(default=None),
        shift: Optional[str] = Query(default=None),
        start: Optional[str] = Query(default=None),
        end: Optional[str] = Query(default=None),
        user: dict = Depends(get_current_user),
        service: AnalyticsService = Depends(get_analytics_service),
    ) -> dict:
        ctx = build_context(user)
        result = _call(
            service.get_reading_count, ctx, machine_id=machine_id,
            machine_name=machine_name, sensor=sensor, metric=metric,
            **_time_kwargs(period, date, shift, start, end),
        )
        count = int(result.get("reading_count") or 0)
        with_values = int(result.get("with_values") or 0)
        result.update({
            "count": count,
            "readings": count,
            "database_rows": count,
            "sensor_samples": with_values,
            "empty": count == 0,
        })
        return result

    @router.get("/thickness/average")
    def thickness_average(
        machine_id: Optional[str] = Query(default=None, max_length=128),
        machine_name: Optional[str] = Query(default=None, max_length=255),
        sensor: Optional[str] = Query(default=None, max_length=128),
        metric: str = Query(default="thickness"),
        period: Optional[str] = Query(default=None),
        date: Optional[str] = Query(default=None),
        shift: Optional[str] = Query(default=None),
        start: Optional[str] = Query(default=None),
        end: Optional[str] = Query(default=None),
        user: dict = Depends(get_current_user),
        service: AnalyticsService = Depends(get_analytics_service),
    ) -> dict:
        ctx = build_context(user)
        result = _call(
            service.get_thickness_statistics, ctx, machine_id=machine_id,
            machine_name=machine_name, sensor=sensor, metric=metric,
            **_time_kwargs(period, date, shift, start, end),
        )
        return _shape_statistics(result)

    @router.get("/thickness/range")
    def thickness_range(
        machine_id: Optional[str] = Query(default=None, max_length=128),
        machine_name: Optional[str] = Query(default=None, max_length=255),
        sensor: Optional[str] = Query(default=None, max_length=128),
        metric: str = Query(default="thickness"),
        period: Optional[str] = Query(default=None),
        date: Optional[str] = Query(default=None),
        shift: Optional[str] = Query(default=None),
        start: Optional[str] = Query(default=None),
        end: Optional[str] = Query(default=None),
        user: dict = Depends(get_current_user),
        service: AnalyticsService = Depends(get_analytics_service),
    ) -> dict:
        ctx = build_context(user)
        result = _call(
            service.get_thickness_statistics, ctx, machine_id=machine_id,
            machine_name=machine_name, sensor=sensor, metric=metric,
            **_time_kwargs(period, date, shift, start, end),
        )
        return _shape_statistics(result)

    @router.get("/thickness/latest")
    def thickness_latest(
        machine_id: Optional[str] = Query(default=None, max_length=128),
        machine_name: Optional[str] = Query(default=None, max_length=255),
        sensor: Optional[str] = Query(default=None, max_length=128),
        metric: str = Query(default="thickness"),
        period: Optional[str] = Query(default=None),
        date: Optional[str] = Query(default=None),
        shift: Optional[str] = Query(default=None),
        start: Optional[str] = Query(default=None),
        end: Optional[str] = Query(default=None),
        user: dict = Depends(get_current_user),
        service: AnalyticsService = Depends(get_analytics_service),
    ) -> dict:
        ctx = build_context(user)
        return _call(
            service.get_latest_measurement, ctx, machine_id=machine_id,
            machine_name=machine_name, sensor=sensor, metric=metric,
            **_time_kwargs(period, date, shift, start, end),
        )

    @router.get("/thickness/out-of-range")
    def thickness_out_of_range(
        machine_id: Optional[str] = Query(default=None, max_length=128),
        machine_name: Optional[str] = Query(default=None, max_length=255),
        sensor: Optional[str] = Query(default=None, max_length=128),
        metric: str = Query(default="thickness"),
        period: Optional[str] = Query(default=None),
        date: Optional[str] = Query(default=None),
        shift: Optional[str] = Query(default=None),
        start: Optional[str] = Query(default=None),
        end: Optional[str] = Query(default=None),
        user: dict = Depends(get_current_user),
        service: AnalyticsService = Depends(get_analytics_service),
    ) -> dict:
        ctx = build_context(user)
        return _call(
            service.get_out_of_range_readings, ctx, machine_id=machine_id,
            machine_name=machine_name, sensor=sensor, metric=metric,
            **_time_kwargs(period, date, shift, start, end),
        )

    @router.get("/daily-summary")
    def daily_summary(
        machine_id: Optional[str] = Query(default=None, max_length=128),
        machine_name: Optional[str] = Query(default=None, max_length=255),
        sensor: Optional[str] = Query(default=None, max_length=128),
        metric: str = Query(default="thickness"),
        period: Optional[str] = Query(default=None),
        date: Optional[str] = Query(default=None),
        shift: Optional[str] = Query(default=None),
        start: Optional[str] = Query(default=None),
        end: Optional[str] = Query(default=None),
        include_shifts: bool = Query(default=False),
        user: dict = Depends(get_current_user),
        service: AnalyticsService = Depends(get_analytics_service),
    ) -> dict:
        ctx = build_context(user)
        return _call(
            service.get_daily_summary, ctx, machine_id=machine_id,
            machine_name=machine_name, sensor=sensor, metric=metric,
            include_shifts=include_shifts,
            **_time_kwargs(period, date, shift, start, end),
        )

    @router.get("/filtered-unfiltered")
    def filtered_unfiltered(
        machine_id: Optional[str] = Query(default=None, max_length=128),
        machine_name: Optional[str] = Query(default=None, max_length=255),
        period: Optional[str] = Query(default=None),
        date: Optional[str] = Query(default=None),
        shift: Optional[str] = Query(default=None),
        start: Optional[str] = Query(default=None),
        end: Optional[str] = Query(default=None),
        user: dict = Depends(get_current_user),
        service: AnalyticsService = Depends(get_analytics_service),
    ) -> dict:
        ctx = build_context(user)
        return _call(
            service.compare_filtered_unfiltered, ctx, machine_id=machine_id,
            machine_name=machine_name,
            **_time_kwargs(period, date, shift, start, end),
        )

    return router


router = _build_router("/api/v1/analytics")
legacy_router = _build_router("/api/analytics")
