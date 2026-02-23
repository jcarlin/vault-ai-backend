"""Integration tests for uptime API endpoints."""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import ApiKey, UptimeEvent
from app.core.security import generate_api_key, get_key_prefix, hash_api_key


@pytest_asyncio.fixture
async def uptime_auth_client(app_with_db, db_engine):
    """Authenticated admin client for uptime endpoints."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="uptime-test",
            scope="admin",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def uptime_user_client(app_with_db, db_engine):
    """Authenticated user-scoped client for uptime endpoints."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="uptime-user-test",
            scope="user",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def uptime_anon_client(app_with_db):
    """Unauthenticated client."""
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ── GET /vault/system/uptime ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_uptime_summary_returns_200(uptime_auth_client):
    resp = await uptime_auth_client.get("/vault/system/uptime")
    assert resp.status_code == 200
    data = resp.json()
    assert "os_uptime_seconds" in data
    assert "api_uptime_seconds" in data
    assert "services" in data
    assert "incidents_24h" in data
    assert isinstance(data["services"], list)


@pytest.mark.asyncio
async def test_uptime_summary_user_access(uptime_user_client):
    resp = await uptime_user_client.get("/vault/system/uptime")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_uptime_summary_requires_auth(uptime_anon_client):
    resp = await uptime_anon_client.get("/vault/system/uptime")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_uptime_summary_service_fields(uptime_auth_client):
    resp = await uptime_auth_client.get("/vault/system/uptime")
    data = resp.json()
    for svc in data["services"]:
        assert "service_name" in svc
        assert "availability_24h" in svc
        assert "availability_7d" in svc
        assert "availability_30d" in svc
        assert "current_status" in svc


# ── GET /vault/system/uptime/events ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_uptime_events_returns_200(uptime_auth_client):
    resp = await uptime_auth_client.get("/vault/system/uptime/events")
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert "total" in data
    assert "limit" in data
    assert "offset" in data


@pytest.mark.asyncio
async def test_uptime_events_requires_admin(uptime_user_client):
    resp = await uptime_user_client.get("/vault/system/uptime/events")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_uptime_events_requires_auth(uptime_anon_client):
    resp = await uptime_anon_client.get("/vault/system/uptime/events")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_uptime_events_with_filter(uptime_auth_client):
    resp = await uptime_auth_client.get(
        "/vault/system/uptime/events?service=vault-vllm&since_hours=24"
    )
    assert resp.status_code == 200


# ── GET /vault/system/uptime/availability ────────────────────────────────────


@pytest.mark.asyncio
async def test_availability_returns_200(uptime_auth_client):
    resp = await uptime_auth_client.get("/vault/system/uptime/availability")
    assert resp.status_code == 200
    data = resp.json()
    assert "window_hours" in data
    assert "services" in data
    assert data["window_hours"] == 24


@pytest.mark.asyncio
async def test_availability_user_access(uptime_user_client):
    resp = await uptime_user_client.get("/vault/system/uptime/availability")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_availability_requires_auth(uptime_anon_client):
    resp = await uptime_anon_client.get("/vault/system/uptime/availability")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_availability_custom_window(uptime_auth_client):
    resp = await uptime_auth_client.get("/vault/system/uptime/availability?window=168")
    assert resp.status_code == 200
    assert resp.json()["window_hours"] == 168


@pytest.mark.asyncio
async def test_availability_single_service(uptime_auth_client):
    resp = await uptime_auth_client.get(
        "/vault/system/uptime/availability?service=caddy"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "caddy" in data["services"]
    assert len(data["services"]) == 1
