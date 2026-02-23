"""GPU resource scheduler — enforces inference priority and manages GPU allocation for training."""

import asyncio

import structlog

from app.config import settings
from app.core.exceptions import VaultError
from app.services.monitoring import get_gpu_info

logger = structlog.get_logger()


class GPUScheduler:
    """Lightweight scheduler enforcing inference priority and managing GPU allocation."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._active_job_id: str | None = None
        self._active_gpu_index: int | None = None

    @property
    def active_job_id(self) -> str | None:
        return self._active_job_id

    @property
    def active_gpu_index(self) -> int | None:
        return self._active_gpu_index

    async def _get_training_config(self) -> dict:
        """Read training config from SystemConfig table, with defaults."""
        from app.core.database import SystemConfig, async_session
        from sqlalchemy import select

        defaults = {
            "training.enabled": "true",
            "training.gpu_index": str(settings.vault_training_gpu_index),
            "training.max_memory_pct": "0.9",
            "training.max_concurrent_jobs": str(settings.vault_training_max_concurrent),
        }

        try:
            async with async_session() as session:
                result = await session.execute(
                    select(SystemConfig).where(
                        SystemConfig.key.in_(defaults.keys())
                    )
                )
                for row in result.scalars().all():
                    defaults[row.key] = row.value
        except Exception as e:
            logger.debug("scheduler_config_read_fallback", reason=str(e))

        return defaults

    async def can_start_training(self) -> tuple[bool, str]:
        """Check if training can start. Returns (allowed, reason)."""
        config = await self._get_training_config()

        # Check global toggle
        if config.get("training.enabled", "true").lower() != "true":
            return False, "Training is disabled in system config."

        # Check concurrent limit
        if self._active_job_id is not None:
            return False, f"Training job '{self._active_job_id}' is already running."

        # Check GPU availability
        gpu_index = int(config.get("training.gpu_index", str(settings.vault_training_gpu_index)))
        max_memory_pct = float(config.get("training.max_memory_pct", "0.9"))

        gpus = await get_gpu_info()
        if not gpus:
            # No GPU detected — allow anyway (dev mode, mock training)
            logger.debug("scheduler_no_gpu_detected", note="allowing training without GPU check")
            return True, "ok"

        # Find the target GPU
        target_gpu = next((g for g in gpus if g.index == gpu_index), None)
        if target_gpu is None:
            return False, f"GPU {gpu_index} not found. Available GPUs: {[g.index for g in gpus]}"

        # Check memory usage
        if target_gpu.memory_total_mb > 0:
            used_pct = target_gpu.memory_used_mb / target_gpu.memory_total_mb
            if used_pct > max_memory_pct:
                return False, (
                    f"GPU {gpu_index} memory usage ({used_pct:.0%}) exceeds "
                    f"threshold ({max_memory_pct:.0%})."
                )

        return True, "ok"

    async def acquire_gpu_for_training(self, job_id: str) -> int:
        """Acquire a GPU for training. Returns the GPU index."""
        async with self._lock:
            allowed, reason = await self.can_start_training()
            if not allowed:
                raise VaultError(
                    code="gpu_unavailable",
                    message=reason,
                    status=409,
                )

            config = await self._get_training_config()
            gpu_index = int(config.get("training.gpu_index", str(settings.vault_training_gpu_index)))

            self._active_job_id = job_id
            self._active_gpu_index = gpu_index

            logger.info("gpu_acquired_for_training", job_id=job_id, gpu_index=gpu_index)
            return gpu_index

    async def release_gpu(self, job_id: str) -> None:
        """Release GPU lock after training completes/fails/cancels."""
        async with self._lock:
            if self._active_job_id == job_id:
                logger.info("gpu_released", job_id=job_id, gpu_index=self._active_gpu_index)
                self._active_job_id = None
                self._active_gpu_index = None

    async def get_allocation_status(self) -> list[dict]:
        """Get current GPU allocation status for all GPUs."""
        config = await self._get_training_config()
        training_gpu = int(config.get("training.gpu_index", str(settings.vault_training_gpu_index)))

        gpus = await get_gpu_info()
        allocations = []

        for gpu in gpus:
            if gpu.index == training_gpu and self._active_job_id is not None:
                assigned_to = "training"
                job_id = self._active_job_id
            else:
                assigned_to = "inference"
                job_id = None

            memory_pct = (gpu.memory_used_mb / gpu.memory_total_mb * 100) if gpu.memory_total_mb > 0 else 0.0

            allocations.append({
                "gpu_index": gpu.index,
                "assigned_to": assigned_to,
                "job_id": job_id,
                "memory_used_pct": round(memory_pct, 1),
            })

        # If no GPUs detected, return a placeholder
        if not allocations:
            allocations.append({
                "gpu_index": 0,
                "assigned_to": "inference",
                "job_id": None,
                "memory_used_pct": 0.0,
            })

        return allocations
