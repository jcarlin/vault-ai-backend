"""Unit tests for TrainingRunner — mocked subprocess."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.training.config import TrainingRunConfig
from app.services.training.runner import TrainingRunner
from app.services.training.scheduler import GPUScheduler
from app.services.training.service import TrainingService


@pytest.fixture
def mock_scheduler():
    scheduler = AsyncMock(spec=GPUScheduler)
    scheduler.acquire_gpu_for_training = AsyncMock(return_value=1)
    scheduler.release_gpu = AsyncMock()
    return scheduler


@pytest.fixture
def mock_service():
    service = AsyncMock(spec=TrainingService)
    service.update_job_status = AsyncMock()
    return service


@pytest.fixture
def runner(mock_scheduler, mock_service):
    return TrainingRunner(scheduler=mock_scheduler, service=mock_service)


@pytest.fixture
def run_config(tmp_path):
    return TrainingRunConfig(
        job_id="test-job-123",
        base_model_path="/opt/vault/models/test-model",
        dataset_path="/opt/vault/data/training/test.jsonl",
        output_dir=str(tmp_path / "output"),
        status_dir=str(tmp_path / "status"),
        gpu_index=1,
    )


class TestTrainingRunner:
    def test_initial_state(self, runner):
        assert runner.active_job_id is None
        assert runner.is_running is False

    @pytest.mark.asyncio
    async def test_start_job_acquires_gpu(self, runner, mock_scheduler, run_config):
        """Starting a job should acquire a GPU from the scheduler."""
        # Mock subprocess to exit immediately
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.read = AsyncMock(return_value=b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await runner.start_job("test-job-123", run_config)

        mock_scheduler.acquire_gpu_for_training.assert_awaited_once_with("test-job-123")

    @pytest.mark.asyncio
    async def test_start_job_writes_config(self, runner, run_config, tmp_path):
        """Starting a job should write config.json to the status dir."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.read = AsyncMock(return_value=b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await runner.start_job("test-job-123", run_config)

        config_path = Path(run_config.status_dir) / "config.json"
        assert config_path.exists()
        data = json.loads(config_path.read_text())
        assert data["job_id"] == "test-job-123"

    @pytest.mark.asyncio
    async def test_start_job_marks_running(self, runner, mock_service, run_config):
        """Starting a job should update DB status to 'running'."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.read = AsyncMock(return_value=b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await runner.start_job("test-job-123", run_config)

        # Should have been called with status="running"
        calls = mock_service.update_job_status.call_args_list
        running_call = [c for c in calls if c.kwargs.get("status") == "running"]
        assert len(running_call) >= 1

    @pytest.mark.asyncio
    async def test_cancel_job_wrong_id(self, runner):
        """Cancelling a job that isn't active should raise."""
        with pytest.raises(RuntimeError, match="not the active job"):
            await runner.cancel_job("nonexistent-id")

    @pytest.mark.asyncio
    async def test_pause_job_wrong_id(self, runner):
        """Pausing a job that isn't active should raise."""
        with pytest.raises(RuntimeError, match="not the active job"):
            await runner.pause_job("nonexistent-id")

    @pytest.mark.asyncio
    async def test_apply_status_update(self, runner, mock_service):
        """_apply_status_update should update DB with progress and metrics."""
        status_data = {
            "step": 50,
            "total_steps": 100,
            "loss": 0.42,
            "lr": 0.0001,
            "epoch": 2,
            "total_epochs": 5,
            "tokens_processed": 1600,
        }

        await runner._apply_status_update("test-job-123", status_data)

        mock_service.update_job_status.assert_awaited_once()
        call_kwargs = mock_service.update_job_status.call_args.kwargs
        assert call_kwargs["progress"] == 50.0
        metrics = json.loads(call_kwargs["metrics_json"])
        assert metrics["loss"] == 0.42
        assert metrics["steps_completed"] == 50

    @pytest.mark.asyncio
    async def test_start_job_subprocess_failure_releases_gpu(
        self, runner, mock_scheduler, mock_service, run_config
    ):
        """If subprocess creation fails, GPU should be released."""
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=OSError("No such file: python"),
        ):
            with pytest.raises(OSError):
                await runner.start_job("test-job-123", run_config)

        mock_scheduler.release_gpu.assert_awaited_once_with("test-job-123")
        # Should also mark job as failed
        fail_calls = [
            c for c in mock_service.update_job_status.call_args_list
            if c.kwargs.get("status") == "failed"
        ]
        assert len(fail_calls) >= 1
