"""Integration tests for AI Safety Stage (Stage 4) through quarantine API."""

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import ApiKey, SystemConfig
from app.core.security import generate_api_key, hash_api_key, get_key_prefix
from app.services.quarantine.directory import QuarantineDirectory
from app.services.quarantine.orchestrator import QuarantinePipeline
from app.services.quarantine.stages.file_integrity import FileIntegrityStage
from app.services.quarantine.stages.malware_scan import MalwareScanStage
from app.services.quarantine.stages.sanitization import SanitizationStage
from app.services.quarantine.stages.ai_safety import AISafetyStage
from tests.mocks.fake_clamav import FakeClamAVClient


FIXTURES = Path(__file__).parent.parent / "fixtures" / "quarantine"


@pytest_asyncio.fixture
async def ai_safety_app(app_with_db, db_engine, tmp_path):
    """App with quarantine pipeline wired including Stage 4 (AI Safety)."""
    quarantine_dir = QuarantineDirectory(base_dir=str(tmp_path / "quarantine"))
    quarantine_dir.init_directories()

    bl = tmp_path / "quarantine" / "blacklist.json"
    bl.write_text(json.dumps({"hashes": []}))

    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    file_integrity = FileIntegrityStage()
    malware_scan = MalwareScanStage(clamav_client=FakeClamAVClient(available=True))
    sanitization = SanitizationStage(sanitized_dir=quarantine_dir.sanitized)
    ai_safety = AISafetyStage()

    pipeline = QuarantinePipeline(
        directory=quarantine_dir,
        stages=[file_integrity, malware_scan, sanitization, ai_safety],
        session_factory=session_factory,
    )
    app_with_db.state.quarantine_pipeline = pipeline

    yield app_with_db


