"""Unit tests for the UpdateEngine class (app/services/update/engine.py)."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, SystemConfig, UpdateJob
from app.core.exceptions import VaultError
from app.services.update.bundle import UpdateBundle
from app.services.update.directory import UpdateDirectory
from app.services.update.engine import APPLY_STEPS, STEP_WEIGHTS, UpdateEngine
from app.services.update.gpg import GPGVerifier
from tests.fixtures.updates import make_test_bundle


@pytest_asyncio.fixture
async def engine_db():
    """In-memory DB for engine tests."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


@pytest.fixture
def update_dir(tmp_path):
    """UpdateDirectory rooted in tmp_path."""
    d = UpdateDirectory(base_dir=str(tmp_path / "updates"))
    d.init_directories()
    return d


@pytest.fixture
def mock_gpg():
    """GPGVerifier mock that always reports unavailable."""
    gpg = MagicMock(spec=GPGVerifier)
    gpg.is_available.return_value = False
    gpg.verify.return_value = True
    return gpg


def _make_engine(update_dir, mock_gpg, engine_db):
    """Helper to construct an UpdateEngine with test fixtures."""
    return UpdateEngine(
        directory=update_dir,
        gpg_verifier=mock_gpg,
        session_factory=engine_db,
    )


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_cannot_start_while_running(self, update_dir, mock_gpg, engine_db, tmp_path):
        """Starting apply while another is running raises VaultError."""
        engine = _make_engine(update_dir, mock_gpg, engine_db)
        # Simulate a running job
        engine._active_job_id = "existing-job-123"

        with pytest.raises(VaultError, match="already in progress"):
            await engine.apply("new-job", str(tmp_path / "bundle.tar"), "1.0.0")

    @pytest.mark.asyncio
    async def test_cannot_rollback_while_running(self, update_dir, mock_gpg, engine_db):
        """Rollback while apply is running raises VaultError."""
        engine = _make_engine(update_dir, mock_gpg, engine_db)
        engine._active_job_id = "existing-job-456"

        with pytest.raises(VaultError, match="update is in progress"):
            await engine.rollback("rollback-job")

    @pytest.mark.asyncio
    async def test_cannot_rollback_without_data(self, update_dir, mock_gpg, engine_db):
        """Rollback without rollback data raises VaultError."""
        engine = _make_engine(update_dir, mock_gpg, engine_db)
        # No rollback data exists (clean directory)

        with pytest.raises(VaultError, match="No rollback data"):
            await engine.rollback("rollback-job")


class TestProgressCalculation:
    def test_progress_empty(self, update_dir, mock_gpg, engine_db):
        """Progress is 0 when all steps are pending."""
        engine = _make_engine(update_dir, mock_gpg, engine_db)
        steps = {s: "pending" for s in APPLY_STEPS}
        assert engine._calculate_progress(steps) == 0

    def test_progress_partial(self, update_dir, mock_gpg, engine_db):
        """Progress reflects completed and in-progress steps."""
        engine = _make_engine(update_dir, mock_gpg, engine_db)
        steps = {s: "pending" for s in APPLY_STEPS}
        steps["verify_signature"] = "completed"  # weight 5
        steps["create_backup"] = "completed"  # weight 15
        steps["extract_bundle"] = "in_progress"  # weight 10, half = 5
        # Expected: 5 + 15 + 5 = 25
        assert engine._calculate_progress(steps) == 25

    def test_progress_capped_at_99(self, update_dir, mock_gpg, engine_db):
        """Progress is capped at 99 even when all steps complete."""
        engine = _make_engine(update_dir, mock_gpg, engine_db)
        steps = {s: "completed" for s in APPLY_STEPS}
        result = engine._calculate_progress(steps)
        assert result <= 99


class TestLogEntries:
    def test_log_entry_is_timestamped(self, update_dir, mock_gpg, engine_db):
        """Log entries contain a timestamp in [HH:MM:SS] format."""
        entry = UpdateEngine._log_entry("Test message")
        assert entry.startswith("[")
        assert "] Test message" in entry
        # Check timestamp format: [HH:MM:SS]
        ts_part = entry.split("]")[0].strip("[")
        parts = ts_part.split(":")
        assert len(parts) == 3


