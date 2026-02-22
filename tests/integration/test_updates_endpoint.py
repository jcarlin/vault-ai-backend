"""Integration tests for update mechanism endpoints (Epic 10)."""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import ApiKey, UpdateJob
from app.core.security import generate_api_key, hash_api_key, get_key_prefix


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def updates_app(app_with_db, db_engine, tmp_path):
    """App with update service wired to tmp directories."""
    from app.services.update.directory import UpdateDirectory
    from app.services.update.service import UpdateService

    session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )

    update_dir = UpdateDirectory(base_dir=str(tmp_path / "updates"))
    update_dir.init_directories()

    update_service = UpdateService(
        directory=update_dir,
        session_factory=session_factory,
    )
    app_with_db.state.update_service = update_service
    yield app_with_db


@pytest_asyncio.fixture
async def admin_client(updates_app, db_engine):
    """Admin-scoped authenticated client."""
    session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    raw_key = generate_api_key()
    async with session_factory() as session:
        session.add(
            ApiKey(
                key_hash=hash_api_key(raw_key),
                key_prefix=get_key_prefix(raw_key),
                label="updates-admin-test",
                scope="admin",
                is_active=True,
            )
        )
        await session.commit()

    transport = ASGITransport(app=updates_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def user_client(updates_app, db_engine):
    """User-scoped client (non-admin)."""
    session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    raw_key = generate_api_key()
    async with session_factory() as session:
        session.add(
            ApiKey(
                key_hash=hash_api_key(raw_key),
                key_prefix=get_key_prefix(raw_key),
                label="updates-user-test",
                scope="user",
                is_active=True,
            )
        )
        await session.commit()

    transport = ASGITransport(app=updates_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def anon_updates_client(updates_app):
    """Unauthenticated client for the updates app."""
    transport = ASGITransport(app=updates_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ── TestUpdateStatus ─────────────────────────────────────────────────────────


class TestUpdateStatus:
    @pytest.mark.asyncio
    async def test_get_status_returns_default_version(self, admin_client):
        """GET /vault/updates/status returns 200 with default version 1.0.0."""
        response = await admin_client.get("/vault/updates/status")
        assert response.status_code == 200
        data = response.json()
        assert data["current_version"] == "1.0.0"
        assert data["rollback_available"] is False
        assert data["rollback_version"] is None
        assert data["update_count"] == 0

    @pytest.mark.asyncio
    async def test_status_requires_admin(self, user_client):
        """User-scoped key gets 403 on status endpoint."""
        response = await user_client.get("/vault/updates/status")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_status_requires_auth(self, anon_updates_client):
        """Unauthenticated request gets 401."""
        response = await anon_updates_client.get("/vault/updates/status")
        assert response.status_code == 401


# ── TestScanForUpdates ───────────────────────────────────────────────────────


class TestScanForUpdates:
    @pytest.mark.asyncio
    async def test_scan_returns_empty_on_dev(self, admin_client):
        """POST /vault/updates/scan on macOS dev returns found=false (no USB)."""
        response = await admin_client.post("/vault/updates/scan")
        assert response.status_code == 200
        data = response.json()
        assert data["found"] is False
        assert data["bundles"] == []

    @pytest.mark.asyncio
    async def test_scan_requires_admin(self, user_client):
        """User-scoped key gets 403 on scan endpoint."""
        response = await user_client.post("/vault/updates/scan")
        assert response.status_code == 403


# ── TestPendingUpdate ────────────────────────────────────────────────────────


class TestPendingUpdate:
    @pytest.mark.asyncio
    async def test_pending_returns_404_when_no_scan(self, admin_client):
        """GET /vault/updates/pending returns 404 when no scan has been run."""
        response = await admin_client.get("/vault/updates/pending")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "not_found"

    @pytest.mark.asyncio
    async def test_pending_requires_admin(self, user_client):
        """User-scoped key gets 403 on pending endpoint."""
        response = await user_client.get("/vault/updates/pending")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_pending_returns_404_after_empty_scan(self, admin_client):
        """After a scan with no bundles found, pending still returns 404."""
        await admin_client.post("/vault/updates/scan")
        response = await admin_client.get("/vault/updates/pending")
        assert response.status_code == 404


# ── TestApplyUpdate ──────────────────────────────────────────────────────────


class TestApplyUpdate:
    @pytest.mark.asyncio
    async def test_apply_rejects_wrong_confirmation(self, admin_client):
        """POST /vault/updates/apply with wrong confirmation text returns 400."""
        response = await admin_client.post(
            "/vault/updates/apply",
            json={"confirmation": "wrong text"},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "confirmation_required"

    @pytest.mark.asyncio
    async def test_apply_fails_without_pending(self, admin_client):
        """POST /vault/updates/apply with correct text but no pending update returns 400."""
        response = await admin_client.post(
            "/vault/updates/apply",
            json={"confirmation": "APPLY UPDATE"},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "no_pending_update"

    @pytest.mark.asyncio
    async def test_apply_requires_admin(self, user_client):
        """User-scoped key gets 403 on apply endpoint."""
        response = await user_client.post(
            "/vault/updates/apply",
            json={"confirmation": "APPLY UPDATE"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_apply_returns_202_and_job_id(self, admin_client, updates_app):
        """With a pending compatible bundle, apply returns 202 with job_id."""
        service = updates_app.state.update_service
        service._pending_bundles = [
            {
                "version": "1.2.0",
                "path": "/tmp/fake-bundle.tar",
                "signature_valid": True,
                "compatible": True,
                "changelog": "Test update",
                "components": {"backend": True},
            }
        ]
        response = await admin_client.post(
            "/vault/updates/apply",
            json={"confirmation": "APPLY UPDATE"},
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "started"
        assert "1.2.0" in data["message"]

    @pytest.mark.asyncio
    async def test_apply_rejects_incompatible_bundle(self, admin_client, updates_app):
        """Apply with a pending but incompatible bundle returns 400."""
        service = updates_app.state.update_service
        service._pending_bundles = [
            {
                "version": "2.0.0",
                "path": "/tmp/fake-bundle.tar",
                "signature_valid": False,
                "compatible": False,
                "changelog": "Major update",
                "components": {"backend": True},
            }
        ]
        response = await admin_client.post(
            "/vault/updates/apply",
            json={"confirmation": "APPLY UPDATE"},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "update_incompatible"

    @pytest.mark.asyncio
    async def test_apply_missing_confirmation_field(self, admin_client):
        """POST /vault/updates/apply without confirmation field returns 422."""
        response = await admin_client.post(
            "/vault/updates/apply",
            json={},
        )
        assert response.status_code == 422


# ── TestProgress ─────────────────────────────────────────────────────────────


class TestProgress:
    @pytest.mark.asyncio
    async def test_progress_returns_404_for_unknown_job(self, admin_client):
        """GET /vault/updates/progress/{job_id} returns 404 for unknown job."""
        response = await admin_client.get("/vault/updates/progress/nonexistent-job")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "not_found"

    @pytest.mark.asyncio
    async def test_progress_requires_admin(self, user_client):
        """User-scoped key gets 403 on progress endpoint."""
        response = await user_client.get("/vault/updates/progress/some-job-id")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_progress_returns_job_data(self, admin_client, db_engine):
        """GET /vault/updates/progress/{job_id} returns progress for an existing job."""
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with session_factory() as session:
            job = UpdateJob(
                id="test-progress-123",
                status="completed",
                bundle_version="1.2.0",
                from_version="1.0.0",
                progress_pct=100,
                current_step="health_checking",
                steps_json=json.dumps([
                    {"name": "extract_bundle", "status": "completed"},
                    {"name": "health_checking", "status": "completed"},
                ]),
                log_json=json.dumps(["Step 1 done", "Step 2 done"]),
            )
            session.add(job)
            await session.commit()

        response = await admin_client.get("/vault/updates/progress/test-progress-123")
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "test-progress-123"
        assert data["status"] == "completed"
        assert data["bundle_version"] == "1.2.0"
        assert data["from_version"] == "1.0.0"
        assert data["progress_pct"] == 100
        assert data["current_step"] == "health_checking"
        assert len(data["steps"]) == 2
        assert len(data["log_entries"]) == 2

    @pytest.mark.asyncio
    async def test_progress_returns_pending_job(self, admin_client, db_engine):
        """Progress endpoint works for a pending job with no steps yet."""
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with session_factory() as session:
            job = UpdateJob(
                id="test-pending-456",
                status="pending",
                bundle_version="1.3.0",
                from_version="1.0.0",
                progress_pct=0,
            )
            session.add(job)
            await session.commit()

        response = await admin_client.get("/vault/updates/progress/test-pending-456")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"
        assert data["progress_pct"] == 0
        assert data["steps"] == []
        assert data["log_entries"] == []
        assert data["error"] is None


# ── TestRollback ─────────────────────────────────────────────────────────────


class TestRollback:
    @pytest.mark.asyncio
    async def test_rollback_rejects_wrong_confirmation(self, admin_client):
        """POST /vault/updates/rollback with wrong confirmation text returns 400."""
        response = await admin_client.post(
            "/vault/updates/rollback",
            json={"confirmation": "wrong text"},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "confirmation_required"

    @pytest.mark.asyncio
    async def test_rollback_fails_without_rollback_data(self, admin_client):
        """POST /vault/updates/rollback without prior update returns 400."""
        response = await admin_client.post(
            "/vault/updates/rollback",
            json={"confirmation": "ROLLBACK UPDATE"},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "no_rollback_available"

    @pytest.mark.asyncio
    async def test_rollback_requires_admin(self, user_client):
        """User-scoped key gets 403 on rollback endpoint."""
        response = await user_client.post(
            "/vault/updates/rollback",
            json={"confirmation": "ROLLBACK UPDATE"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_rollback_returns_202_with_rollback_data(
        self, admin_client, updates_app
    ):
        """With rollback data present, rollback returns 202 with job_id."""
        # Create rollback data (version.json in the rollback directory)
        directory = updates_app.state.update_service._directory
        version_file = directory.rollback / "version.json"
        version_file.write_text(json.dumps({"version": "1.0.0"}))

        response = await admin_client.post(
            "/vault/updates/rollback",
            json={"confirmation": "ROLLBACK UPDATE"},
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "rollback_started"
        assert data["rollback_to_version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_rollback_missing_confirmation_field(self, admin_client):
        """POST /vault/updates/rollback without confirmation field returns 422."""
        response = await admin_client.post(
            "/vault/updates/rollback",
            json={},
        )
        assert response.status_code == 422


# ── TestHistory ──────────────────────────────────────────────────────────────


class TestHistory:
    @pytest.mark.asyncio
    async def test_history_returns_empty_initially(self, admin_client):
        """GET /vault/updates/history returns empty list when no updates exist."""
        response = await admin_client.get("/vault/updates/history")
        assert response.status_code == 200
        data = response.json()
        assert data["updates"] == []
        assert data["total"] == 0
        assert data["offset"] == 0
        assert data["limit"] == 20

    @pytest.mark.asyncio
    async def test_history_returns_jobs(self, admin_client, db_engine):
        """GET /vault/updates/history returns existing update jobs."""
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with session_factory() as session:
            for i in range(3):
                job = UpdateJob(
                    id=f"history-job-{i}",
                    status="completed",
                    bundle_version=f"1.{i}.0",
                    from_version="1.0.0",
                    progress_pct=100,
                )
                session.add(job)
            await session.commit()

        response = await admin_client.get("/vault/updates/history")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert len(data["updates"]) == 3
        # Each job should have the expected fields
        for item in data["updates"]:
            assert "job_id" in item
            assert "status" in item
            assert "bundle_version" in item
            assert "from_version" in item

    @pytest.mark.asyncio
    async def test_history_pagination(self, admin_client, db_engine):
        """GET /vault/updates/history respects offset and limit params."""
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with session_factory() as session:
            for i in range(5):
                job = UpdateJob(
                    id=f"page-job-{i}",
                    status="completed",
                    bundle_version=f"2.{i}.0",
                    from_version="1.0.0",
                    progress_pct=100,
                )
                session.add(job)
            await session.commit()

        # First page: limit=2
        response = await admin_client.get(
            "/vault/updates/history", params={"limit": 2, "offset": 0}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["updates"]) == 2
        assert data["offset"] == 0
        assert data["limit"] == 2

        # Second page: offset=2, limit=2
        response = await admin_client.get(
            "/vault/updates/history", params={"limit": 2, "offset": 2}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["updates"]) == 2
        assert data["offset"] == 2

        # Last page: offset=4, limit=2
        response = await admin_client.get(
            "/vault/updates/history", params={"limit": 2, "offset": 4}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["updates"]) == 1

    @pytest.mark.asyncio
    async def test_history_requires_admin(self, user_client):
        """User-scoped key gets 403 on history endpoint."""
        response = await user_client.get("/vault/updates/history")
        assert response.status_code == 403


# ── TestAuthEnforcement ──────────────────────────────────────────────────────


class TestAuthEnforcement:
    ALL_ENDPOINTS = [
        ("GET", "/vault/updates/status"),
        ("POST", "/vault/updates/scan"),
        ("GET", "/vault/updates/pending"),
        ("POST", "/vault/updates/apply"),
        ("GET", "/vault/updates/progress/any-job-id"),
        ("POST", "/vault/updates/rollback"),
        ("GET", "/vault/updates/history"),
    ]

    @pytest.mark.asyncio
    async def test_all_endpoints_reject_unauthenticated(self, anon_updates_client):
        """All 7 update endpoints return 401 for unauthenticated requests."""
        for method, path in self.ALL_ENDPOINTS:
            if method == "GET":
                response = await anon_updates_client.get(path)
            else:
                response = await anon_updates_client.post(path, json={})
            assert response.status_code == 401, (
                f"{method} {path} returned {response.status_code}, expected 401"
            )
