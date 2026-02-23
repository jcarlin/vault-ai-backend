"""LoRA adapter management endpoints (Epic 16)."""

from fastapi import APIRouter

import app.core.database as db_module
from app.schemas.training import AdapterInfo, AdapterList
from app.services.training.adapter_manager import AdapterManager

router = APIRouter()


def _get_manager() -> AdapterManager:
    return AdapterManager(session_factory=db_module.async_session)


@router.get("/vault/training/adapters", response_model=AdapterList)
async def list_adapters():
    """List all registered LoRA adapters."""
    manager = _get_manager()
    return await manager.list_adapters()


@router.get("/vault/training/adapters/{adapter_id}", response_model=AdapterInfo)
async def get_adapter(adapter_id: str):
    """Get adapter details."""
    manager = _get_manager()
    return await manager.get_adapter(adapter_id)


@router.post("/vault/training/adapters/{adapter_id}/activate", response_model=AdapterInfo)
async def activate_adapter(adapter_id: str):
    """Load adapter for inference via vLLM. Admin only."""
    manager = _get_manager()
    return await manager.activate_adapter(adapter_id)


@router.post("/vault/training/adapters/{adapter_id}/deactivate", response_model=AdapterInfo)
async def deactivate_adapter(adapter_id: str):
    """Remove adapter from inference. Admin only."""
    manager = _get_manager()
    return await manager.deactivate_adapter(adapter_id)


@router.delete("/vault/training/adapters/{adapter_id}", status_code=204)
async def delete_adapter(adapter_id: str):
    """Delete adapter from disk. Admin only. Refuses if active."""
    manager = _get_manager()
    await manager.delete_adapter(adapter_id)
