from fastapi import APIRouter

from app.schemas.system import GpuDetail, SystemResources
from app.services.monitoring import get_gpu_info
from app.services.system import get_system_resources

router = APIRouter()


@router.get("/vault/system/resources")
async def system_resources() -> SystemResources:
    """CPU, RAM, disk, network, and temperature metrics."""
    return await get_system_resources()


@router.get("/vault/system/gpu")
async def system_gpu() -> list[GpuDetail]:
    """Per-GPU metrics. Returns empty list if no NVIDIA GPUs detected."""
    gpu_infos = await get_gpu_info()
    return [
        GpuDetail(
            index=g.index,
            name=g.name,
            memory_total_mb=g.memory_total_mb,
            memory_used_mb=g.memory_used_mb,
            utilization_pct=g.utilization_pct,
            temperature_celsius=None,
            power_draw_watts=None,
        )
        for g in gpu_infos
    ]
