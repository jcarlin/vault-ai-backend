"""Unit tests for the quarantine pipeline orchestrator."""

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, QuarantineFile, QuarantineJob
from app.services.quarantine.directory import QuarantineDirectory
from app.services.quarantine.orchestrator import QuarantinePipeline
from app.services.quarantine.stages import PipelineStage, StageFinding, StageResult


class PassthroughStage(PipelineStage):
    """Test stage that always passes."""

    @property
    def name(self) -> str:
        return "passthrough"

    async def scan(self, file_path, original_filename, config):
        return StageResult(passed=True, findings=[])


class FailingStage(PipelineStage):
    """Test stage that always fails with a finding."""

    @property
    def name(self) -> str:
        return "failing"

    async def scan(self, file_path, original_filename, config):
        return StageResult(
            passed=False,
            findings=[StageFinding(
                stage="failing",
                severity="critical",
                code="test_failure",
                message="Intentional test failure",
            )],
        )


@pytest_asyncio.fixture
async def pipeline_db():
    """In-memory DB for orchestrator tests."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


@pytest.fixture
def quarantine_dir(tmp_path):
    d = QuarantineDirectory(base_dir=str(tmp_path / "quarantine"))
    d.init_directories()
    return d


class TestSubmitScan:
    @pytest.mark.asyncio
    async def test_submit_creates_job(self, pipeline_db, quarantine_dir):
        pipeline = QuarantinePipeline(
            directory=quarantine_dir,
            stages=[PassthroughStage()],
            session_factory=pipeline_db,
        )
        job_id = await pipeline.submit_scan(
            [("test.txt", b"hello world")],
            source_type="upload",
            submitted_by="vault_sk_test",
        )
        assert job_id

        # Small delay for background task
        await asyncio.sleep(0.5)

        status = await pipeline.get_job_status(job_id)
        assert status["total_files"] == 1
        assert status["source_type"] == "upload"
        assert status["submitted_by"] == "vault_sk_test"

    @pytest.mark.asyncio
    async def test_submit_multiple_files(self, pipeline_db, quarantine_dir):
        pipeline = QuarantinePipeline(
            directory=quarantine_dir,
            stages=[PassthroughStage()],
            session_factory=pipeline_db,
        )
        files = [("a.txt", b"file a"), ("b.txt", b"file b"), ("c.txt", b"file c")]
        job_id = await pipeline.submit_scan(files)
        await asyncio.sleep(0.5)

        status = await pipeline.get_job_status(job_id)
        assert status["total_files"] == 3

    @pytest.mark.asyncio
    async def test_batch_too_large(self, pipeline_db, quarantine_dir):
        pipeline = QuarantinePipeline(
            directory=quarantine_dir,
            stages=[PassthroughStage()],
            session_factory=pipeline_db,
        )
        # Default max_batch_files is 100
        files = [(f"file_{i}.txt", b"data") for i in range(101)]
        from app.core.exceptions import VaultError
        with pytest.raises(VaultError, match="batch_too_large|Batch exceeds"):
            await pipeline.submit_scan(files)


class TestPipelineExecution:
    @pytest.mark.asyncio
    async def test_all_stages_pass(self, pipeline_db, quarantine_dir):
        pipeline = QuarantinePipeline(
            directory=quarantine_dir,
            stages=[PassthroughStage()],
            session_factory=pipeline_db,
        )
        job_id = await pipeline.submit_scan([("clean.txt", b"clean content")])
        await asyncio.sleep(1.0)

        status = await pipeline.get_job_status(job_id)
        assert status["status"] == "completed"
        assert status["files_clean"] == 1
        assert status["files_flagged"] == 0
        assert len(status["files"]) == 1
        assert status["files"][0]["status"] == "clean"

    @pytest.mark.asyncio
    async def test_stage_failure_holds_file(self, pipeline_db, quarantine_dir):
        pipeline = QuarantinePipeline(
            directory=quarantine_dir,
            stages=[FailingStage()],
            session_factory=pipeline_db,
        )
        job_id = await pipeline.submit_scan([("bad.txt", b"bad content")])
        await asyncio.sleep(1.0)

        status = await pipeline.get_job_status(job_id)
        assert status["status"] == "completed"
        assert status["files_flagged"] == 1
        assert status["files"][0]["status"] == "held"
        assert len(status["files"][0]["findings"]) > 0

    @pytest.mark.asyncio
    async def test_mixed_results(self, pipeline_db, quarantine_dir):
        """Pipeline with passthrough: all files should be clean."""
        pipeline = QuarantinePipeline(
            directory=quarantine_dir,
            stages=[PassthroughStage()],
            session_factory=pipeline_db,
        )
        files = [("good.txt", b"good"), ("also_good.txt", b"also good")]
        job_id = await pipeline.submit_scan(files)
        await asyncio.sleep(1.0)

        status = await pipeline.get_job_status(job_id)
        assert status["files_clean"] == 2


class TestHeldFileWorkflow:
    @pytest.mark.asyncio
    async def test_approve_held_file(self, pipeline_db, quarantine_dir):
        pipeline = QuarantinePipeline(
            directory=quarantine_dir,
            stages=[FailingStage()],
            session_factory=pipeline_db,
        )
        job_id = await pipeline.submit_scan([("flagged.txt", b"flagged")])
        await asyncio.sleep(1.0)

        status = await pipeline.get_job_status(job_id)
        file_id = status["files"][0]["id"]
        assert status["files"][0]["status"] == "held"

        # Approve
        result = await pipeline.approve_file(file_id, reason="False positive", reviewed_by="admin")
        assert result["status"] == "approved"
        assert result["review_reason"] == "False positive"
        assert result["reviewed_by"] == "admin"

    @pytest.mark.asyncio
    async def test_reject_held_file(self, pipeline_db, quarantine_dir):
        pipeline = QuarantinePipeline(
            directory=quarantine_dir,
            stages=[FailingStage()],
            session_factory=pipeline_db,
        )
        job_id = await pipeline.submit_scan([("malware.bin", b"evil")])
        await asyncio.sleep(1.0)

        status = await pipeline.get_job_status(job_id)
        file_id = status["files"][0]["id"]

        result = await pipeline.reject_file(file_id, reason="Confirmed malicious", reviewed_by="admin")
        assert result["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_approve_non_held_file_fails(self, pipeline_db, quarantine_dir):
        pipeline = QuarantinePipeline(
            directory=quarantine_dir,
            stages=[PassthroughStage()],
            session_factory=pipeline_db,
        )
        job_id = await pipeline.submit_scan([("clean.txt", b"clean")])
        await asyncio.sleep(1.0)

        status = await pipeline.get_job_status(job_id)
        file_id = status["files"][0]["id"]
        assert status["files"][0]["status"] == "clean"

        from app.core.exceptions import VaultError
        with pytest.raises(VaultError, match="invalid_status|not 'held'"):
            await pipeline.approve_file(file_id, reason="Test")

    @pytest.mark.asyncio
    async def test_list_held_files(self, pipeline_db, quarantine_dir):
        pipeline = QuarantinePipeline(
            directory=quarantine_dir,
            stages=[FailingStage()],
            session_factory=pipeline_db,
        )
        await pipeline.submit_scan([("a.txt", b"a"), ("b.txt", b"b")])
        await asyncio.sleep(1.0)

        held = await pipeline.list_held_files()
        assert held["total"] == 2
        assert len(held["files"]) == 2


class TestStats:
    @pytest.mark.asyncio
    async def test_stats_after_scans(self, pipeline_db, quarantine_dir):
        pipeline = QuarantinePipeline(
            directory=quarantine_dir,
            stages=[PassthroughStage()],
            session_factory=pipeline_db,
        )
        await pipeline.submit_scan([("a.txt", b"a")])
        await asyncio.sleep(1.0)

        stats = await pipeline.get_stats()
        assert stats["total_jobs"] == 1
        assert stats["total_files_scanned"] == 1
        assert stats["files_clean"] == 1


class TestConfig:
    @pytest.mark.asyncio
    async def test_get_default_config(self, pipeline_db, quarantine_dir):
        pipeline = QuarantinePipeline(
            directory=quarantine_dir,
            session_factory=pipeline_db,
        )
        config = await pipeline.get_config()
        assert config["max_file_size"] == 1073741824
        assert config["auto_approve_clean"] is True
        assert config["strictness_level"] == "standard"

    @pytest.mark.asyncio
    async def test_update_config(self, pipeline_db, quarantine_dir):
        pipeline = QuarantinePipeline(
            directory=quarantine_dir,
            session_factory=pipeline_db,
        )
        updated = await pipeline.update_config({"auto_approve_clean": False, "strictness_level": "strict"})
        assert updated["auto_approve_clean"] is False
        assert updated["strictness_level"] == "strict"

        # Verify persistence
        config = await pipeline.get_config()
        assert config["auto_approve_clean"] is False
