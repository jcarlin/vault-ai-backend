from pydantic import BaseModel


class ServiceAvailability(BaseModel):
    service_name: str
    availability_24h: float
    availability_7d: float
    availability_30d: float
    current_status: str  # "up", "down", or "unknown"


class UptimeSummaryResponse(BaseModel):
    os_uptime_seconds: float
    api_uptime_seconds: float
    services: list[ServiceAvailability]
    incidents_24h: int


class DowntimeEvent(BaseModel):
    id: int
    service_name: str
    event_type: str
    timestamp: str
    duration_seconds: float | None = None
    details: str | None = None


class UptimeEventsResponse(BaseModel):
    events: list[DowntimeEvent]
    total: int
    limit: int
    offset: int


class AvailabilityResponse(BaseModel):
    window_hours: int
    services: dict[str, float]  # service_name → availability %
