"""Integration tests for quarantine pipeline endpoints (Epic 9)."""

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import ApiKey
from app.core.security import generate_api_key, hash_api_key, get_key_prefix
from app.services.quarantine.directory import QuarantineDirectory
from app.services.quarantine.orchestrator import QuarantinePipeline
from app.services.quarantine.stages import PipelineStage, StageFinding, StageResult
from app.services.quarantine.stages.file_integrity import FileIntegrityStage
from app.services.quarantine.stages.malware_scan import MalwareScanStage
from app.services.quarantine.stages.sanitization import SanitizationStage
from tests.mocks.fake_clamav import FakeClamAVClient


FIXTURES = Path(__file__).parent.parent / "fixtures" / "quarantine"


class PassthroughStage(PipelineStage):
    @property
    def name(self):
        return "passthrough"

    async def scan(self, file_path, original_filename, config):
        return StageResult(passed=True, findings=[])


class FailingStage(PipelineStage):
    @property
    def name(self):
        return "failing"

    async def scan(self, file_path, original_filename, config):
        return StageResult(
            passed=False,
            findings=[StageFinding(
                stage="failing", severity="critical", code="test_threat",
                message="Threat detected in test",
            )],
        )


@pytest_asyncio.fixture
async def quarantine_app(app_with_db, db_engine, tmp_path):
    """App with quarantine pipeline wired to tmp directories and fake ClamAV."""
    quarantine_dir = QuarantineDirectory(base_dir=str(tmp_path / "quarantine"))
    quarantine_dir.init_directories()

    # Write a blacklist file
    bl = tmp_path / "quarantine" / "blacklist.json"
    bl.write_text(json.dumps({"hashes": []}))

    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    # Stage 1: real file integrity
    file_integrity = FileIntegrityStage()

    # Stage 2: fake ClamAV
    malware_scan = MalwareScanStage(clamav_client=FakeClamAVClient(available=True))

    # Stage 3: sanitization to tmp dir
    sanitization = SanitizationStage(sanitized_dir=quarantine_dir.sanitized)

    pipeline = QuarantinePipeline(
        directory=quarantine_dir,
        stages=[file_integrity, malware_scan, sanitization],
        session_factory=session_factory,
    )
    app_with_db.state.quarantine_pipeline = pipeline

    yield app_with_db


@pytest_asyncio.fixture
async def admin_client(quarantine_app, db_engine):
    """Admin-scoped authenticated client."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        session.add(ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="quarantine-admin-test",
            scope="admin",
            is_active=True,
        ))
        await session.commit()

    transport = ASGITransport(app=quarantine_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def user_client(quarantine_app, db_engine):
    """User-scoped authenticated client."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        session.add(ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="quarantine-user-test",
            scope="user",
            is_active=True,
        ))
        await session.commit()

    transport = ASGITransport(app=quarantine_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


class TestSubmitScan:
    @pytest.mark.asyncio
    async def test_submit_file_upload(self, admin_client):
        """POST /vault/quarantine/scan with multipart upload."""
        response = await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("test.txt", b"Hello World", "text/plain")},
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "pending"
        assert data["total_files"] == 1

    @pytest.mark.asyncio
    async def test_submit_multiple_files(self, admin_client):
        response = await admin_client.post(
            "/vault/quarantine/scan",
            files=[
                ("files", ("a.txt", b"file a", "text/plain")),
                ("files", ("b.txt", b"file b", "text/plain")),
            ],
        )
        assert response.status_code == 202
        data = response.json()
        assert data["total_files"] == 2

    @pytest.mark.asyncio
    async def test_submit_no_files_returns_error(self, admin_client):
        response = await admin_client.post("/vault/quarantine/scan")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_user_scope_can_submit(self, user_client):
        """User scope should be able to submit scans."""
        response = await user_client.post(
            "/vault/quarantine/scan",
            files={"files": ("test.txt", b"user upload", "text/plain")},
        )
        assert response.status_code == 202


class TestScanStatus:
    @pytest.mark.asyncio
    async def test_get_scan_status(self, admin_client):
        """GET /vault/quarantine/scan/{job_id} returns progress."""
        submit = await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("test.txt", b"clean content", "text/plain")},
        )
        job_id = submit.json()["job_id"]

        # Wait for scanning to complete
        await asyncio.sleep(1.5)

        response = await admin_client.get(f"/vault/quarantine/scan/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job_id
        assert data["total_files"] == 1
        assert "files" in data
        assert len(data["files"]) == 1

    @pytest.mark.asyncio
    async def test_nonexistent_job_returns_404(self, admin_client):
        response = await admin_client.get("/vault/quarantine/scan/nonexistent-id")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_clean_file_completes(self, admin_client):
        """Clean file should pass all stages and be marked clean."""
        submit = await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("readme.txt", b"This is safe content", "text/plain")},
        )
        job_id = submit.json()["job_id"]
        await asyncio.sleep(2.0)

        response = await admin_client.get(f"/vault/quarantine/scan/{job_id}")
        data = response.json()
        assert data["status"] == "completed"
        assert data["files"][0]["status"] == "clean"

    @pytest.mark.asyncio
    async def test_eicar_file_gets_held(self, admin_client):
        """EICAR test string should be detected and held."""
        eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
        submit = await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("virus.txt", eicar, "text/plain")},
        )
        job_id = submit.json()["job_id"]
        await asyncio.sleep(2.0)

        response = await admin_client.get(f"/vault/quarantine/scan/{job_id}")
        data = response.json()
        assert data["status"] == "completed"
        assert data["files_flagged"] >= 1
        file_status = data["files"][0]["status"]
        assert file_status == "held"


