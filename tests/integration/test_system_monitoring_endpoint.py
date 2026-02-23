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
            "/vault/system/services/vault-backend/restart"
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
        # Mock logs should return entries on non-Linux (dev)
        assert len(data["entries"]) > 0
        assert data["total"] > 0

    @pytest.mark.asyncio
    async def test_get_logs_entry_format(self, auth_client_monitoring):
        """Verify log entries have correct data types matching frontend expectations."""
        response = await auth_client_monitoring.get("/vault/system/logs")
        data = response.json()
        entry = data["entries"][0]
        # Timestamp must be ISO 8601 (parseable, ends with Z)
        assert entry["timestamp"].endswith("Z")
        datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
        # Severity must be a string name, not a number
        assert entry["severity"] in ("critical", "error", "warning", "info", "debug")
        # Service must not have .service suffix
        assert not entry["service"].endswith(".service")
        assert isinstance(entry["message"], str)

    @pytest.mark.asyncio
    async def test_get_logs_with_params(self, auth_client_monitoring):
        response = await auth_client_monitoring.get(
            "/vault/system/logs", params={"limit": 10, "offset": 0, "severity": "error"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 10
        assert data["offset"] == 0
        # All entries should be error severity when filtered
        for entry in data["entries"]:
            assert entry["severity"] == "error"

    @pytest.mark.asyncio
    async def test_get_logs_service_filter(self, auth_client_monitoring):
        """Verify service filter maps friendly names to correct results."""
        response = await auth_client_monitoring.get(
            "/vault/system/logs", params={"service": "vllm", "limit": 50}
        )
        assert response.status_code == 200
        data = response.json()
        for entry in data["entries"]:
            assert entry["service"] == "vault-vllm"

    @pytest.mark.asyncio
    async def test_get_logs_pagination(self, auth_client_monitoring):
        """Verify pagination returns different entries."""
        resp1 = await auth_client_monitoring.get(
            "/vault/system/logs", params={"limit": 5, "offset": 0}
        )
        resp2 = await auth_client_monitoring.get(
            "/vault/system/logs", params={"limit": 5, "offset": 5}
        )
        data1 = resp1.json()
        data2 = resp2.json()
        assert len(data1["entries"]) == 5
        assert len(data2["entries"]) == 5
        # Pages should not overlap
        ts1 = {e["timestamp"] for e in data1["entries"]}
        ts2 = {e["timestamp"] for e in data2["entries"]}
        assert ts1.isdisjoint(ts2)
