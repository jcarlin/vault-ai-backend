from pydantic import BaseModel


class SystemResources(BaseModel):
    cpu_usage_pct: float
    cpu_count: int
    ram_total_mb: int
    ram_used_mb: int
    ram_usage_pct: float
    disk_total_gb: float
    disk_used_gb: float
    disk_usage_pct: float
    network_in_bytes: int
    network_out_bytes: int
    temperature_celsius: float | None = None
    os_uptime_seconds: float | None = None


class GpuDetail(BaseModel):
    index: int
    name: str
    memory_total_mb: int
    memory_used_mb: int
    utilization_pct: float
    temperature_celsius: float | None = None
    power_draw_watts: float | None = None
