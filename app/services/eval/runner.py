"""Eval job runner — spawns eval worker subprocesses and monitors progress."""

import asyncio
import json
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog

from app.services.eval.config import EvalRunConfig
from app.services.eval.service import EvalService

logger = structlog.get_logger()


class EvalRunner:
    """Manages eval subprocess lifecycle: spawn, monitor, cancel."""

    def __init__(self, service: EvalService):
        self._service = service
        self._active_processes: dict[str, asyncio.subprocess.Process] = {}
        self._monitor_tasks: dict[str, asyncio.Task] = {}
        self._status_dirs: dict[str, str] = {}

    async def start_job(self, job_id: str, run_config: EvalRunConfig) -> None:
        """Start an eval job as a subprocess."""
        if job_id in self._active_processes:
            raise RuntimeError(f"Eval job {job_id} is already running.")

        # Write config to temp file
        config_path = Path(run_config.status_dir) / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(run_config.model_dump_json(indent=2))

        # Build subprocess command — use current Python interpreter
        python_path = sys.executable
        cmd = [
            python_path,
            "-m", "app.services.eval.worker",
            "--config", str(config_path),
        ]

        logger.info(
            "eval_job_starting",
            job_id=job_id,
            model=run_config.model_id,
            dataset=run_config.dataset_path,
        )

        # Mark job as running
        await self._service.update_job_status(
            job_id,
            status="running",
            started_at=datetime.now(timezone.utc),
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
            )
        except Exception as e:
            await self._service.update_job_status(
                job_id,
                status="failed",
                error=f"Failed to start eval process: {e}",
                completed_at=datetime.now(timezone.utc),
            )
            raise

        self._active_processes[job_id] = process
        self._status_dirs[job_id] = run_config.status_dir

        # Start monitoring task
        self._monitor_tasks[job_id] = asyncio.create_task(
            self._monitor_job(job_id, run_config.status_dir)
        )

    async def cancel_job(self, job_id: str) -> None:
        """Cancel a running eval job via SIGTERM."""
        process = self._active_processes.get(job_id)
        if process is None:
            raise RuntimeError(f"Eval job {job_id} is not active.")

        if process.returncode is None:
            logger.info("eval_job_cancelling", job_id=job_id)
            try:
                process.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass

    async def _monitor_job(self, job_id: str, status_dir: str) -> None:
        """Poll status.json and update DB until the process exits."""
        status_path = Path(status_dir) / "status.json"
        process = self._active_processes.get(job_id)

        try:
            while process and process.returncode is None:
                await asyncio.sleep(2)

                if status_path.exists():
                    try:
                        status_data = json.loads(status_path.read_text())
                        await self._apply_status_update(job_id, status_data)
                    except (json.JSONDecodeError, Exception) as e:
                        logger.debug("eval_status_parse_error", job_id=job_id, error=str(e))

            # Process exited
            return_code = process.returncode if process else -1

            # Read final status
            final_status = None
            if status_path.exists():
                try:
                    final_status = json.loads(status_path.read_text())
                except Exception:
                    pass

            if return_code == 0 and final_status:
                # Success — store results
                results_data = final_status.get("results", {})
                await self._service.update_job_status(
                    job_id,
                    status="completed",
                    progress=100.0,
                    results_json=json.dumps(results_data),
                    total_examples=final_status.get("total_examples", 0),
                    examples_completed=final_status.get("examples_completed", 0),
                    completed_at=datetime.now(timezone.utc),
                )
                logger.info("eval_job_completed", job_id=job_id)

            elif return_code == -signal.SIGTERM or return_code == 143:
                await self._service.update_job_status(
                    job_id,
                    status="cancelled",
                    completed_at=datetime.now(timezone.utc),
                )
                logger.info("eval_job_cancelled", job_id=job_id)

            else:
                stderr_text = ""
                if process and process.stderr:
                    try:
                        stderr_bytes = await asyncio.wait_for(
                            process.stderr.read(4096), timeout=2.0
                        )
                        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
                    except Exception:
                        pass

                error_msg = (
                    final_status.get("error", stderr_text) if final_status else stderr_text
                )
                await self._service.update_job_status(
                    job_id,
                    status="failed",
                    error=error_msg[:2000] if error_msg else "Unknown error",
                    completed_at=datetime.now(timezone.utc),
                )
                logger.warning("eval_job_failed", job_id=job_id, return_code=return_code)

        except Exception as e:
            logger.error("eval_monitor_error", job_id=job_id, error=str(e))
            await self._service.update_job_status(
                job_id,
                status="failed",
                error=f"Monitor error: {e}",
                completed_at=datetime.now(timezone.utc),
            )

        finally:
            self._active_processes.pop(job_id, None)
            self._monitor_tasks.pop(job_id, None)
            self._status_dirs.pop(job_id, None)

    async def _apply_status_update(self, job_id: str, status_data: dict) -> None:
        """Apply a status.json update to the DB."""
        completed = status_data.get("examples_completed", 0)
        total = status_data.get("total_examples", 1)
        progress = (completed / total * 100) if total > 0 else 0.0

        await self._service.update_job_status(
            job_id,
            progress=round(progress, 1),
            examples_completed=completed,
            total_examples=total,
        )

    def get_latest_status(self, job_id: str) -> dict | None:
        """Read the latest status.json for a job (for WebSocket streaming)."""
        status_dir = self._status_dirs.get(job_id)
        if not status_dir:
            return None
        status_path = Path(status_dir) / "status.json"
        if not status_path.exists():
            return None
        try:
            return json.loads(status_path.read_text())
        except Exception:
            return None
