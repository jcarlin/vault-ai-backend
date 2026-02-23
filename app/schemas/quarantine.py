"""Pydantic schemas for quarantine pipeline endpoints."""

from pydantic import BaseModel, Field


# ── Scan submission ──────────────────────────────────────────────────────


class ScanSubmitResponse(BaseModel):
    job_id: str
    status: str = "pending"
    total_files: int
    message: str = "Scan submitted"


class ScanPathRequest(BaseModel):
    path: str = Field(..., description="Filesystem path to scan (USB mount, directory)")


# ── Scan status ──────────────────────────────────────────────────────────


class FileFinding(BaseModel):
    stage: str
    severity: str
    code: str
    message: str
    details: dict = {}


class FileStatus(BaseModel):
    id: str
    job_id: str
    original_filename: str
    file_size: int
    mime_type: str | None = None
    sha256_hash: str | None = None
    status: str
    current_stage: str | None = None
    risk_severity: str = "none"
    findings: list[FileFinding] = []
    quarantine_path: str | None = None
    sanitized_path: str | None = None
    destination_path: str | None = None
    review_reason: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ScanJobStatus(BaseModel):
    id: str
    status: str
    total_files: int
    files_completed: int
    files_flagged: int
    files_clean: int
    source_type: str
    submitted_by: str | None = None
    created_at: str | None = None
    completed_at: str | None = None
    files: list[FileStatus] = []


# ── Held files ───────────────────────────────────────────────────────────


class HeldFilesResponse(BaseModel):
    files: list[FileStatus]
    total: int
    offset: int
    limit: int


class ReviewRequest(BaseModel):
    reason: str = Field(..., min_length=1, description="Reason for approval/rejection")


# ── Signatures ───────────────────────────────────────────────────────────


class SignatureSource(BaseModel):
    available: bool = False
    last_updated: str | None = None
    age_hours: float | None = None
    freshness: str | None = None
    file_count: int | None = None
    rule_count: int | None = None
    hash_count: int | None = None


class SignaturesResponse(BaseModel):
    clamav: SignatureSource
    yara: SignatureSource
    blacklist: SignatureSource


# ── Stats ────────────────────────────────────────────────────────────────


class QuarantineStatsResponse(BaseModel):
    total_jobs: int
    jobs_completed: int
    total_files_scanned: int
    files_clean: int
    files_held: int
    files_approved: int
    files_rejected: int
    severity_distribution: dict = {}


# ── Config ───────────────────────────────────────────────────────────────


class QuarantineConfig(BaseModel):
    max_file_size: int = 1073741824
    max_batch_files: int = 100
    max_compression_ratio: int = 100
    max_archive_depth: int = 3
    auto_approve_clean: bool = True
    strictness_level: str = "standard"
    ai_safety_enabled: bool = True
    pii_enabled: bool = True
    pii_action: str = "flag"
    injection_detection_enabled: bool = True
    model_hash_verification: bool = True


class QuarantineConfigUpdate(BaseModel):
    max_file_size: int | None = None
    max_batch_files: int | None = None
    max_compression_ratio: int | None = None
    max_archive_depth: int | None = None
    auto_approve_clean: bool | None = None
    strictness_level: str | None = None
    ai_safety_enabled: bool | None = None
    pii_enabled: bool | None = None
    pii_action: str | None = None
    injection_detection_enabled: bool | None = None
    model_hash_verification: bool | None = None
