"""Uptime query service — availability calculations and downtime event queries."""

import platform
import time
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import UptimeEvent
from app.services.service_manager import MANAGED_SERVICES

logger = structlog.get_logger()

_api_start = time.monotonic()


def get_os_uptime_seconds() -> float:
    """Read OS uptime from /proc/uptime. Returns 0.0 on non-Linux."""
    if platform.system() != "Linux":
        return 0.0
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except Exception:
        return 0.0


def get_api_uptime_seconds() -> float:
    """Seconds since the API process started."""
    return round(time.monotonic() - _api_start, 1)


async def get_availability(
    session_factory: async_sessionmaker,
    service_name: str,
    window_hours: int,
) -> float:
    """Calculate availability % for a service over the given window.

    Returns 100.0 if no downtime events exist in the window.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    window_seconds = window_hours * 3600.0

    async with session_factory() as session:
        result = await session.execute(
            select(UptimeEvent)
            .where(
                UptimeEvent.service_name == service_name,
                UptimeEvent.event_type == "down",
                UptimeEvent.timestamp >= cutoff,
            )
            .order_by(UptimeEvent.timestamp)
        )
        down_events = list(result.scalars().all())

    if not down_events:
        return 100.0

    total_downtime = 0.0
    for evt in down_events:
        if evt.duration_seconds is not None:
            total_downtime += evt.duration_seconds
        else:
            # Ongoing outage — count from event timestamp to now
            elapsed = (datetime.now(timezone.utc) - evt.timestamp.replace(tzinfo=timezone.utc)).total_seconds()
            total_downtime += max(0.0, elapsed)

    availability = max(0.0, (1.0 - total_downtime / window_seconds) * 100.0)
    return round(availability, 4)


async def get_all_availability(
    session_factory: async_sessionmaker,
    window_hours: int,
) -> dict[str, float]:
    """Return availability % for all monitored services."""
    result = {}
    for service in sorted(MANAGED_SERVICES):
        result[service] = await get_availability(session_factory, service, window_hours)
    return result


async def get_downtime_events(
    session_factory: async_sessionmaker,
    service: str | None = None,
    limit: int = 50,
    offset: int = 0,
    since_hours: int | None = None,
) -> tuple[list[UptimeEvent], int]:
    """Return paginated downtime events, newest first."""
    async with session_factory() as session:
        query = select(UptimeEvent).where(UptimeEvent.event_type == "down")
        count_query = select(func.count()).select_from(UptimeEvent).where(
            UptimeEvent.event_type == "down"
        )

        if service:
            query = query.where(UptimeEvent.service_name == service)
            count_query = count_query.where(UptimeEvent.service_name == service)

        if since_hours:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
            query = query.where(UptimeEvent.timestamp >= cutoff)
            count_query = count_query.where(UptimeEvent.timestamp >= cutoff)

        total = await session.scalar(count_query) or 0

        result = await session.execute(
            query.order_by(UptimeEvent.timestamp.desc()).offset(offset).limit(limit)
        )
        events = list(result.scalars().all())

    return events, total


async def count_incidents_24h(session_factory: async_sessionmaker) -> int:
    """Count downtime events in the last 24 hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(UptimeEvent)
            .where(
                UptimeEvent.event_type == "down",
                UptimeEvent.timestamp >= cutoff,
            )
        )
    return count or 0


async def get_uptime_summary(
    session_factory: async_sessionmaker,
    last_state: dict[str, str] | None = None,
) -> dict:
    """Combined response: OS uptime + per-service availability for 24h/7d/30d."""
    services = []
    for svc in sorted(MANAGED_SERVICES):
        avail_24h = await get_availability(session_factory, svc, 24)
        avail_7d = await get_availability(session_factory, svc, 168)
        avail_30d = await get_availability(session_factory, svc, 720)

        current = "unknown"
        if last_state and svc in last_state:
            current = last_state[svc]

        services.append({
            "service_name": svc,
            "availability_24h": avail_24h,
            "availability_7d": avail_7d,
            "availability_30d": avail_30d,
            "current_status": current,
        })

    incidents = await count_incidents_24h(session_factory)

    return {
        "os_uptime_seconds": get_os_uptime_seconds(),
        "api_uptime_seconds": get_api_uptime_seconds(),
        "services": services,
        "incidents_24h": incidents,
    }
