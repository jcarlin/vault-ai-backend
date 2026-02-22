"""Filesystem layout manager for the update mechanism."""

import shutil
from pathlib import Path

from app.config import settings


class UpdateDirectory:
    """Manages filesystem layout for update staging, rollback, and bundle storage."""

    def __init__(self, base_dir: str | None = None):
        self._base = Path(base_dir or settings.vault_updates_dir)

    @property
    def base(self) -> Path:
        return self._base

    @property
    def staging(self) -> Path:
        return self._base / "staging"

    @property
    def rollback(self) -> Path:
        return self._base / "rollback"

    @property
    def bundles(self) -> Path:
        return self._base / "bundles"

    @property
    def next_deploy(self) -> Path:
        return self._base / "next"

    def init_directories(self) -> None:
        """Create all directories if they don't exist."""
        for d in [self.staging, self.rollback, self.bundles, self.next_deploy]:
            d.mkdir(parents=True, exist_ok=True)

    def staging_path(self, job_id: str) -> Path:
        """Directory for extracting a bundle during apply."""
        d = self.staging / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def cleanup_staging(self, job_id: str) -> None:
        """Remove staging directory after job completes."""
        job_dir = self.staging / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir)

    def cleanup_rollback(self) -> None:
        """Remove existing rollback data before a new update."""
        if self.rollback.exists():
            shutil.rmtree(self.rollback)
            self.rollback.mkdir(parents=True, exist_ok=True)

    def has_rollback(self) -> bool:
        """Check if rollback data exists."""
        version_file = self.rollback / "version.json"
        return version_file.exists()
