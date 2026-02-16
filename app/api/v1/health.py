import time

from fastapi import APIRouter, Depends

from app.dependencies import get_inference_backend
from app.schemas.health import HealthResponse
from app.services.inference.base import InferenceBackend
from app.services.monitoring import get_gpu_info

router = APIRouter()

_start_time = time.monotonic()


@router.get("/vault/health")
async def health_check(
    backend: InferenceBackend = Depends(get_inference_backend),
) -> HealthResponse:
    """System health check — no auth required."""
    vllm_ok = await backend.health_check()
    gpus = await get_gpu_info()

    status = "ok" if vllm_ok else "degraded"
    vllm_status = "connected" if vllm_ok else "disconnected"

    return HealthResponse(
        status=status,
        vllm_status=vllm_status,
        gpu_count=len(gpus),
        gpus=gpus,
        uptime_seconds=round(time.monotonic() - _start_time, 1),
    )
