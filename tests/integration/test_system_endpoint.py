import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import ApiKey
from app.core.security import generate_api_key, get_key_prefix, hash_api_key


@pytest_asyncio.fixture
async def system_app(app_with_db):
    """Register system routers on the test app."""
    from app.api.v1.system import router as system_router

    app_with_db.include_router(system_router, tags=["System"])
    yield app_with_db


@pytest_asyncio.fixture
async def auth_client_system(system_app, db_engine):
    """Authenticated client with system routes registered."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="system-test",
            scope="admin",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=system_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def anon_client_system(system_app):
    """Unauthenticated client with system routes registered."""
    transport = ASGITransport(app=system_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_system_resources_returns_valid_data(auth_client_system):
    resp = await auth_client_system.get("/vault/system/resources")
    assert resp.status_code == 200
    data = resp.json()
    assert "cpu_usage_pct" in data
    assert "cpu_count" in data
    assert data["cpu_count"] > 0
    assert "ram_total_mb" in data
    assert data["ram_total_mb"] > 0
    assert "ram_used_mb" in data
    assert "ram_usage_pct" in data
    assert "disk_total_gb" in data
    assert data["disk_total_gb"] > 0
    assert "disk_used_gb" in data
    assert "disk_usage_pct" in data
    assert "network_in_bytes" in data
    assert "network_out_bytes" in data
    assert "temperature_celsius" in data


@pytest.mark.asyncio
async def test_system_resources_requires_auth(anon_client_system):
    resp = await anon_client_system.get("/vault/system/resources")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_system_gpu_returns_list(auth_client_system):
    resp = await auth_client_system.get("/vault/system/gpu")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_system_gpu_requires_auth(anon_client_system):
    resp = await anon_client_system.get("/vault/system/gpu")
    assert resp.status_code == 401
