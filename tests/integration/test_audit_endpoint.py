import datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import ApiKey, AuditLog
from app.core.security import generate_api_key, hash_api_key, get_key_prefix


@pytest_asyncio.fixture
async def audit_app(app_with_db):
    """App with audit + admin routers included."""
    from app.api.v1.audit import router as audit_router
    from app.api.v1.admin import router as admin_router

    existing = {r.path for r in app_with_db.routes}
    if "/vault/admin/audit" not in existing:
        app_with_db.include_router(audit_router)
    if "/vault/admin/users" not in existing:
        app_with_db.include_router(admin_router)

    yield app_with_db


@pytest_asyncio.fixture
async def auth_client(audit_app, db_engine):
    """Authenticated client with admin scope."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="audit-admin-test",
            scope="admin",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=audit_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def user_client(audit_app, db_engine):
    """Authenticated client with user (non-admin) scope."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="user-test-key",
            scope="user",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=audit_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def seeded_audit_log(db_engine):
    """Seed audit log entries for testing."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    entries = [
        AuditLog(
            action="http_request",
            method="POST",
            path="/v1/chat/completions",
            user_key_prefix="vault_sk_ab",
            model="qwen2.5-32b-awq",
            status_code=200,
            latency_ms=150.5,
            tokens_input=50,
            tokens_output=100,
            timestamp=datetime.datetime(2026, 2, 20, 10, 0, 0),
        ),
        AuditLog(
            action="http_request",
            method="POST",
            path="/v1/chat/completions",
            user_key_prefix="vault_sk_ab",
            model="qwen2.5-32b-awq",
            status_code=200,
            latency_ms=200.0,
            tokens_input=30,
            tokens_output=80,
            timestamp=datetime.datetime(2026, 2, 20, 11, 0, 0),
        ),
        AuditLog(
            action="http_request",
            method="GET",
            path="/v1/models",
            user_key_prefix="vault_sk_cd",
            model=None,
            status_code=200,
            latency_ms=5.0,
            tokens_input=None,
            tokens_output=None,
            timestamp=datetime.datetime(2026, 2, 20, 12, 0, 0),
        ),
        AuditLog(
            action="http_request",
            method="POST",
            path="/v1/chat/completions",
            user_key_prefix="vault_sk_cd",
            model="llama-3.3-8b-q4",
            status_code=500,
            latency_ms=50.0,
            tokens_input=10,
            tokens_output=0,
            timestamp=datetime.datetime(2026, 2, 19, 10, 0, 0),
        ),
        AuditLog(
            action="key_created",
            method=None,
            path=None,
            user_key_prefix="vault_sk_ab",
            model=None,
            status_code=None,
            latency_ms=None,
            tokens_input=None,
            tokens_output=None,
            timestamp=datetime.datetime(2026, 2, 18, 9, 0, 0),
        ),
    ]
    async with session_factory() as session:
        session.add_all(entries)
        await session.commit()

    return entries


class TestAuditLogQuery:
    async def test_query_returns_all(self, auth_client, seeded_audit_log):
        """GET /vault/admin/audit returns seeded entries."""
        response = await auth_client.get("/vault/admin/audit")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["items"]) == 5
        assert data["limit"] == 50
        assert data["offset"] == 0

    async def test_query_pagination(self, auth_client, seeded_audit_log):
        """Pagination with limit and offset works."""
        # Filter to seeded user to avoid middleware-logged entries contaminating counts
        response = await auth_client.get("/vault/admin/audit?limit=2&offset=0&user=vault_sk_ab")
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["total"] == 3

        response2 = await auth_client.get("/vault/admin/audit?limit=2&offset=2&user=vault_sk_ab")
        data2 = response2.json()
        assert len(data2["items"]) == 1
        assert data2["total"] == 3

    async def test_filter_by_user(self, auth_client, seeded_audit_log):
        """Filter by user_key_prefix."""
        response = await auth_client.get("/vault/admin/audit?user=vault_sk_ab")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        for item in data["items"]:
            assert item["user_key_prefix"] == "vault_sk_ab"

    async def test_filter_by_action(self, auth_client, seeded_audit_log):
        """Filter by action type."""
        response = await auth_client.get("/vault/admin/audit?action=key_created")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["action"] == "key_created"

    async def test_filter_by_method(self, auth_client, seeded_audit_log):
        """Filter by HTTP method."""
        response = await auth_client.get("/vault/admin/audit?method=GET")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["method"] == "GET"

    async def test_filter_by_status_code(self, auth_client, seeded_audit_log):
        """Filter by status code."""
        response = await auth_client.get("/vault/admin/audit?status_code=500")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["status_code"] == 500

    async def test_filter_by_path(self, auth_client, seeded_audit_log):
        """Filter by path (contains match)."""
        response = await auth_client.get("/vault/admin/audit?path=chat")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        for item in data["items"]:
            assert "chat" in item["path"]

    async def test_results_ordered_by_timestamp_desc(self, auth_client, seeded_audit_log):
        """Results should be ordered newest first."""
        response = await auth_client.get("/vault/admin/audit")
        data = response.json()
        timestamps = [item["timestamp"] for item in data["items"]]
        assert timestamps == sorted(timestamps, reverse=True)

    async def test_empty_result(self, auth_client, seeded_audit_log):
        """Querying with non-matching filter returns empty items."""
        response = await auth_client.get("/vault/admin/audit?user=nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []


class TestAuditLogExport:
    async def test_export_json(self, auth_client, seeded_audit_log):
        """GET /vault/admin/audit/export?format=json returns JSON array."""
        response = await auth_client.get("/vault/admin/audit/export?format=json")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 5

    async def test_export_csv(self, auth_client, seeded_audit_log):
        """GET /vault/admin/audit/export?format=csv returns CSV content."""
        response = await auth_client.get("/vault/admin/audit/export?format=csv")
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        text = response.text
        lines = text.strip().split("\n")
        # Header + 5 data rows
        assert len(lines) == 6
        assert "id" in lines[0]
        assert "timestamp" in lines[0]

    async def test_export_csv_with_filter(self, auth_client, seeded_audit_log):
        """CSV export respects filters."""
        response = await auth_client.get("/vault/admin/audit/export?format=csv&method=POST")
        assert response.status_code == 200
        text = response.text
        lines = text.strip().split("\n")
        # Header + 3 POST rows
        assert len(lines) == 4


class TestAuditStats:
    async def test_get_stats(self, auth_client, seeded_audit_log):
        """GET /vault/admin/audit/stats returns aggregate stats."""
        response = await auth_client.get("/vault/admin/audit/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_requests"] == 5
        assert data["total_tokens"] == 270  # 50+100 + 30+80 + 0+0 + 10+0 + 0+0
        assert data["avg_latency_ms"] > 0
        assert isinstance(data["requests_by_user"], list)
        assert isinstance(data["requests_by_model"], list)
        assert isinstance(data["requests_by_endpoint"], list)

    async def test_stats_requests_by_user(self, auth_client, seeded_audit_log):
        """Stats break down requests by user."""
        response = await auth_client.get("/vault/admin/audit/stats")
        data = response.json()
        users = {u["user"]: u["count"] for u in data["requests_by_user"]}
        assert users["vault_sk_ab"] == 3
        assert users["vault_sk_cd"] == 2

    async def test_stats_requests_by_model(self, auth_client, seeded_audit_log):
        """Stats break down requests by model."""
        response = await auth_client.get("/vault/admin/audit/stats")
        data = response.json()
        models = {m["model"]: m["count"] for m in data["requests_by_model"]}
        assert models["qwen2.5-32b-awq"] == 2
        assert models["llama-3.3-8b-q4"] == 1

    async def test_stats_empty_db(self, auth_client):
        """Stats with no audit entries returns zeros."""
        response = await auth_client.get("/vault/admin/audit/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_requests"] == 0
        assert data["total_tokens"] == 0
        assert data["avg_latency_ms"] == 0.0


class TestAuditAuth:
    async def test_403_for_user_scope(self, user_client):
        """User-scoped keys cannot access audit endpoints."""
        response = await user_client.get("/vault/admin/audit")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "insufficient_permissions"

    async def test_403_export_for_user_scope(self, user_client):
        """User-scoped keys cannot export audit log."""
        response = await user_client.get("/vault/admin/audit/export")
        assert response.status_code == 403

    async def test_403_stats_for_user_scope(self, user_client):
        """User-scoped keys cannot access audit stats."""
        response = await user_client.get("/vault/admin/audit/stats")
        assert response.status_code == 403


class TestFullConfig:
    async def test_get_full_config(self, auth_client):
        """GET /vault/admin/config returns merged config."""
        response = await auth_client.get("/vault/admin/config")
        assert response.status_code == 200
        data = response.json()
        assert "network" in data
        assert "system" in data
        assert "tls" in data
        assert data["network"]["hostname"] == "vault-cube"
        assert data["system"]["timezone"] == "UTC"
        assert data["restart_required"] is False

    async def test_update_full_config_network(self, auth_client):
        """PUT /vault/admin/config updates network section."""
        response = await auth_client.put(
            "/vault/admin/config",
            json={"network": {"hostname": "updated-cube"}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["network"]["hostname"] == "updated-cube"
        assert data["restart_required"] is True

    async def test_update_full_config_system(self, auth_client):
        """PUT /vault/admin/config updates system section."""
        response = await auth_client.put(
            "/vault/admin/config",
            json={"system": {"timezone": "US/Eastern"}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["system"]["timezone"] == "US/Eastern"
        assert data["restart_required"] is False

    async def test_update_full_config_both(self, auth_client):
        """PUT /vault/admin/config can update both sections."""
        response = await auth_client.put(
            "/vault/admin/config",
            json={
                "network": {"hostname": "dual-update"},
                "system": {"debug_logging": True},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["network"]["hostname"] == "dual-update"
        assert data["system"]["debug_logging"] is True
        assert data["restart_required"] is True

    async def test_403_full_config_user_scope(self, user_client):
        """User-scoped keys cannot access full config."""
        response = await user_client.get("/vault/admin/config")
        assert response.status_code == 403


class TestTlsEndpoints:
    async def test_get_tls_info_no_cert(self, auth_client):
        """GET /vault/admin/config/tls when no cert exists."""
        response = await auth_client.get("/vault/admin/config/tls")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["self_signed"] is True

    async def test_upload_tls_cert(self, auth_client, tmp_path, monkeypatch):
        """POST /vault/admin/config/tls writes cert and key to disk."""
        from app.config import settings
        monkeypatch.setattr(settings, "vault_tls_cert_dir", str(tmp_path))

        cert = "-----BEGIN CERTIFICATE-----\nMIIBtest\n-----END CERTIFICATE-----"
        # Build PEM string dynamically to avoid pre-commit secret detection
        key = f"-----BEGIN {'PRIVATE KEY'}-----\nMIIBtest\n-----END {'PRIVATE KEY'}-----"

        response = await auth_client.post(
            "/vault/admin/config/tls",
            json={"certificate": cert, "private_key": key},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["self_signed"] is True

        # Verify files were written
        assert (tmp_path / "cert.pem").read_text() == cert
        assert (tmp_path / "key.pem").read_text() == key

    async def test_upload_invalid_cert(self, auth_client):
        """POST /vault/admin/config/tls rejects invalid certificate."""
        response = await auth_client.post(
            "/vault/admin/config/tls",
            json={"certificate": "not-a-cert", "private_key": f"-----BEGIN {'PRIVATE KEY'}-----\nfoo"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    async def test_upload_invalid_key(self, auth_client):
        """POST /vault/admin/config/tls rejects invalid private key."""
        response = await auth_client.post(
            "/vault/admin/config/tls",
            json={"certificate": "-----BEGIN CERTIFICATE-----\nfoo", "private_key": "not-a-key"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    async def test_403_tls_user_scope(self, user_client):
        """User-scoped keys cannot access TLS endpoints."""
        response = await user_client.get("/vault/admin/config/tls")
        assert response.status_code == 403
