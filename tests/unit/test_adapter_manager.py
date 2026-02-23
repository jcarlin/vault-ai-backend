"""Unit tests for AdapterManager — adapter CRUD with mock Docker."""

import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Adapter, Base
from app.core.exceptions import NotFoundError, VaultError
from app.services.training.adapter_manager import AdapterManager


@pytest_asyncio.fixture
async def adapter_db():
    """In-memory SQLite engine with adapter table."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


@pytest.fixture
def adapter_dir(tmp_path):
    """Create a mock adapter directory on disk."""
    adapter_path = tmp_path / "test-adapter"
    adapter_path.mkdir()
    (adapter_path / "adapter_config.json").write_text('{"r": 16}')
    (adapter_path / "adapter_model.safetensors").write_bytes(b"fake-weights" * 100)
    return adapter_path


@pytest.fixture
def manager(adapter_db):
    return AdapterManager(session_factory=adapter_db)


class TestAdapterManager:
    @pytest.mark.asyncio
    async def test_list_empty(self, manager):
        result = await manager.list_adapters()
        assert result.total == 0
        assert result.adapters == []

    @pytest.mark.asyncio
    async def test_register_adapter(self, manager, adapter_dir):
        info = await manager.register_adapter(
            name="legal-lora",
            base_model="qwen2.5-32b-awq",
            adapter_type="lora",
            path=str(adapter_dir),
            training_job_id="job-123",
            config={"rank": 16, "alpha": 32},
            metrics={"loss": 0.05},
        )
        assert info.name == "legal-lora"
        assert info.base_model == "qwen2.5-32b-awq"
        assert info.adapter_type == "lora"
        assert info.status == "ready"
        assert info.size_bytes > 0
        assert info.training_job_id == "job-123"

    @pytest.mark.asyncio
    async def test_get_adapter(self, manager, adapter_dir):
        created = await manager.register_adapter(
            name="test-adapter",
            base_model="model-1",
            adapter_type="lora",
            path=str(adapter_dir),
        )
        fetched = await manager.get_adapter(created.id)
        assert fetched.id == created.id
        assert fetched.name == "test-adapter"

    @pytest.mark.asyncio
    async def test_get_adapter_not_found(self, manager):
        with pytest.raises(NotFoundError):
            await manager.get_adapter("nonexistent-id")

    @pytest.mark.asyncio
    async def test_list_after_register(self, manager, adapter_dir):
        await manager.register_adapter(
            name="adapter-1",
            base_model="model-1",
            adapter_type="lora",
            path=str(adapter_dir),
        )
        await manager.register_adapter(
            name="adapter-2",
            base_model="model-2",
            adapter_type="qlora",
            path=str(adapter_dir),
        )
        result = await manager.list_adapters()
        assert result.total == 2

    @pytest.mark.asyncio
    async def test_delete_adapter(self, manager, tmp_path):
        # Create a disposable adapter dir
        adapter_path = tmp_path / "disposable"
        adapter_path.mkdir()
        (adapter_path / "model.safetensors").write_bytes(b"data")

        info = await manager.register_adapter(
            name="to-delete",
            base_model="model-1",
            adapter_type="lora",
            path=str(adapter_path),
        )

        await manager.delete_adapter(info.id)

        # Dir should be removed
        assert not adapter_path.exists()

        # DB record should be gone
        with pytest.raises(NotFoundError):
            await manager.get_adapter(info.id)

    @pytest.mark.asyncio
    async def test_delete_active_adapter_rejected(self, manager, adapter_db, adapter_dir):
        """Cannot delete an active adapter."""
        info = await manager.register_adapter(
            name="active-adapter",
            base_model="model-1",
            adapter_type="lora",
            path=str(adapter_dir),
        )

        # Manually set status to active in DB
        async with adapter_db() as session:
            from sqlalchemy import select
            result = await session.execute(select(Adapter).where(Adapter.id == info.id))
            row = result.scalar_one()
            row.status = "active"
            await session.commit()

        with pytest.raises(VaultError) as exc_info:
            await manager.delete_adapter(info.id)
        assert exc_info.value.status == 409
        assert "active" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_activate_adapter(self, manager, adapter_db, adapter_dir, tmp_path):
        """Activating should update gpu-config and set status to active."""
        from unittest.mock import patch, MagicMock

        info = await manager.register_adapter(
            name="to-activate",
            base_model="model-1",
            adapter_type="lora",
            path=str(adapter_dir),
        )

        mock_manager = MagicMock()
        mock_manager._load_gpu_config.return_value = {"strategy": "replica", "models": []}
        mock_manager._save_gpu_config = MagicMock()

        with patch("app.services.model_manager.ModelManager", return_value=mock_manager):
            result = await manager.activate_adapter(info.id)

        assert result.status == "active"
        assert result.activated_at is not None
        mock_manager._save_gpu_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_deactivate_adapter(self, manager, adapter_db, adapter_dir):
        """Deactivating should remove from gpu-config and set status to ready."""
        from unittest.mock import patch, MagicMock

        info = await manager.register_adapter(
            name="to-deactivate",
            base_model="model-1",
            adapter_type="lora",
            path=str(adapter_dir),
        )

        # First activate
        mock_manager = MagicMock()
        mock_manager._load_gpu_config.return_value = {"strategy": "replica", "models": [], "lora_modules": []}
        mock_manager._save_gpu_config = MagicMock()

        with patch("app.services.model_manager.ModelManager", return_value=mock_manager):
            await manager.activate_adapter(info.id)
            result = await manager.deactivate_adapter(info.id)

        assert result.status == "ready"
        assert result.activated_at is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, manager):
        with pytest.raises(NotFoundError):
            await manager.delete_adapter("fake-id")
