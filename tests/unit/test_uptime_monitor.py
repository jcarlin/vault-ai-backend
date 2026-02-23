"""Unit tests for the background UptimeMonitor."""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, UptimeEvent
from app.services.uptime_monitor import UptimeMonitor


@pytest_asyncio.fixture
async def monitor_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def monitor_session_factory(monitor_engine):
    return async_sessionmaker(monitor_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def monitor(monitor_session_factory):
    m = UptimeMonitor(session_factory=monitor_session_factory)
    yield m
    if m._task:
        await m.stop()


def _mock_service_status(name, status="running"):
    return {"name": name, "status": status, "uptime_seconds": 100}


@pytest.mark.asyncio
class TestInitialState:
    async def test_seeds_initial_state(self, monitor):
        with patch("app.services.uptime_monitor.platform.system", return_value="Linux"):
            with patch.object(
                monitor._service_manager,
                "get_service_status",
                side_effect=lambda name: _mock_service_status(name, "running"),
            ):
                await monitor._seed_initial_state()
                assert len(monitor.last_state) > 0
                for svc, state in monitor.last_state.items():
                    assert state == "up"

    async def test_seeds_down_state(self, monitor):
        with patch("app.services.uptime_monitor.platform.system", return_value="Linux"):
            with patch.object(
                monitor._service_manager,
                "get_service_status",
                side_effect=lambda name: _mock_service_status(name, "stopped"),
            ):
                await monitor._seed_initial_state()
                for svc, state in monitor.last_state.items():
                    assert state == "down"

    async def test_non_linux_skips(self, monitor):
        with patch("app.services.uptime_monitor.platform.system", return_value="Darwin"):
            await monitor.start()
            for svc, state in monitor.last_state.items():
                assert state == "unknown"


@pytest.mark.asyncio
class TestStateTransitions:
    async def test_no_event_on_same_state(self, monitor, monitor_session_factory):
        from app.services.service_manager import MANAGED_SERVICES

        # Set all services to "up"
        for svc in MANAGED_SERVICES:
            monitor._last_state[svc] = "up"

        with patch.object(
            monitor._service_manager,
            "get_service_status",
            side_effect=lambda name: _mock_service_status(name, "running"),
        ):
            await monitor._check_all()

        async with monitor_session_factory() as session:
            result = await session.execute(select(UptimeEvent))
            events = list(result.scalars().all())
        assert len(events) == 0

    async def test_records_down_event(self, monitor, monitor_session_factory):
        from app.services.service_manager import MANAGED_SERVICES

        # Set all services to "up" so only vault-vllm transitions
        for svc in MANAGED_SERVICES:
            monitor._last_state[svc] = "up"

        async def mock_status(name):
            if name == "vault-vllm":
                return _mock_service_status(name, "stopped")
            return _mock_service_status(name, "running")

        with patch.object(
            monitor._service_manager,
            "get_service_status",
            side_effect=mock_status,
        ):
            await monitor._check_all()

        assert monitor._last_state["vault-vllm"] == "down"

        async with monitor_session_factory() as session:
            result = await session.execute(select(UptimeEvent))
            events = list(result.scalars().all())
        assert len(events) == 1
        assert events[0].event_type == "down"
        assert events[0].service_name == "vault-vllm"

    async def test_records_up_event_with_duration(self, monitor, monitor_session_factory):
        from app.services.service_manager import MANAGED_SERVICES

        # First record a down event
        now = datetime.now(timezone.utc)
        async with monitor_session_factory() as session:
            session.add(UptimeEvent(
                service_name="vault-vllm",
                event_type="down",
                timestamp=now - timedelta(minutes=5),
                duration_seconds=None,
            ))
            await session.commit()

        # Set all services to "up" except vault-vllm which is "down"
        for svc in MANAGED_SERVICES:
            monitor._last_state[svc] = "up"
        monitor._last_state["vault-vllm"] = "down"

        with patch.object(
            monitor._service_manager,
            "get_service_status",
            side_effect=lambda name: _mock_service_status(name, "running"),
        ):
            await monitor._check_all()

        assert monitor._last_state["vault-vllm"] == "up"

        async with monitor_session_factory() as session:
            result = await session.execute(
                select(UptimeEvent).order_by(UptimeEvent.id)
            )
            events = list(result.scalars().all())

        assert len(events) == 2
        # Down event should now have duration filled
        down_evt = events[0]
        assert down_evt.event_type == "down"
        assert down_evt.duration_seconds is not None
        assert down_evt.duration_seconds > 0
        # Up event
        up_evt = events[1]
        assert up_evt.event_type == "up"
        assert up_evt.duration_seconds is not None


@pytest.mark.asyncio
class TestLifecycle:
    async def test_start_stop(self, monitor):
        with patch("app.services.uptime_monitor.platform.system", return_value="Linux"):
            with patch.object(
                monitor._service_manager,
                "get_service_status",
                side_effect=lambda name: _mock_service_status(name, "running"),
            ):
                await monitor.start()
                assert monitor._task is not None
                assert monitor._running is True

                await monitor.stop()
                assert monitor._running is False

    async def test_last_state_property(self, monitor):
        monitor._last_state = {"vault-vllm": "up", "caddy": "down"}
        state = monitor.last_state
        assert state == {"vault-vllm": "up", "caddy": "down"}
        # Should be a copy
        state["vault-vllm"] = "down"
        assert monitor._last_state["vault-vllm"] == "up"
