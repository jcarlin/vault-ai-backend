"""Training progress tracker — reads status.json and provides formatted progress data."""

import json
from pathlib import Path

import structlog

from app.config import settings

logger = structlog.get_logger()


class ProgressTracker:
    """Reads status.json files and provides formatted training progress data."""

    def __init__(self):
        self._adapters_dir = Path(settings.vault_adapters_dir)
        self._training_data_dir = Path(settings.vault_training_data_dir)

    def get_status_dir(self, job_id: str) -> str:
        """Get the status directory for a training job."""
        return str(self._adapters_dir / job_id)

    def get_progress(self, job_id: str) -> dict | None:
        """Read the latest progress from a job's status.json."""
        status_path = Path(self.get_status_dir(job_id)) / "status.json"
        if not status_path.exists():
            return None

        try:
            data = json.loads(status_path.read_text())
            return self._format_progress(data)
        except Exception as e:
            logger.debug("progress_read_error", job_id=job_id, error=str(e))
            return None

    def get_log_lines(self, job_id: str, tail: int = 100) -> list[str]:
        """Read the last N lines of the training log."""
        log_path = Path(self.get_status_dir(job_id)) / "training.log"
        if not log_path.exists():
            return []

        try:
            lines = log_path.read_text().splitlines()
            return lines[-tail:]
        except Exception:
            return []

    def _format_progress(self, data: dict) -> dict:
        """Format raw status.json data into a clean progress dict."""
        step = data.get("step", 0)
        total_steps = data.get("total_steps", 1)
        progress_pct = (step / total_steps * 100) if total_steps > 0 else 0.0

        return {
            "state": data.get("state", "unknown"),
            "step": step,
            "total_steps": total_steps,
            "progress_pct": round(progress_pct, 1),
            "epoch": data.get("epoch"),
            "total_epochs": data.get("total_epochs"),
            "loss": data.get("loss"),
            "learning_rate": data.get("lr"),
            "tokens_processed": data.get("tokens_processed", 0),
            "eta_seconds": data.get("eta_seconds"),
            "loss_history": data.get("loss_history", []),
            "error": data.get("error"),
            "adapter_id": data.get("adapter_id"),
        }
