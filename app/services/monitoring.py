import structlog

from app.schemas.health import GpuInfo

logger = structlog.get_logger()


async def get_gpu_info() -> list[GpuInfo]:
    """Detect GPUs via py3nvml. Returns empty list if no NVIDIA GPU or driver."""
    try:
        from py3nvml.py3nvml import (
            nvmlDeviceGetCount,
            nvmlDeviceGetHandleByIndex,
            nvmlDeviceGetMemoryInfo,
            nvmlDeviceGetName,
            nvmlDeviceGetUtilizationRates,
            nvmlInit,
            nvmlShutdown,
        )

        nvmlInit()
        count = nvmlDeviceGetCount()
        gpus = []
        for i in range(count):
            handle = nvmlDeviceGetHandleByIndex(i)
            name = nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8")
            mem = nvmlDeviceGetMemoryInfo(handle)
            util = nvmlDeviceGetUtilizationRates(handle)
            gpus.append(
                GpuInfo(
                    index=i,
                    name=name,
                    memory_total_mb=mem.total // (1024 * 1024),
                    memory_used_mb=mem.used // (1024 * 1024),
                    utilization_pct=float(util.gpu),
                )
            )
        nvmlShutdown()
        return gpus
    except Exception as e:
        logger.debug("gpu_detection_unavailable", reason=str(e))
        return []
