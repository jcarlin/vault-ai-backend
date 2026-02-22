from pydantic import BaseModel


class ServiceStatus(BaseModel):
    name: str
    status: str  # "running", "stopped", "unavailable"
    uptime_seconds: int | None = None


class ServiceListResponse(BaseModel):
    services: list[ServiceStatus]


class LogEntry(BaseModel):
    timestamp: str
    service: str
    severity: str
    message: str


class LogResponse(BaseModel):
    entries: list[LogEntry]
    total: int
    limit: int
    offset: int


class InferenceStatsResponse(BaseModel):
    requests_per_minute: float
    avg_latency_ms: float
    tokens_per_second: float
    active_requests: int
    window_seconds: int = 300


class ExpandedHealthResponse(BaseModel):
    status: str  # "healthy", "degraded", "unhealthy"
    services: list[ServiceStatus]
    timestamp: str
