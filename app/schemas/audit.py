from pydantic import BaseModel


class AuditLogEntry(BaseModel):
    id: int
    timestamp: str
    action: str
    method: str | None = None
    path: str | None = None
    user_key_prefix: str | None = None
    model: str | None = None
    status_code: int | None = None
    latency_ms: float | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    details: str | None = None


class AuditLogResponse(BaseModel):
    items: list[AuditLogEntry]
    total: int
    limit: int
    offset: int


class AuditStatsResponse(BaseModel):
    total_requests: int
    total_tokens: int
    avg_latency_ms: float
    requests_by_user: list[dict]
    requests_by_model: list[dict]
    requests_by_endpoint: list[dict]
