from datetime import datetime

from pydantic import BaseModel


# ── 11.5: Data Export ────────────────────────────────────────────────────────


class ExportedApiKey(BaseModel):
    id: int
    key_prefix: str
    label: str
    scope: str
    is_active: bool
    created_at: str
    last_used_at: str | None = None


class ExportedMessage(BaseModel):
    id: str
    role: str
    content: str
    timestamp: str


class ExportedConversation(BaseModel):
    id: str
    title: str
    model_id: str
    created_at: str
    updated_at: str
    messages: list[ExportedMessage]


class ExportedSystemConfig(BaseModel):
    key: str
    value: str


class DataExportResponse(BaseModel):
    conversations: list[ExportedConversation]
    api_keys: list[ExportedApiKey]
    training_jobs: list[dict]
    system_config: list[ExportedSystemConfig]
    exported_at: str


# ── 11.6: Data Purge ────────────────────────────────────────────────────────


class DataPurgeRequest(BaseModel):
    confirmation: str
    include_api_keys: bool = False


class DeletedCounts(BaseModel):
    conversations: int
    messages: int
    training_jobs: int
    api_keys: int = 0


class DataPurgeResponse(BaseModel):
    status: str
    deleted: DeletedCounts


# ── 11.7: Chat Archive ──────────────────────────────────────────────────────


class ArchiveRequest(BaseModel):
    before: datetime


class ArchiveResponse(BaseModel):
    status: str
    archived_count: int
    message_count: int


# ── 11.4: Factory Reset ─────────────────────────────────────────────────────


class FactoryResetRequest(BaseModel):
    confirmation: str


class FactoryResetResponse(BaseModel):
    status: str
    message: str
    cleared: list[str]


# ── 11.1: Support Bundle ────────────────────────────────────────────────────

# No request model — returns StreamingResponse (application/gzip)


# ── 11.2: Backup ────────────────────────────────────────────────────────────


class BackupRequest(BaseModel):
    output_path: str | None = None
    passphrase: str | None = None


class BackupResponse(BaseModel):
    status: str
    filename: str
    path: str
    size_bytes: int
    encrypted: bool
    checksum_sha256: str


# ── 11.3: Restore ───────────────────────────────────────────────────────────


class RestoreRequest(BaseModel):
    backup_path: str
    passphrase: str | None = None


class RestoreResponse(BaseModel):
    status: str
    tables_restored: list[str]
    message: str