@pytest_asyncio.fixture
async def admin_client(ai_safety_app, db_engine):
    """Admin-scoped authenticated client for AI safety tests."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        session.add(ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="ai-safety-admin-test",
            scope="admin",
            is_active=True,
        ))
        await session.commit()

    transport = ASGITransport(app=ai_safety_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


async def _poll_job(client, job_id, max_wait=10):
    """Poll scan job until completed or timeout."""
    for _ in range(max_wait * 10):
        resp = await client.get(f"/vault/quarantine/scan/{job_id}")
        data = resp.json()
        if data["status"] == "completed":
            return data
        await asyncio.sleep(0.1)
    return data


class TestAISafetyPipeline:
    """Test Stage 4 through the full quarantine pipeline."""

    @pytest.mark.asyncio
    async def test_clean_jsonl_passes_pipeline(self, admin_client):
        """Clean JSONL passes all 4 stages."""
        content = (FIXTURES / "training_chat.jsonl").read_bytes()
        resp = await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("train.jsonl", content, "application/x-ndjson")},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status = await _poll_job(admin_client, job_id)
        assert status["status"] == "completed"
        for f in status["files"]:
            assert f["status"] in ("clean", "held")

    @pytest.mark.asyncio
    async def test_jsonl_with_pii_held(self, admin_client):
        """JSONL with PII gets held with ai_safety findings."""
        content = (FIXTURES / "training_with_pii.jsonl").read_bytes()
        resp = await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("pii_data.jsonl", content, "application/x-ndjson")},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status = await _poll_job(admin_client, job_id)
        assert status["status"] == "completed"
        file_status = status["files"][0]
        pii_findings = [f for f in file_status["findings"] if f["stage"] == "ai_safety" and f["code"].startswith("pii_")]
        assert len(pii_findings) > 0

    @pytest.mark.asyncio
    async def test_jsonl_with_injections_flagged(self, admin_client):
        """JSONL with prompt injections gets flagged."""
        content = (FIXTURES / "training_with_injections.jsonl").read_bytes()
        resp = await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("injected.jsonl", content, "application/x-ndjson")},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status = await _poll_job(admin_client, job_id)
        assert status["status"] == "completed"
        file_status = status["files"][0]
        injection_findings = [f for f in file_status["findings"] if f["stage"] == "ai_safety" and f["code"].startswith("injection_")]
        assert len(injection_findings) > 0

    @pytest.mark.asyncio
    async def test_pickle_model_blocked(self, admin_client):
        """Pickle model files should be blocked (critical finding)."""
        resp = await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("model.pkl", b"fake pickle content", "application/octet-stream")},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status = await _poll_job(admin_client, job_id)
        assert status["status"] == "completed"
        file_status = status["files"][0]
        assert file_status["status"] == "held"
        model_findings = [f for f in file_status["findings"] if f["code"] == "model_dangerous_format"]
        assert len(model_findings) > 0

    @pytest.mark.asyncio
    async def test_ai_safety_disabled_skips_stage4(self, admin_client, db_engine):
        """When ai_safety_enabled=false, Stage 4 checks are skipped."""
        session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            session.add(SystemConfig(key="quarantine.ai_safety_enabled", value="false"))
            await session.commit()

        content = (FIXTURES / "training_with_pii.jsonl").read_bytes()
        resp = await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("pii_data.jsonl", content, "application/x-ndjson")},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status = await _poll_job(admin_client, job_id)
        file_status = status["files"][0]
        ai_findings = [f for f in file_status["findings"] if f["stage"] == "ai_safety"]
        assert len(ai_findings) == 0

    @pytest.mark.asyncio
    async def test_pii_block_mode(self, admin_client, db_engine):
        """PII action=block rejects files with PII."""
        session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            session.add(SystemConfig(key="quarantine.pii_action", value="block"))
            await session.commit()

        content = (FIXTURES / "training_with_pii.jsonl").read_bytes()
        resp = await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("pii_block.jsonl", content, "application/x-ndjson")},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status = await _poll_job(admin_client, job_id)
        file_status = status["files"][0]
        assert file_status["status"] == "held"

    @pytest.mark.asyncio
    async def test_config_get_includes_ai_fields(self, admin_client):
        """GET config returns AI safety fields."""
        resp = await admin_client.get("/vault/admin/config/quarantine")
        assert resp.status_code == 200
        data = resp.json()
        assert "ai_safety_enabled" in data
        assert "pii_enabled" in data
        assert "pii_action" in data
        assert "injection_detection_enabled" in data
        assert "model_hash_verification" in data

    @pytest.mark.asyncio
    async def test_config_update_ai_fields(self, admin_client):
        """PUT config updates AI safety fields."""
        resp = await admin_client.put(
            "/vault/admin/config/quarantine",
            json={"pii_action": "redact", "ai_safety_enabled": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pii_action"] == "redact"
        assert data["ai_safety_enabled"] is False

    @pytest.mark.asyncio
    async def test_plain_text_pii_scanning(self, admin_client):
        """Plain text files also get PII scanning."""
        content = b"Patient SSN: 123-45-6789\nEmail: test@example.com\n"
        resp = await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("notes.txt", content, "text/plain")},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status = await _poll_job(admin_client, job_id)
        file_status = status["files"][0]
        pii_findings = [f for f in file_status["findings"] if f["stage"] == "ai_safety" and f["code"].startswith("pii_")]
        assert len(pii_findings) > 0

    @pytest.mark.asyncio
    async def test_backdoor_poisoned_data_flagged(self, admin_client):
        """Poisoned training data should be flagged."""
        content = (FIXTURES / "training_poisoned.jsonl").read_bytes()
        resp = await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("poisoned.jsonl", content, "application/x-ndjson")},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status = await _poll_job(admin_client, job_id)
        file_status = status["files"][0]
        poisoning_findings = [f for f in file_status["findings"] if f["code"].startswith("poisoning_")]
        assert len(poisoning_findings) > 0

    @pytest.mark.asyncio
    async def test_duplicate_training_data_flagged(self, admin_client):
        """High duplicate rate in training data should be flagged."""
        content = (FIXTURES / "training_duplicates.jsonl").read_bytes()
        resp = await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("dupes.jsonl", content, "application/x-ndjson")},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status = await _poll_job(admin_client, job_id)
        file_status = status["files"][0]
        dupe_findings = [f for f in file_status["findings"] if f["code"] == "training_high_duplicate_rate"]
        assert len(dupe_findings) > 0

    @pytest.mark.asyncio
    async def test_held_file_shows_ai_safety_findings(self, admin_client):
        """Held file detail includes AI safety findings."""
        content = (FIXTURES / "training_with_pii.jsonl").read_bytes()
        resp = await admin_client.post(
            "/vault/quarantine/scan",
            files={"files": ("pii_detail.jsonl", content, "application/x-ndjson")},
        )
        job_id = resp.json()["job_id"]
        status = await _poll_job(admin_client, job_id)

        # Get held files
        held_resp = await admin_client.get("/vault/quarantine/held")
        if held_resp.status_code == 200 and held_resp.json()["total"] > 0:
            file_id = held_resp.json()["files"][0]["id"]
            detail_resp = await admin_client.get(f"/vault/quarantine/held/{file_id}")
            assert detail_resp.status_code == 200
            findings = detail_resp.json()["findings"]
            ai_findings = [f for f in findings if f["stage"] == "ai_safety"]
            assert len(ai_findings) > 0
