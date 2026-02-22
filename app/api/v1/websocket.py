import asyncio
import platform
import sys
from datetime import datetime, timezone

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

import app.core.database as db_module
from app.core.database import ApiKey
from app.core.security import hash_api_key
from app.schemas.system import GpuDetail, SystemResources
from app.services.monitoring import get_gpu_info
from app.services.system import get_system_resources

router = APIRouter()


async def _validate_ws_token(token: str) -> dict | None:
    """Validate API key from WebSocket query param.

    Returns {"key_prefix": str, "scope": str} on success, None on failure.
    """
    if not token or not token.startswith("vault_sk_"):
        return None
    key_hash = hash_api_key(token)
    async with db_module.async_session() as session:
        result = await session.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)  # noqa: E712
        )
        key_row = result.scalar_one_or_none()
        if key_row is None:
            return None
        return {"key_prefix": key_row.key_prefix, "scope": key_row.scope}


@router.websocket("/ws/system")
async def system_metrics_ws(websocket: WebSocket, token: str = Query(default="")):
    """Live system metrics push every 2 seconds."""
    if await _validate_ws_token(token) is None:
        await websocket.close(code=4001, reason="Invalid or missing API key")
        return

    await websocket.accept()

    try:
        while True:
            resources = await get_system_resources()
            gpu_infos = await get_gpu_info()

            gpus = [
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

            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
                "resources": resources.model_dump(),
                "gpus": [g.model_dump() for g in gpus],
            }

            await websocket.send_json(payload)
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass


def _build_journalctl_cmd(service: str | None, severity: str | None) -> list[str]:
    """Build journalctl command with optional service/severity filters."""
    cmd = ["journalctl", "--follow", "--output=json", "-n", "50"]
    if service:
        # Map friendly names to systemd unit names
        unit_map = {
            "vllm": "vault-vllm",
            "api-gateway": "vault-backend",
            "prometheus": "prometheus",
            "grafana": "grafana-server",
            "caddy": "caddy",
        }
        unit = unit_map.get(service, service)
        cmd.extend(["-u", unit])
    if severity:
        # Map severity to journalctl priority levels
        priority_map = {
            "error": "3",     # err
            "warning": "4",   # warning
            "info": "6",      # info
            "debug": "7",     # debug
        }
        priority = priority_map.get(severity.lower(), "6")
        cmd.extend(["-p", f"0..{priority}"])
    return cmd


def _parse_journal_entry(line: str) -> dict | None:
    """Parse a journalctl JSON line into a log entry dict."""
    import json

    try:
        entry = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    # Map journalctl priority to severity string
    priority = int(entry.get("PRIORITY", 6))
    severity_map = {0: "critical", 1: "critical", 2: "critical", 3: "error", 4: "warning", 5: "info", 6: "info", 7: "debug"}
    severity = severity_map.get(priority, "info")

    # Extract timestamp — journalctl uses __REALTIME_TIMESTAMP (microseconds since epoch)
    ts_usec = entry.get("__REALTIME_TIMESTAMP")
    if ts_usec:
        try:
            ts = datetime.fromtimestamp(int(ts_usec) / 1_000_000, tz=timezone.utc).isoformat() + "Z"
        except (ValueError, OSError):
            ts = datetime.now(timezone.utc).isoformat() + "Z"
    else:
        ts = datetime.now(timezone.utc).isoformat() + "Z"

    service = entry.get("_SYSTEMD_UNIT", entry.get("SYSLOG_IDENTIFIER", "unknown"))
    # Strip .service suffix for cleaner display
    if service.endswith(".service"):
        service = service[:-8]

    message = entry.get("MESSAGE", "")

    return {
        "type": "log",
        "entry": {
            "timestamp": ts,
            "service": service,
            "severity": severity,
            "message": message,
        },
    }


@router.websocket("/ws/logs")
async def live_logs_ws(
    websocket: WebSocket,
    token: str = Query(default=""),
    service: str = Query(default=""),
    severity: str = Query(default=""),
):
    """Live log streaming via journalctl --follow. Admin-only."""
    auth = await _validate_ws_token(token)
    if auth is None:
        await websocket.close(code=4001, reason="Invalid or missing API key")
        return

    if auth["scope"] != "admin":
        await websocket.close(code=4003, reason="Admin scope required")
        return

    # journalctl only available on Linux with systemd
    if platform.system() != "Linux":
        await websocket.accept()
        await websocket.send_json({"type": "info", "message": "Live logs unavailable — not running on Linux/systemd"})
        await websocket.close(code=1000)
        return

    await websocket.accept()

    cmd = _build_journalctl_cmd(service or None, severity or None)
    proc = None

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        async def read_and_send():
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                parsed = _parse_journal_entry(line.decode("utf-8", errors="replace").strip())
                if parsed:
                    await websocket.send_json(parsed)

        async def wait_for_disconnect():
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass

        # Run both concurrently — whichever finishes first cancels the other
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(read_and_send()),
                asyncio.create_task(wait_for_disconnect()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        if proc is not None:
            try:
                proc.terminate()
                await proc.wait()
            except ProcessLookupError:
                pass
