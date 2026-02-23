"""Unit tests for GPUScheduler."""

import pytest
from unittest.mock import AsyncMock, patch

from app.core.exceptions import VaultError
from app.schemas.health import GpuInfo
from app.services.training.scheduler import GPUScheduler


@pytest.fixture
def scheduler():
    return GPUScheduler()


def _mock_gpus(memory_used_pct=0.5):
    """Create mock GPU info."""
    total_mb = 32768  # 32GB
    used_mb = int(total_mb * memory_used_pct)
    return [
        GpuInfo(index=0, name="RTX 5090", memory_total_mb=total_mb, memory_used_mb=used_mb, utilization_pct=30.0),
        GpuInfo(index=1, name="RTX 5090", memory_total_mb=total_mb, memory_used_mb=1024, utilization_pct=5.0),
    ]


class TestGPUScheduler:
    @pytest.mark.asyncio
    async def test_can_start_training_ok(self, scheduler):
        """Should allow training when GPU is available."""
        with patch.object(scheduler, "_get_training_config", return_value={
            "training.enabled": "true",
            "training.gpu_index": "1",
            "training.max_memory_pct": "0.9",
        }):
            with patch("app.services.training.scheduler.get_gpu_info", return_value=_mock_gpus()):
                allowed, reason = await scheduler.can_start_training()
                assert allowed is True
                assert reason == "ok"

    @pytest.mark.asyncio
    async def test_can_start_training_disabled(self, scheduler):
        """Should deny training when disabled in config."""
        with patch.object(scheduler, "_get_training_config", return_value={
            "training.enabled": "false",
            "training.gpu_index": "1",
            "training.max_memory_pct": "0.9",
        }):
            allowed, reason = await scheduler.can_start_training()
            assert allowed is False
            assert "disabled" in reason.lower()

    @pytest.mark.asyncio
    async def test_can_start_training_already_running(self, scheduler):
        """Should deny training when a job is already running."""
        scheduler._active_job_id = "existing-job"
        with patch.object(scheduler, "_get_training_config", return_value={
            "training.enabled": "true",
        }):
            allowed, reason = await scheduler.can_start_training()
            assert allowed is False
            assert "already running" in reason.lower()

    @pytest.mark.asyncio
    async def test_acquire_gpu_sets_lock(self, scheduler):
        """Acquiring GPU should set the active job and GPU index."""
        with patch.object(scheduler, "_get_training_config", return_value={
            "training.enabled": "true",
            "training.gpu_index": "1",
            "training.max_memory_pct": "0.9",
        }):
            with patch("app.services.training.scheduler.get_gpu_info", return_value=_mock_gpus()):
                gpu_index = await scheduler.acquire_gpu_for_training("job-1")
                assert gpu_index == 1
                assert scheduler.active_job_id == "job-1"
                assert scheduler.active_gpu_index == 1

    @pytest.mark.asyncio
    async def test_release_gpu_clears_lock(self, scheduler):
        """Releasing GPU should clear the active job."""
        scheduler._active_job_id = "job-1"
        scheduler._active_gpu_index = 1

        await scheduler.release_gpu("job-1")

        assert scheduler.active_job_id is None
        assert scheduler.active_gpu_index is None

    @pytest.mark.asyncio
    async def test_acquire_gpu_when_already_locked(self, scheduler):
        """Should raise 409 when GPU is already locked."""
        scheduler._active_job_id = "existing-job"

        with patch.object(scheduler, "_get_training_config", return_value={
            "training.enabled": "true",
        }):
            with pytest.raises(VaultError) as exc_info:
                await scheduler.acquire_gpu_for_training("new-job")
            assert exc_info.value.status == 409

    @pytest.mark.asyncio
    async def test_no_gpus_allows_training(self, scheduler):
        """When no GPUs detected (dev mode), should still allow."""
        with patch.object(scheduler, "_get_training_config", return_value={
            "training.enabled": "true",
            "training.gpu_index": "1",
            "training.max_memory_pct": "0.9",
        }):
            with patch("app.services.training.scheduler.get_gpu_info", return_value=[]):
                allowed, reason = await scheduler.can_start_training()
                assert allowed is True
