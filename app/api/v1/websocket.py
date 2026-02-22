import asyncio
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


async def _validate_ws_token(token: str) -> bool:
    """Validate API key from WebSocket query param."""
    if not token or not token.startswith("vault_sk_"):
        return False
    key_hash = hash_api_key(token)
    async with db_module.async_session() as session:
        result = await session.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)  # noqa: E712
        )
        return result.scalar_one_or_none() is not None


@router.websocket("/ws/system")
async def system_metrics_ws(websocket: WebSocket, token: str = Query(default="")):
    """Live system metrics push every 2 seconds."""
    if not await _validate_ws_token(token):
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
