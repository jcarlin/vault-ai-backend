import asyncio
import json as _json
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
from app.services.service_manager import PRIORITY_TO_SEVERITY, SERVICE_UNIT_MAP
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
        unit = SERVICE_UNIT_MAP.get(service, service)
        cmd.extend(["-u", unit])
    if severity:
        priority_map = {"error": "3", "warning": "4", "info": "6", "debug": "7"}
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
    try:
        priority = int(entry.get("PRIORITY", 6))
    except (ValueError, TypeError):
        priority = 6
    severity = PRIORITY_TO_SEVERITY.get(priority, "info")

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


# ── PTY WebSocket bridge (shared by terminal + python) ──────────────────────


async def _pty_ws_bridge(
    websocket: WebSocket,
    token: str,
    session_id: str,
    get_session_fn,
    label: str,
):
    """Generic PTY ↔ WebSocket bridge. Used by /ws/terminal and /ws/python."""
    auth = await _validate_ws_token(token)
    if auth is None:
        await websocket.close(code=4001, reason="Invalid or missing API key")
        return
    if auth["scope"] != "admin":
        await websocket.close(code=4003, reason="Admin scope required")
        return

    pty_session = get_session_fn(session_id)
    if pty_session is None:
        await websocket.close(code=4004, reason=f"{label} session not found: {session_id}")
        return

    await websocket.accept()

    async def read_pty_and_send():
        """Read from PTY and send to WebSocket."""
        while pty_session.is_alive:
            data = await pty_session.read()
            if data:
                await websocket.send_bytes(data)
            else:
                await asyncio.sleep(0.02)

    async def receive_and_write_pty():
        """Receive from WebSocket and write to PTY."""
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if "bytes" in message and message["bytes"]:
                    pty_session.write(message["bytes"])
                elif "text" in message and message["text"]:
                    text = message["text"]
                    # Check for JSON control messages (resize)
                    try:
                        msg = _json.loads(text)
                        if msg.get("type") == "resize":
                            pty_session.resize(msg.get("cols", 80), msg.get("rows", 24))
                            continue
                    except (ValueError, _json.JSONDecodeError):
                        pass
                    # Regular text input
                    pty_session.write(text.encode("utf-8"))
        except WebSocketDisconnect:
            pass

    try:
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(read_pty_and_send()),
                asyncio.create_task(receive_and_write_pty()),
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


@router.websocket("/ws/training/{job_id}")
async def training_progress_ws(
    websocket: WebSocket,
    job_id: str,
    token: str = Query(default=""),
):
    """Real-time training metrics + log streaming. User-level auth."""
    if await _validate_ws_token(token) is None:
        await websocket.close(code=4001, reason="Invalid or missing API key")
        return

    await websocket.accept()

    try:
        while True:
            # Read progress from the training runner's status.json
            progress_tracker = getattr(websocket.app.state, "progress_tracker", None)
            if progress_tracker is None:
                await websocket.send_json({"type": "error", "message": "Training progress tracker not available"})
                break

            progress = progress_tracker.get_progress(job_id)
            if progress is None:
                await websocket.send_json({"type": "waiting", "message": "No progress data yet"})
            else:
                await websocket.send_json({"type": "progress", "data": progress})

                # If training is done, send final message and close
                if progress.get("state") in ("completed", "failed", "cancelled", "paused"):
                    break

            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass


@router.websocket("/ws/terminal")
async def terminal_ws(
    websocket: WebSocket,
    token: str = Query(default=""),
    session: str = Query(default=""),
):
    """WebSocket bridge to a terminal PTY session. Admin-only."""
    from app.services.devmode_terminal import get_terminal_session

    await _pty_ws_bridge(websocket, token, session, get_terminal_session, "Terminal")


@router.websocket("/ws/python")
async def python_ws(
    websocket: WebSocket,
    token: str = Query(default=""),
    session: str = Query(default=""),
):
    """WebSocket bridge to a Python/IPython PTY session. Admin-only."""
    from app.services.devmode_python import get_python_session

    await _pty_ws_bridge(websocket, token, session, get_python_session, "Python")
