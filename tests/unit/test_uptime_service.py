"""Unit tests for uptime query service."""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, UptimeEvent
from app.services.uptime import (
    count_incidents_24h,
    get_all_availability,
    get_api_uptime_seconds,
    get_availability,
    get_downtime_events,
    get_os_uptime_seconds,
    get_uptime_summary,
)


@pytest_asyncio.fixture
async def uptime_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def uptime_session_factory(uptime_engine):
    return async_sessionmaker(uptime_engine, class_=AsyncSession, expire_on_commit=False)


class TestOsUptime:
    def test_returns_float(self):
        result = get_os_uptime_seconds()
        assert isinstance(result, float)

    def test_returns_zero_on_non_linux(self):
        with patch("app.services.uptime.platform.system", return_value="Darwin"):
            assert get_os_uptime_seconds() == 0.0

    def test_returns_value_on_linux(self):
        with patch("app.services.uptime.platform.system", return_value="Linux"):
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__ = lambda s: s
                mock_open.return_value.__exit__ = lambda s, *a: None
                mock_open.return_value.read = lambda: "12345.67 23456.78\n"
                result = get_os_uptime_seconds()
                assert result == 12345.67

    def test_handles_read_error(self):
        with patch("app.services.uptime.platform.system", return_value="Linux"):
            with patch("builtins.open", side_effect=FileNotFoundError):
                assert get_os_uptime_seconds() == 0.0


class TestApiUptime:
    def test_returns_positive_float(self):
        result = get_api_uptime_seconds()
        assert isinstance(result, float)
        assert result >= 0


@pytest.mark.asyncio
class TestAvailability:
    async def test_100_percent_when_no_events(self, uptime_session_factory):
        result = await get_availability(uptime_session_factory, "vault-backend", 24)
        assert result == 100.0

    async def test_with_resolved_downtime(self, uptime_session_factory):
        now = datetime.now(timezone.utc)
        async with uptime_session_factory() as session:
            # 1 hour downtime in a 24h window = ~95.83%
            session.add(UptimeEvent(
                service_name="vault-vllm",
                event_type="down",
                timestamp=now - timedelta(hours=2),
                duration_seconds=3600.0,
            ))
            await session.commit()

        result = await get_availability(uptime_session_factory, "vault-vllm", 24)
        expected = (1 - 3600 / 86400) * 100
        assert abs(result - expected) < 0.1

    async def test_ongoing_outage(self, uptime_session_factory):
        now = datetime.now(timezone.utc)
        async with uptime_session_factory() as session:
            session.add(UptimeEvent(
                service_name="caddy",
                event_type="down",
                timestamp=now - timedelta(minutes=30),
                duration_seconds=None,  # ongoing
            ))
            await session.commit()

        result = await get_availability(uptime_session_factory, "caddy", 24)
        assert result < 100.0
        assert result > 95.0  # 30 min out of 24h is ~97.9%

    async def test_all_services(self, uptime_session_factory):
        result = await get_all_availability(uptime_session_factory, 24)
        assert isinstance(result, dict)
        assert len(result) > 0
        for svc, pct in result.items():
            assert pct == 100.0  # no events = 100%


@pytest.mark.asyncio
class TestDowntimeEvents:
    async def test_empty_when_no_events(self, uptime_session_factory):
        events, total = await get_downtime_events(uptime_session_factory)
        assert events == []
        assert total == 0

    async def test_returns_down_events(self, uptime_session_factory):
        now = datetime.now(timezone.utc)
        async with uptime_session_factory() as session:
            session.add(UptimeEvent(
                service_name="vault-vllm",
                event_type="down",
                timestamp=now - timedelta(hours=1),
                duration_seconds=300.0,
            ))
            session.add(UptimeEvent(
                service_name="vault-vllm",
                event_type="up",
                timestamp=now - timedelta(minutes=55),
                duration_seconds=300.0,
            ))
            await session.commit()

        events, total = await get_downtime_events(uptime_session_factory)
        assert total == 1  # only "down" events
        assert events[0].service_name == "vault-vllm"

    async def test_filter_by_service(self, uptime_session_factory):
        now = datetime.now(timezone.utc)
        async with uptime_session_factory() as session:
            session.add(UptimeEvent(
                service_name="vault-vllm",
                event_type="down",
                timestamp=now - timedelta(hours=1),
            ))
            session.add(UptimeEvent(
                service_name="caddy",
                event_type="down",
                timestamp=now - timedelta(hours=2),
            ))
            await session.commit()

        events, total = await get_downtime_events(
            uptime_session_factory, service="caddy"
        )
        assert total == 1
        assert events[0].service_name == "caddy"

    async def test_filter_by_since_hours(self, uptime_session_factory):
        now = datetime.now(timezone.utc)
        async with uptime_session_factory() as session:
            session.add(UptimeEvent(
                service_name="vault-vllm",
                event_type="down",
                timestamp=now - timedelta(hours=48),
            ))
            session.add(UptimeEvent(
                service_name="vault-vllm",
                event_type="down",
                timestamp=now - timedelta(hours=1),
            ))
            await session.commit()

        events, total = await get_downtime_events(
            uptime_session_factory, since_hours=24
        )
        assert total == 1

    async def test_pagination(self, uptime_session_factory):
        now = datetime.now(timezone.utc)
        async with uptime_session_factory() as session:
            for i in range(5):
                session.add(UptimeEvent(
                    service_name="vault-vllm",
                    event_type="down",
                    timestamp=now - timedelta(hours=i),
                ))
            await session.commit()

        events, total = await get_downtime_events(
            uptime_session_factory, limit=2, offset=0
        )
        assert total == 5
        assert len(events) == 2


@pytest.mark.asyncio
class TestIncidents24h:
    async def test_zero_when_no_events(self, uptime_session_factory):
        count = await count_incidents_24h(uptime_session_factory)
        assert count == 0

    async def test_counts_recent_only(self, uptime_session_factory):
        now = datetime.now(timezone.utc)
        async with uptime_session_factory() as session:
            session.add(UptimeEvent(
                service_name="vault-vllm",
                event_type="down",
                timestamp=now - timedelta(hours=1),
            ))
            session.add(UptimeEvent(
                service_name="caddy",
                event_type="down",
                timestamp=now - timedelta(hours=48),
            ))
            await session.commit()

        count = await count_incidents_24h(uptime_session_factory)
        assert count == 1


@pytest.mark.asyncio
class TestUptimeSummary:
    async def test_summary_structure(self, uptime_session_factory):
        result = await get_uptime_summary(uptime_session_factory)
        assert "os_uptime_seconds" in result
        assert "api_uptime_seconds" in result
        assert "services" in result
        assert "incidents_24h" in result
        assert isinstance(result["services"], list)
        assert len(result["services"]) > 0
        for svc in result["services"]:
            assert "service_name" in svc
            assert "availability_24h" in svc
            assert "availability_7d" in svc
            assert "availability_30d" in svc
            assert "current_status" in svc
