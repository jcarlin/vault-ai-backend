"""Pydantic schemas for dataset management endpoints (Epic 22)."""

from pydantic import BaseModel, Field


# ── Data Sources ─────────────────────────────────────────────────────────────


class DataSourceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    source_type: str = Field(..., pattern="^(local|s3|smb|nfs)$")
    config: dict = {}


class DataSourceUpdate(BaseModel):
    name: str | None = None
    config: dict | None = None
    status: str | None = None


class DataSourceResponse(BaseModel):
    id: str
    name: str
    source_type: str
    status: str
    config: dict = {}
    last_scanned_at: str | None = None
    last_error: str | None = None
    created_at: str
    updated_at: str


class DataSourceList(BaseModel):
    sources: list[DataSourceResponse]
    total: int


class DataSourceTestResult(BaseModel):
    success: bool
    message: str
    files_found: int = 0


class DataSourceScanResult(BaseModel):
    source_id: str
    datasets_discovered: int
    datasets_updated: int
    errors: list[str] = []


# ── Datasets ─────────────────────────────────────────────────────────────────


class DatasetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    dataset_type: str = Field(default="other", pattern="^(training|eval|document|other)$")
    format: str = Field(default="jsonl", pattern="^(jsonl|csv|parquet|txt|pdf|mixed)$")
    source_path: str = Field(..., min_length=1)
    tags: list[str] = []


class DatasetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    dataset_type: str | None = None
    tags: list[str] | None = None


class DatasetResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    dataset_type: str
    format: str
    status: str
    source_id: str | None = None
    source_path: str
    file_size_bytes: int = 0
    record_count: int = 0
    tags: list[str] = []
    metadata: dict = {}
    quarantine_job_id: str | None = None
    validation: dict | None = None
    registered_by: str | None = None
    created_at: str
    updated_at: str


class DatasetList(BaseModel):
    datasets: list[DatasetResponse]
    total: int


class DatasetUploadResponse(BaseModel):
    id: str
    name: str
    format: str
    file_size_bytes: int
    status: str
    message: str = "Dataset uploaded successfully"


class DatasetPreview(BaseModel):
    id: str
    name: str
    format: str
    total_records: int
    preview_records: list[dict] = []


class DatasetStats(BaseModel):
    total_datasets: int = 0
    by_type: dict[str, int] = {}
    by_format: dict[str, int] = {}
    by_status: dict[str, int] = {}
    total_size_bytes: int = 0


class DatasetValidateResponse(BaseModel):
    id: str
    valid: bool
    errors: list[str] = []
    warnings: list[str] = []
    record_count: int = 0
    format_detected: str | None = None