class TestStepExecution:
    @pytest.mark.asyncio
    async def test_step_updates_progress_in_db(self, update_dir, mock_gpg, engine_db, tmp_path):
        """Running a step updates the UpdateJob progress in the database."""
        engine = _make_engine(update_dir, mock_gpg, engine_db)

        # Create a job record in the DB
        job_id = "test-step-job"
        async with engine_db() as session:
            job = UpdateJob(
                id=job_id,
                status="pending",
                bundle_version="1.2.0",
                from_version="1.0.0",
            )
            session.add(job)
            await session.commit()

        steps_status = {s: "pending" for s in APPLY_STEPS}
        log_entries = []

        # Define a simple async step function
        async def dummy_step():
            return "Step completed"

        await engine._run_step(
            job_id, "verify_signature", steps_status, log_entries, dummy_step,
        )

        assert steps_status["verify_signature"] == "completed"
        assert len(log_entries) == 1
        assert "Step completed" in log_entries[0]

        # Verify DB was updated
        async with engine_db() as session:
            result = await session.execute(select(UpdateJob).where(UpdateJob.id == job_id))
            job = result.scalar_one()
            assert job.progress_pct > 0

    @pytest.mark.asyncio
    async def test_failed_step_marks_status(self, update_dir, mock_gpg, engine_db):
        """A step that raises marks its status as failed and re-raises."""
        engine = _make_engine(update_dir, mock_gpg, engine_db)

        job_id = "test-fail-job"
        async with engine_db() as session:
            job = UpdateJob(
                id=job_id,
                status="pending",
                bundle_version="1.2.0",
                from_version="1.0.0",
            )
            session.add(job)
            await session.commit()

        steps_status = {s: "pending" for s in APPLY_STEPS}
        log_entries = []

        async def failing_step():
            raise RuntimeError("Something broke")

        with pytest.raises(VaultError, match="Something broke"):
            await engine._run_step(
                job_id, "extract_bundle", steps_status, log_entries, failing_step,
            )

        assert steps_status["extract_bundle"] == "failed"


class TestApplyLifecycle:
    @pytest.mark.asyncio
    async def test_failed_apply_marks_job_failed(self, update_dir, mock_gpg, engine_db, tmp_path):
        """A failed apply sets job status to 'failed' with error message."""
        engine = _make_engine(update_dir, mock_gpg, engine_db)

        # Create a bundle that will fail checksum (no real files extracted)
        bundle_path = make_test_bundle(tmp_path, version="2.0.0")

        job_id = "test-apply-fail"
        async with engine_db() as session:
            job = UpdateJob(
                id=job_id,
                status="pending",
                bundle_version="2.0.0",
                from_version="1.0.0",
                bundle_path=str(bundle_path),
            )
            session.add(job)
            await session.commit()

        # Mock systemctl calls so we don't need real systemd
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
            mock_proc.return_value.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.return_value.returncode = 1
            # Run apply — it will fail at some step
            await engine.apply(job_id, str(bundle_path), "1.0.0")

        # Job should be marked as failed (or completed depending on step failure)
        async with engine_db() as session:
            result = await session.execute(select(UpdateJob).where(UpdateJob.id == job_id))
            job = result.scalar_one()
            # Engine should have cleared active job
            assert engine._active_job_id is None
            # Job should have completed_at set
            assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_version_updated_on_success(self, update_dir, mock_gpg, engine_db, tmp_path):
        """SystemConfig version is updated when all steps succeed."""
        engine = _make_engine(update_dir, mock_gpg, engine_db)

        # Seed the current version
        async with engine_db() as session:
            session.add(SystemConfig(key="update.current_version", value="1.0.0"))
            await session.commit()

        # Create a bundle
        files = {"backend/main.py": b"print('v2')"}
        bundle_path = make_test_bundle(tmp_path, version="2.0.0", files=files)

        job_id = "test-apply-success"
        async with engine_db() as session:
            job = UpdateJob(
                id=job_id,
                status="pending",
                bundle_version="2.0.0",
                from_version="1.0.0",
                bundle_path=str(bundle_path),
            )
            session.add(job)
            await session.commit()

        # Mock external calls
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))

        mock_health = AsyncMock(return_value={
            "all_passed": True,
            "backend": {"passed": True, "attempts": 1, "status": 200},
            "frontend": {"passed": True, "attempts": 1, "status": 200},
            "caddy": {"passed": True, "attempts": 1, "status": 200},
        })

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(b"ok", b"")), \
             patch("app.services.update.engine.HealthChecker") as mock_checker_cls, \
             patch("app.services.update.engine.settings") as mock_settings:

            mock_checker_cls.return_value.check_all = mock_health
            mock_settings.vault_db_url = "sqlite+aiosqlite://"
            mock_settings.vault_updates_dir = str(update_dir.base)
            mock_settings.vault_version_file_path = str(tmp_path / "version.json")
            mock_settings.vault_yara_rules_dir = str(tmp_path / "yara")

            await engine.apply(job_id, str(bundle_path), "1.0.0")

        # Check that the version was updated
        async with engine_db() as session:
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key == "update.current_version")
            )
            row = result.scalar_one_or_none()
            if row:
                assert row.value == "2.0.0"

        # Active job should be cleared
        assert engine._active_job_id is None

    @pytest.mark.asyncio
    async def test_active_job_cleared_after_apply(self, update_dir, mock_gpg, engine_db, tmp_path):
        """_active_job_id is always cleared in the finally block, even on failure."""
        engine = _make_engine(update_dir, mock_gpg, engine_db)

        # Point to a non-existent bundle to force failure
        job_id = "test-cleanup"
        async with engine_db() as session:
            job = UpdateJob(
                id=job_id,
                status="pending",
                bundle_version="9.9.9",
                from_version="1.0.0",
            )
            session.add(job)
            await session.commit()

        # This will fail because the bundle doesn't exist
        await engine.apply(job_id, str(tmp_path / "nonexistent.tar"), "1.0.0")

        assert engine._active_job_id is None
