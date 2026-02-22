from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

import app.core.database as db_module
from app.services.service_manager import ServiceManager

router = APIRouter()

_service_manager = ServiceManager()


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics() -> PlainTextResponse:
    """Prometheus exposition format metrics — no auth required."""
    stats = await _service_manager.get_inference_stats(db_module.async_session)

    lines = [
        "# HELP vault_inference_requests_per_minute Inference requests per minute (5m window)",
        "# TYPE vault_inference_requests_per_minute gauge",
        f'vault_inference_requests_per_minute {stats["requests_per_minute"]}',
        "",
        "# HELP vault_inference_avg_latency_ms Average inference latency in milliseconds (5m window)",
        "# TYPE vault_inference_avg_latency_ms gauge",
        f'vault_inference_avg_latency_ms {stats["avg_latency_ms"]}',
        "",
        "# HELP vault_inference_tokens_per_second Tokens generated per second (5m window)",
        "# TYPE vault_inference_tokens_per_second gauge",
        f'vault_inference_tokens_per_second {stats["tokens_per_second"]}',
        "",
        "# HELP vault_inference_active_requests Currently active inference requests",
        "# TYPE vault_inference_active_requests gauge",
        f'vault_inference_active_requests {stats["active_requests"]}',
        "",
    ]

    return PlainTextResponse("\n".join(lines), media_type="text/plain")
