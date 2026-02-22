"""Quarantine filesystem layout manager."""

from pathlib import Path

from app.config import settings


class QuarantineDirectory:
    """Manages the quarantine directory structure.

    Layout:
        /opt/vault/quarantine/
        ├── staging/{job_id}/{file_id}_{filename}
        ├── held/{file_id}_{filename}
        ├── sanitized/{file_id}_{filename}
        ├── signatures/clamav/
        ├── signatures/yara_rules/
        └── blacklist.json
    """

    def __init__(self, base_dir: str | None = None):
        self._base = Path(base_dir or settings.vault_quarantine_dir)

    @property
    def base(self) -> Path:
        return self._base

    @property
    def staging(self) -> Path:
        return self._base / "staging"

    @property
    def held(self) -> Path:
        return self._base / "held"

    @property
    def sanitized(self) -> Path:
        return self._base / "sanitized"

    @property
    def signatures_clamav(self) -> Path:
        return self._base / "signatures" / "clamav"

    @property
    def signatures_yara(self) -> Path:
        return self._base / "signatures" / "yara_rules"

    @property
    def blacklist_path(self) -> Path:
        return self._base / "blacklist.json"

    def init_directories(self) -> None:
        """Create all quarantine directories if they don't exist."""
        for d in [self.staging, self.held, self.sanitized, self.signatures_clamav, self.signatures_yara]:
            d.mkdir(parents=True, exist_ok=True)

    def staging_dir_for_job(self, job_id: str) -> Path:
        """Get (and create) the staging directory for a specific job."""
        d = self.staging / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def staging_path(self, job_id: str, file_id: str, filename: str) -> Path:
        """Full path for a file in staging."""
        safe_name = filename.replace("/", "_").replace("\\", "_")
        return self.staging_dir_for_job(job_id) / f"{file_id}_{safe_name}"

    def held_path(self, file_id: str, filename: str) -> Path:
        """Full path for a held file."""
        safe_name = filename.replace("/", "_").replace("\\", "_")
        return self.held / f"{file_id}_{safe_name}"

    def sanitized_file_path(self, file_id: str, filename: str) -> Path:
        """Full path for a sanitized file."""
        safe_name = filename.replace("/", "_").replace("\\", "_")
        return self.sanitized / f"{file_id}_{safe_name}"

    def cleanup_job_staging(self, job_id: str) -> None:
        """Remove the staging directory for a completed job."""
        import shutil
        job_dir = self.staging / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir)
