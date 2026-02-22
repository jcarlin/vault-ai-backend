from fastapi import APIRouter, Depends, Query, Request

import app.core.database as db_module
from app.dependencies import require_admin
from app.schemas.services import (
    ExpandedHealthResponse,
    InferenceStatsResponse,
    LogEntry,
    LogResponse,
    ServiceListResponse,
    ServiceStatus,
)
from app.schemas.system import GpuDetail, SystemResources
from app.services.monitoring import get_gpu_info
from app.services.service_manager import ServiceManager
from app.services.system import get_system_resources

router = APIRouter()

_service_manager = ServiceManager()


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


@router.get("/vault/system/health")
async def expanded_health(request: Request) -> ExpandedHealthResponse:
    """Expanded health check covering all managed services."""
    backend = request.app.state.inference_backend
    result = await _service_manager.get_expanded_health(backend=backend)
    return ExpandedHealthResponse(
        status=result["status"],
        services=[ServiceStatus(**s) for s in result["services"]],
        timestamp=result["timestamp"],
    )


@router.get("/vault/system/inference")
async def inference_stats() -> InferenceStatsResponse:
    """Inference request stats from the last 5 minutes."""
    stats = await _service_manager.get_inference_stats(db_module.async_session)
    return InferenceStatsResponse(**stats)


@router.get("/vault/system/services", dependencies=[Depends(require_admin)])
async def list_services() -> ServiceListResponse:
    """List all managed services and their status."""
    services = await _service_manager.list_services()
    return ServiceListResponse(services=[ServiceStatus(**s) for s in services])


@router.post(
    "/vault/system/services/{service_name}/restart",
    status_code=202,
    dependencies=[Depends(require_admin)],
)
async def restart_service(service_name: str) -> dict:
    """Restart a managed service. Refuses self-restart (vault-api)."""
    result = await _service_manager.restart_service(service_name)
    return result


@router.get("/vault/system/logs", dependencies=[Depends(require_admin)])
async def system_logs(
    service: str | None = None,
    severity: str | None = None,
    since: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> LogResponse:
    """Get system logs from journalctl."""
    entries, total = await _service_manager.get_logs(
        service=service, severity=severity, since=since, limit=limit, offset=offset
    )
    return LogResponse(
        entries=[LogEntry(**e) for e in entries],
        total=total,
        limit=limit,
        offset=offset,
    )