class TestHeldFiles:
    @pytest.mark.asyncio
    async def test_list_held_files(self, admin_client):
        """GET /vault/quarantine/held returns held files."""
        # Submit EICAR to generate a held file
        eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
        await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("malware.exe", eicar, "application/octet-stream")},
        )
        await asyncio.sleep(2.0)

        response = await admin_client.get("/vault/quarantine/held")
        assert response.status_code == 200
        data = response.json()
        assert "files" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_list_held_requires_admin(self, user_client):
        response = await user_client.get("/vault/quarantine/held")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_get_held_file_detail(self, admin_client):
        """GET /vault/quarantine/held/{id} returns single file details."""
        eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
        submit = await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("threat.bin", eicar, "application/octet-stream")},
        )
        job_id = submit.json()["job_id"]
        await asyncio.sleep(2.0)

        # Get file ID from job status
        job_status = await admin_client.get(f"/vault/quarantine/scan/{job_id}")
        files = job_status.json()["files"]
        held_files = [f for f in files if f["status"] == "held"]
        if held_files:
            file_id = held_files[0]["id"]
            response = await admin_client.get(f"/vault/quarantine/held/{file_id}")
            assert response.status_code == 200
            data = response.json()
            assert data["id"] == file_id
            assert len(data["findings"]) > 0


class TestApproveReject:
    async def _create_held_file(self, admin_client):
        """Helper to create a held file and return its ID."""
        eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
        submit = await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("threat.bin", eicar, "application/octet-stream")},
        )
        job_id = submit.json()["job_id"]
        await asyncio.sleep(2.0)

        job_status = await admin_client.get(f"/vault/quarantine/scan/{job_id}")
        files = job_status.json()["files"]
        held = [f for f in files if f["status"] == "held"]
        return held[0]["id"] if held else None

    @pytest.mark.asyncio
    async def test_approve_held_file(self, admin_client):
        """POST /vault/quarantine/held/{id}/approve with reason."""
        file_id = await self._create_held_file(admin_client)
        if file_id is None:
            pytest.skip("No held file created")

        response = await admin_client.post(
            f"/vault/quarantine/held/{file_id}/approve",
            json={"reason": "Verified as false positive by security team"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "approved"
        assert data["review_reason"] == "Verified as false positive by security team"

    @pytest.mark.asyncio
    async def test_reject_held_file(self, admin_client):
        """POST /vault/quarantine/held/{id}/reject with reason."""
        file_id = await self._create_held_file(admin_client)
        if file_id is None:
            pytest.skip("No held file created")

        response = await admin_client.post(
            f"/vault/quarantine/held/{file_id}/reject",
            json={"reason": "Confirmed malicious — deleting"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_approve_requires_admin(self, user_client):
        response = await user_client.post(
            "/vault/quarantine/held/fake-id/approve",
            json={"reason": "test"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_reject_requires_admin(self, user_client):
        response = await user_client.post(
            "/vault/quarantine/held/fake-id/reject",
            json={"reason": "test"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_approve_missing_reason(self, admin_client):
        """Approve without reason should fail validation."""
        response = await admin_client.post(
            "/vault/quarantine/held/fake-id/approve",
            json={},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_approve_nonexistent_file(self, admin_client):
        response = await admin_client.post(
            "/vault/quarantine/held/nonexistent-id/approve",
            json={"reason": "test"},
        )
        assert response.status_code == 404


class TestSignatures:
    @pytest.mark.asyncio
    async def test_get_signatures(self, admin_client):
        """GET /vault/quarantine/signatures returns signature info."""
        response = await admin_client.get("/vault/quarantine/signatures")
        assert response.status_code == 200
        data = response.json()
        assert "clamav" in data
        assert "yara" in data
        assert "blacklist" in data

    @pytest.mark.asyncio
    async def test_signatures_requires_admin(self, user_client):
        response = await user_client.get("/vault/quarantine/signatures")
        assert response.status_code == 403


class TestStats:
    @pytest.mark.asyncio
    async def test_get_stats(self, admin_client):
        """GET /vault/quarantine/stats returns aggregate statistics."""
        # Submit a file first to have data
        await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("test.txt", b"data", "text/plain")},
        )
        await asyncio.sleep(1.5)

        response = await admin_client.get("/vault/quarantine/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total_jobs" in data
        assert "total_files_scanned" in data
        assert "files_clean" in data
        assert "severity_distribution" in data
        assert data["total_jobs"] >= 1

    @pytest.mark.asyncio
    async def test_stats_requires_admin(self, user_client):
        response = await user_client.get("/vault/quarantine/stats")
        assert response.status_code == 403


class TestQuarantineConfig:
    @pytest.mark.asyncio
    async def test_get_config(self, admin_client):
        """GET /vault/admin/config/quarantine returns current config."""
        response = await admin_client.get("/vault/admin/config/quarantine")
        assert response.status_code == 200
        data = response.json()
        assert data["max_file_size"] == 1073741824
        assert data["auto_approve_clean"] is True
        assert data["strictness_level"] == "standard"

    @pytest.mark.asyncio
    async def test_update_config(self, admin_client):
        """PUT /vault/admin/config/quarantine updates settings."""
        response = await admin_client.put(
            "/vault/admin/config/quarantine",
            json={"auto_approve_clean": False, "strictness_level": "strict"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["auto_approve_clean"] is False
        assert data["strictness_level"] == "strict"

    @pytest.mark.asyncio
    async def test_config_requires_admin(self, user_client):
        response = await user_client.get("/vault/admin/config/quarantine")
        assert response.status_code == 403
        response = await user_client.put(
            "/vault/admin/config/quarantine",
            json={"auto_approve_clean": False},
        )
        assert response.status_code == 403
