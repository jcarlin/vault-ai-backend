"""Pydantic v2 request/response models for update endpoints."""

from pydantic import BaseModel, Field


# ── Response Models ──────────────────────────────────────────────────────────


class UpdateStatusResponse(BaseModel):
    """GET /vault/updates/status"""

    current_version: str = "1.0.0"
    last_update_at: str | None = None
    last_update_version: str | None = None
    rollback_available: bool = False
    rollback_version: str | None = None
    update_count: int = 0


class BundleInfo(BaseModel):
    """Info about a discovered update bundle."""

    version: str
    path: str
    signature_valid: bool = False
    size_bytes: int = 0
    changelog: str = ""
    components: dict[str, bool] = Field(default_factory=dict)
    compatible: bool = True
    min_compatible_version: str = "0.0.0"
    created_at: str = ""


class ScanResponse(BaseModel):
    """POST /vault/updates/scan"""

    found: bool = False
    bundles: list[BundleInfo] = Field(default_factory=list)


class ApplyRequest(BaseModel):
    """POST /vault/updates/apply"""

    confirmation: str = Field(..., description="Must be exactly 'APPLY UPDATE'")
    create_backup: bool = True
    backup_passphrase: str | None = None


class ApplyResponse(BaseModel):
    """POST /vault/updates/apply response"""

    job_id: str
    status: str = "started"
    message: str = ""


class ProgressStep(BaseModel):
    """A single step in the update process."""

    name: str
    status: str = "pending"  # pending | in_progress | completed | failed | skipped


class ProgressResponse(BaseModel):
    """GET /vault/updates/progress/{job_id}"""

    job_id: str
    status: str
    bundle_version: str
    from_version: str
    progress_pct: int = 0
    current_step: str | None = None
    steps: list[ProgressStep] = Field(default_factory=list)
    log_entries: list[str] = Field(default_factory=list)
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class RollbackRequest(BaseModel):
    """POST /vault/updates/rollback"""

    confirmation: str = Field(..., description="Must be exactly 'ROLLBACK UPDATE'")


class RollbackResponse(BaseModel):
    """POST /vault/updates/rollback response"""

    job_id: str
    status: str = "rollback_started"
    rollback_to_version: str = ""


class UpdateHistoryItem(BaseModel):
    """A single entry in the update history."""

    job_id: str
    status: str
    bundle_version: str
    from_version: str
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str | None = None


class HistoryResponse(BaseModel):
    """GET /vault/updates/history"""

    updates: list[UpdateHistoryItem] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 20
