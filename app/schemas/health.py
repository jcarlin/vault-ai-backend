from pydantic import BaseModel


class GpuInfo(BaseModel):
    index: int
    name: str
    memory_total_mb: int
    memory_used_mb: int
    utilization_pct: float


class HealthResponse(BaseModel):
    status: str  # "ok" or "degraded"
    vllm_status: str  # "connected" or "disconnected"
    gpu_count: int = 0
    gpus: list[GpuInfo] = []
    uptime_seconds: float = 0.0
    version: str = "0.1.0"
