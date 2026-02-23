from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

import app.core.database as db_module
from app.services.service_manager import ServiceManager
from app.services.uptime import get_all_availability, get_api_uptime_seconds, get_os_uptime_seconds

router = APIRouter()

_service_manager = ServiceManager()


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics(request: Request) -> PlainTextResponse:
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
        "# HELP vault_os_uptime_seconds OS uptime in seconds",
        "# TYPE vault_os_uptime_seconds gauge",
        f"vault_os_uptime_seconds {get_os_uptime_seconds()}",
        "",
        "# HELP vault_api_uptime_seconds API process uptime in seconds",
        "# TYPE vault_api_uptime_seconds gauge",
        f"vault_api_uptime_seconds {get_api_uptime_seconds()}",
        "",
    ]

    # Per-service up/down status from uptime monitor
    monitor = getattr(request.app.state, "uptime_monitor", None)
    if monitor:
        lines.extend([
            "# HELP vault_service_up Whether a managed service is up (1) or down (0)",
            "# TYPE vault_service_up gauge",
        ])
        for svc, state in sorted(monitor.last_state.items()):
            val = 1 if state == "up" else 0
            lines.append(f'vault_service_up{{service="{svc}"}} {val}')
        lines.append("")

        # Per-service 24h availability
        try:
            avail = await get_all_availability(db_module.async_session, 24)
            lines.extend([
                "# HELP vault_service_availability_24h Service availability percentage over 24h",
                "# TYPE vault_service_availability_24h gauge",
            ])
            for svc, pct in sorted(avail.items()):
                lines.append(f'vault_service_availability_24h{{service="{svc}"}} {pct}')
            lines.append("")
        except Exception:
            pass

    return PlainTextResponse("\n".join(lines), media_type="text/plain")
