from fastapi import APIRouter, Depends, Query, Request

import app.core.database as db_module
from app.dependencies import require_admin
from app.schemas.uptime import (
    AvailabilityResponse,
    DowntimeEvent,
    ServiceAvailability,
    UptimeEventsResponse,
    UptimeSummaryResponse,
)
from app.services.uptime import (
    get_all_availability,
    get_downtime_events,
    get_uptime_summary,
)

router = APIRouter()


@router.get("/vault/system/uptime")
async def uptime_summary(request: Request) -> UptimeSummaryResponse:
    """OS uptime, API process uptime, per-service availability (24h/7d/30d)."""
    monitor = getattr(request.app.state, "uptime_monitor", None)
    last_state = monitor.last_state if monitor else None

    data = await get_uptime_summary(db_module.async_session, last_state=last_state)

    return UptimeSummaryResponse(
        os_uptime_seconds=data["os_uptime_seconds"],
        api_uptime_seconds=data["api_uptime_seconds"],
        services=[ServiceAvailability(**s) for s in data["services"]],
        incidents_24h=data["incidents_24h"],
    )


@router.get(
    "/vault/system/uptime/events",
    dependencies=[Depends(require_admin)],
)
async def uptime_events(
    service: str | None = None,
    since_hours: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> UptimeEventsResponse:
    """Paginated downtime events (admin only)."""
    events, total = await get_downtime_events(
        db_module.async_session,
        service=service,
        limit=limit,
        offset=offset,
        since_hours=since_hours,
    )

    return UptimeEventsResponse(
        events=[
            DowntimeEvent(
                id=e.id,
                service_name=e.service_name,
                event_type=e.event_type,
                timestamp=e.timestamp.isoformat() if e.timestamp else "",
                duration_seconds=e.duration_seconds,
                details=e.details,
            )
            for e in events
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/vault/system/uptime/availability")
async def uptime_availability(
    service: str | None = None,
    window: int = Query(24, description="Window in hours (24, 168, 720, 2160)"),
) -> AvailabilityResponse:
    """Availability % for specific service or all services."""
    if service:
        from app.services.uptime import get_availability

        pct = await get_availability(db_module.async_session, service, window)
        services = {service: pct}
    else:
        services = await get_all_availability(db_module.async_session, window)

    return AvailabilityResponse(window_hours=window, services=services)
