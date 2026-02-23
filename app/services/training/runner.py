"""Training job runner — spawns HF trl/peft training processes in the training-venv."""

import asyncio
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path

import structlog

from app.config import settings
from app.services.training.config import TrainingRunConfig
from app.services.training.scheduler import GPUScheduler
from app.services.training.service import TrainingService

logger = structlog.get_logger()


class TrainingRunner:
    """Manages training subprocess lifecycle: spawn, monitor, cancel, pause."""

    def __init__(
        self,
        scheduler: GPUScheduler,
        service: TrainingService,
    ):
        self._scheduler = scheduler
        self._service = service
        self._active_process: asyncio.subprocess.Process | None = None
        self._active_job_id: str | None = None
        self._monitor_task: asyncio.Task | None = None

    @property
    def active_job_id(self) -> str | None:
        return self._active_job_id

    @property
    def is_running(self) -> bool:
        return self._active_process is not None and self._active_process.returncode is None

    async def start_job(self, job_id: str, run_config: TrainingRunConfig) -> None:
        """Start a training job as a subprocess in the training-venv."""
        if self._active_job_id is not None:
            raise RuntimeError(f"Cannot start job {job_id}: job {self._active_job_id} already running.")

        # Acquire GPU
        gpu_index = await self._scheduler.acquire_gpu_for_training(job_id)
        run_config.gpu_index = gpu_index

        # Write config to temp file
        config_path = Path(run_config.status_dir) / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(run_config.model_dump_json(indent=2))

        # Build subprocess command
        python_path = Path(settings.vault_training_venv) / "bin" / "python"
        cmd = [
            str(python_path),
            "-m", "app.services.training.worker",
            "--config", str(config_path),
        ]

        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_index)

        logger.info(
            "training_job_starting",
            job_id=job_id,
            gpu_index=gpu_index,
            model=run_config.base_model_path,
            dataset=run_config.dataset_path,
        )

        # Mark job as running
        await self._service.update_job_status(
            job_id,
            status="running",
            started_at=datetime.now(timezone.utc),
        )

        try:
            self._active_process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except Exception as e:
            await self._scheduler.release_gpu(job_id)
            await self._service.update_job_status(
                job_id,
                status="failed",
                error=f"Failed to start training process: {e}",
                completed_at=datetime.now(timezone.utc),
            )
            raise

        self._active_job_id = job_id

        # Start monitoring task
        self._monitor_task = asyncio.create_task(
            self._monitor_job(job_id, run_config.status_dir)
        )

    async def cancel_job(self, job_id: str) -> None:
        """Cancel a running training job via SIGTERM."""
        if self._active_job_id != job_id:
            raise RuntimeError(f"Job {job_id} is not the active job.")

        if self._active_process and self._active_process.returncode is None:
            logger.info("training_job_cancelling", job_id=job_id)
            try:
                self._active_process.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass

    async def pause_job(self, job_id: str) -> None:
        """Pause a running training job via SIGUSR1 (checkpoint and exit)."""
        if self._active_job_id != job_id:
            raise RuntimeError(f"Job {job_id} is not the active job.")

        if self._active_process and self._active_process.returncode is None:
            logger.info("training_job_pausing", job_id=job_id)
            try:
                self._active_process.send_signal(signal.SIGUSR1)
            except ProcessLookupError:
                pass

    async def _monitor_job(self, job_id: str, status_dir: str) -> None:
        """Poll status.json and update DB until the process exits."""
        status_path = Path(status_dir) / "status.json"

        try:
            while self._active_process and self._active_process.returncode is None:
                await asyncio.sleep(2)

                # Read status.json if it exists
                if status_path.exists():
                    try:
                        status_data = json.loads(status_path.read_text())
                        await self._apply_status_update(job_id, status_data)
                    except (json.JSONDecodeError, Exception) as e:
                        logger.debug("status_parse_error", job_id=job_id, error=str(e))

            # Process exited — determine outcome
            return_code = self._active_process.returncode if self._active_process else -1

            # Read final status
            final_status = None
            if status_path.exists():
                try:
                    final_status = json.loads(status_path.read_text())
                except Exception:
                    pass

            if return_code == 0:
                # Success — check for adapter output
                adapter_id = final_status.get("adapter_id") if final_status else None
                await self._service.update_job_status(
                    job_id,
                    status="completed",
                    progress=100.0,
                    adapter_id=adapter_id,
                    completed_at=datetime.now(timezone.utc),
                    metrics_json=json.dumps(final_status.get("metrics", {})) if final_status else None,
                )
                logger.info("training_job_completed", job_id=job_id, adapter_id=adapter_id)

            elif return_code == 42:
                # Paused (checkpoint exit code)
                await self._service.update_job_status(job_id, status="paused")
                logger.info("training_job_paused", job_id=job_id)

            elif return_code == -signal.SIGTERM or return_code == 143:
                # Cancelled via SIGTERM
                await self._service.update_job_status(
                    job_id,
                    status="cancelled",
                    completed_at=datetime.now(timezone.utc),
                )
                logger.info("training_job_cancelled", job_id=job_id)

            else:
                # Failed
                stderr_text = ""
                if self._active_process and self._active_process.stderr:
                    try:
                        stderr_bytes = await asyncio.wait_for(
                            self._active_process.stderr.read(4096), timeout=2.0
                        )
                        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
                    except Exception:
                        pass

                error_msg = final_status.get("error", stderr_text) if final_status else stderr_text
                if "CUDA out of memory" in error_msg or "OutOfMemoryError" in error_msg:
                    error_msg = f"GPU out of memory. Try reducing batch_size or using QLoRA. Original error: {error_msg[:500]}"

                await self._service.update_job_status(
                    job_id,
                    status="failed",
                    error=error_msg[:2000],
                    completed_at=datetime.now(timezone.utc),
                )
                logger.warning("training_job_failed", job_id=job_id, return_code=return_code)

        except Exception as e:
            logger.error("training_monitor_error", job_id=job_id, error=str(e))
            await self._service.update_job_status(
                job_id,
                status="failed",
                error=f"Monitor error: {e}",
                completed_at=datetime.now(timezone.utc),
            )

        finally:
            # Always release GPU
            await self._scheduler.release_gpu(job_id)
            self._active_job_id = None
            self._active_process = None

    async def _apply_status_update(self, job_id: str, status_data: dict) -> None:
        """Apply a status.json update to the DB."""
        step = status_data.get("step", 0)
        total_steps = status_data.get("total_steps", 1)
        progress = (step / total_steps * 100) if total_steps > 0 else 0.0

        metrics = {
            "loss": status_data.get("loss"),
            "learning_rate": status_data.get("lr"),
            "epochs_completed": status_data.get("epoch", 0),
            "total_epochs": status_data.get("total_epochs", 0),
            "tokens_processed": status_data.get("tokens_processed", 0),
            "estimated_time_remaining": status_data.get("eta_seconds"),
            "steps_completed": step,
            "total_steps": total_steps,
            "loss_history": status_data.get("loss_history"),
        }

        await self._service.update_job_status(
            job_id,
            progress=round(progress, 1),
            metrics_json=json.dumps(metrics),
        )

    async def get_latest_status(self, job_id: str, status_dir: str) -> dict | None:
        """Read the latest status.json for a job."""
        status_path = Path(status_dir) / "status.json"
        if not status_path.exists():
            return None
        try:
            return json.loads(status_path.read_text())
        except Exception:
            return None
