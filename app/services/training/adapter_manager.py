"""LoRA adapter management — register, activate on vLLM, deactivate, delete."""

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.core.database import Adapter, async_session as default_session_factory
from app.core.exceptions import NotFoundError, VaultError
from app.schemas.training import AdapterInfo, AdapterList

logger = structlog.get_logger()


def _row_to_info(row: Adapter) -> AdapterInfo:
    """Convert an Adapter ORM row to an AdapterInfo schema."""
    return AdapterInfo(
        id=row.id,
        name=row.name,
        base_model=row.base_model,
        adapter_type=row.adapter_type,
        status=row.status,
        path=row.path,
        training_job_id=row.training_job_id,
        config=json.loads(row.config_json) if row.config_json else None,
        metrics=json.loads(row.metrics_json) if row.metrics_json else None,
        size_bytes=row.size_bytes,
        version=row.version,
        created_at=row.created_at.isoformat() + "Z" if row.created_at else "",
        activated_at=row.activated_at.isoformat() + "Z" if row.activated_at else None,
    )


class AdapterManager:
    """Manages LoRA adapter lifecycle — register, activate/deactivate on vLLM, delete."""

    def __init__(self, session_factory: async_sessionmaker | None = None):
        self._session_factory = session_factory or default_session_factory
        self._adapters_dir = Path(settings.vault_adapters_dir)

    async def list_adapters(self) -> AdapterList:
        """List all registered adapters."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Adapter).order_by(Adapter.created_at.desc())
            )
            rows = list(result.scalars().all())
            return AdapterList(
                adapters=[_row_to_info(r) for r in rows],
                total=len(rows),
            )

    async def get_adapter(self, adapter_id: str) -> AdapterInfo:
        """Get a single adapter by ID."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Adapter).where(Adapter.id == adapter_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Adapter '{adapter_id}' not found.")
            return _row_to_info(row)

    async def register_adapter(
        self,
        name: str,
        base_model: str,
        adapter_type: str,
        path: str,
        training_job_id: str | None = None,
        config: dict | None = None,
        metrics: dict | None = None,
    ) -> AdapterInfo:
        """Register a new adapter (called after training completes)."""
        adapter_path = Path(path)
        size_bytes = sum(f.stat().st_size for f in adapter_path.rglob("*") if f.is_file()) if adapter_path.exists() else 0

        adapter_id = str(uuid.uuid4())
        row = Adapter(
            id=adapter_id,
            name=name,
            base_model=base_model,
            adapter_type=adapter_type,
            status="ready",
            path=str(adapter_path),
            training_job_id=training_job_id,
            config_json=json.dumps(config) if config else None,
            metrics_json=json.dumps(metrics) if metrics else None,
            size_bytes=size_bytes,
        )

        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            logger.info("adapter_registered", id=adapter_id, name=name, base_model=base_model)
            return _row_to_info(row)

    async def activate_adapter(self, adapter_id: str, docker_client=None) -> AdapterInfo:
        """Activate an adapter for inference via vLLM --lora-modules."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Adapter).where(Adapter.id == adapter_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Adapter '{adapter_id}' not found.")

            if row.status == "active":
                return _row_to_info(row)

            # Update gpu-config.yaml with lora_modules entry
            from app.services.model_manager import ModelManager
            manager = ModelManager()
            gpu_config = manager._load_gpu_config()

            if "lora_modules" not in gpu_config:
                gpu_config["lora_modules"] = []

            # Add this adapter
            lora_entry = {"name": row.name, "path": row.path, "base_model": row.base_model}
            # Remove any existing entry with same name
            gpu_config["lora_modules"] = [
                m for m in gpu_config["lora_modules"] if m.get("name") != row.name
            ]
            gpu_config["lora_modules"].append(lora_entry)
            manager._save_gpu_config(gpu_config)

            # Restart vLLM container if Docker client available
            if docker_client:
                import asyncio
                await asyncio.to_thread(manager._restart_vllm_container, docker_client)

            row.status = "active"
            row.activated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(row)

            logger.info("adapter_activated", id=adapter_id, name=row.name)
            return _row_to_info(row)

    async def deactivate_adapter(self, adapter_id: str, docker_client=None) -> AdapterInfo:
        """Remove adapter from vLLM inference."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Adapter).where(Adapter.id == adapter_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Adapter '{adapter_id}' not found.")

            if row.status != "active":
                return _row_to_info(row)

            # Remove from gpu-config.yaml
            from app.services.model_manager import ModelManager
            manager = ModelManager()
            gpu_config = manager._load_gpu_config()

            if "lora_modules" in gpu_config:
                gpu_config["lora_modules"] = [
                    m for m in gpu_config["lora_modules"] if m.get("name") != row.name
                ]
                manager._save_gpu_config(gpu_config)

            # Restart vLLM
            if docker_client:
                import asyncio
                await asyncio.to_thread(manager._restart_vllm_container, docker_client)

            row.status = "ready"
            row.activated_at = None
            await session.commit()
            await session.refresh(row)

            logger.info("adapter_deactivated", id=adapter_id, name=row.name)
            return _row_to_info(row)

    async def delete_adapter(self, adapter_id: str) -> None:
        """Delete an adapter from disk and DB. Refuses if active."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Adapter).where(Adapter.id == adapter_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Adapter '{adapter_id}' not found.")

            if row.status == "active":
                raise VaultError(
                    code="conflict",
                    message=f"Adapter '{row.name}' is currently active. Deactivate it first.",
                    status=409,
                )

            # Remove from disk
            adapter_path = Path(row.path)
            if adapter_path.exists():
                shutil.rmtree(adapter_path)

            await session.delete(row)
            await session.commit()
            logger.info("adapter_deleted", id=adapter_id, name=row.name)
