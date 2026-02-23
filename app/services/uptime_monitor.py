"""Background service health monitor — polls services and records uptime events."""

import asyncio
import platform
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.database import UptimeEvent
from app.services.service_manager import MANAGED_SERVICES, ServiceManager

logger = structlog.get_logger()

POLL_INTERVAL = 30  # seconds


class UptimeMonitor:
    """Polls managed services and records state transitions as UptimeEvents."""

    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory
        self._service_manager = ServiceManager()
        self._last_state: dict[str, str] = {}
        self._task: asyncio.Task | None = None
        self._running = False

    @property
    def last_state(self) -> dict[str, str]:
        return dict(self._last_state)

    async def start(self) -> None:
        """Start the background polling task."""
        if platform.system() != "Linux":
            # On non-Linux (dev mode), seed all services as unknown
            for svc in MANAGED_SERVICES:
                self._last_state[svc] = "unknown"
            logger.info("uptime_monitor_skipped", reason="non-Linux platform")
            return

        self._running = True
        # Seed initial state without recording events
        await self._seed_initial_state()
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("uptime_monitor_started", services=len(MANAGED_SERVICES))

    async def stop(self) -> None:
        """Stop the background polling task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("uptime_monitor_stopped")

    async def _seed_initial_state(self) -> None:
        """Check all services once and set initial state (no events recorded)."""
        for svc in sorted(MANAGED_SERVICES):
            status = await self._service_manager.get_service_status(svc)
            self._last_state[svc] = "up" if status["status"] == "running" else "down"
        logger.info("uptime_monitor_seeded", states=dict(self._last_state))

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await asyncio.sleep(POLL_INTERVAL)
                await self._check_all()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("uptime_monitor_error")
                await asyncio.sleep(POLL_INTERVAL)

    async def _check_all(self) -> None:
        """Check all services and record state changes."""
        for svc in sorted(MANAGED_SERVICES):
            try:
                status = await self._service_manager.get_service_status(svc)
                new_state = "up" if status["status"] == "running" else "down"
                old_state = self._last_state.get(svc, "unknown")

                if old_state != new_state:
                    await self._record_transition(svc, old_state, new_state)
                    self._last_state[svc] = new_state
            except Exception:
                logger.exception("uptime_check_failed", service=svc)

    async def _record_transition(
        self, service: str, old_state: str, new_state: str
    ) -> None:
        """Record a state transition as an UptimeEvent."""
        now = datetime.now(timezone.utc)

        if new_state == "down":
            # Service went down
            event = UptimeEvent(
                service_name=service,
                event_type="down",
                timestamp=now,
                details=f"Transitioned from {old_state} to down",
            )
            async with self._session_factory() as session:
                session.add(event)
                await session.commit()
            logger.warning("service_down", service=service)

        elif new_state == "up" and old_state == "down":
            # Service recovered — find the last "down" event and compute duration
            async with self._session_factory() as session:
                result = await session.execute(
                    select(UptimeEvent)
                    .where(
                        UptimeEvent.service_name == service,
                        UptimeEvent.event_type == "down",
                        UptimeEvent.duration_seconds.is_(None),
                    )
                    .order_by(UptimeEvent.timestamp.desc())
                    .limit(1)
                )
                last_down = result.scalar_one_or_none()

                duration = None
                if last_down:
                    ts = last_down.timestamp
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    duration = (now - ts).total_seconds()
                    last_down.duration_seconds = duration
                    session.add(last_down)

                # Record the "up" event
                up_event = UptimeEvent(
                    service_name=service,
                    event_type="up",
                    timestamp=now,
                    duration_seconds=duration,
                    details=f"Recovered after {duration:.0f}s" if duration else "Recovered",
                )
                session.add(up_event)
                await session.commit()

            logger.info("service_recovered", service=service, downtime_seconds=duration)
