from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import ApiKey, AuditLog
from app.core.security import generate_api_key, get_key_prefix, hash_api_key


@pytest_asyncio.fixture
async def monitoring_app(app_with_db):
    """Register system routers on the test app."""
    from app.api.v1.system import router as system_router

    app_with_db.include_router(system_router, tags=["SystemMonitoring"])
    yield app_with_db


@pytest_asyncio.fixture
async def auth_client_monitoring(monitoring_app, db_engine):
    """Authenticated client with system monitoring routes."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="monitoring-test",
            scope="admin",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=monitoring_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


class TestExpandedHealth:
    @pytest.mark.asyncio
    async def test_expanded_health(self, auth_client_monitoring):
        response = await auth_client_monitoring.get("/vault/system/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("healthy", "degraded", "unhealthy")
        assert "services" in data
        assert isinstance(data["services"], list)
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_expanded_health_has_services(self, auth_client_monitoring):
        response = await auth_client_monitoring.get("/vault/system/health")
        data = response.json()
        for svc in data["services"]:
            assert "name" in svc
            assert "status" in svc
            assert svc["status"] in ("running", "stopped", "unavailable")


class TestInferenceStats:
    @pytest.mark.asyncio
    async def test_empty_stats(self, auth_client_monitoring):
        response = await auth_client_monitoring.get("/vault/system/inference")
        assert response.status_code == 200
        data = response.json()
        assert data["requests_per_minute"] == 0.0
        assert data["avg_latency_ms"] == 0.0
        assert data["tokens_per_second"] == 0.0
        assert data["active_requests"] == 0
        assert data["window_seconds"] == 300

    @pytest.mark.asyncio
    async def test_stats_with_data(self, auth_client_monitoring, db_engine):
        session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            for i in range(5):
                session.add(
                    AuditLog(
                        action="http_request",
                        method="POST",
                        path="/v1/chat/completions",
                        latency_ms=100.0 + i * 10,
                        tokens_output=50,
                        timestamp=datetime.now(timezone.utc),
                    )
                )
            await session.commit()

        response = await auth_client_monitoring.get("/vault/system/inference")
        assert response.status_code == 200
        data = response.json()
        assert data["requests_per_minute"] > 0
        assert data["avg_latency_ms"] > 0
        assert data["tokens_per_second"] > 0


class TestServices:
    @pytest.mark.asyncio
    async def test_list_services_admin(self, auth_client_monitoring):
        response = await auth_client_monitoring.get("/vault/system/services")
        assert response.status_code == 200
        data = response.json()
        assert "services" in data
        assert isinstance(data["services"], list)
        assert len(data["services"]) > 0

    @pytest.mark.asyncio
    async def test_restart_unknown_service(self, auth_client_monitoring):
        response = await auth_client_monitoring.post(
            "/vault/system/services/unknown-service/restart"
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_restart_self(self, auth_client_monitoring):
        response = await auth_client_monitoring.post(
            "/vault/system/services/vault-api/restart"
        )
        assert response.status_code == 400


class TestSystemLogs:
    @pytest.mark.asyncio
    async def test_get_logs(self, auth_client_monitoring):
        response = await auth_client_monitoring.get("/vault/system/logs")
        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data

    @pytest.mark.asyncio
    async def test_get_logs_with_params(self, auth_client_monitoring):
        response = await auth_client_monitoring.get(
            "/vault/system/logs", params={"limit": 10, "offset": 0, "severity": "error"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 10
        assert data["offset"] == 0
