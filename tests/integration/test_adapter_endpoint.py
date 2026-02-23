"""Integration tests for adapter management endpoints (Epic 16)."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import Adapter


@pytest.fixture
def adapter_fixture(db_engine, tmp_path):
    """Helper to create a test adapter in the DB."""

    async def _create(name="test-lora", base_model="qwen2.5-32b-awq", status="ready"):
        import uuid

        adapter_path = tmp_path / name
        adapter_path.mkdir(exist_ok=True)
        (adapter_path / "adapter_config.json").write_text('{"r": 16}')

        adapter_id = str(uuid.uuid4())
        session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            row = Adapter(
                id=adapter_id,
                name=name,
                base_model=base_model,
                adapter_type="lora",
                status=status,
                path=str(adapter_path),
                size_bytes=1024,
            )
            session.add(row)
            await session.commit()
        return adapter_id

    return _create


class TestAdapterEndpoints:
    @pytest.mark.asyncio
    async def test_list_adapters_empty(self, auth_client):
        resp = await auth_client.get("/vault/training/adapters")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["adapters"] == []

    @pytest.mark.asyncio
    async def test_list_adapters_with_data(self, auth_client, adapter_fixture):
        await adapter_fixture("adapter-1")
        await adapter_fixture("adapter-2")

        resp = await auth_client.get("/vault/training/adapters")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    @pytest.mark.asyncio
    async def test_get_adapter(self, auth_client, adapter_fixture):
        adapter_id = await adapter_fixture("my-lora")

        resp = await auth_client.get(f"/vault/training/adapters/{adapter_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "my-lora"
        assert data["status"] == "ready"

    @pytest.mark.asyncio
    async def test_get_adapter_not_found(self, auth_client):
        resp = await auth_client.get("/vault/training/adapters/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_adapter(self, auth_client, adapter_fixture):
        adapter_id = await adapter_fixture("to-delete")

        resp = await auth_client.delete(f"/vault/training/adapters/{adapter_id}")
        assert resp.status_code == 204

        # Should be gone
        resp = await auth_client.get(f"/vault/training/adapters/{adapter_id}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_active_adapter_rejected(self, auth_client, adapter_fixture):
        adapter_id = await adapter_fixture("active-adapter", status="active")

        resp = await auth_client.delete(f"/vault/training/adapters/{adapter_id}")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_activate_adapter(self, auth_client, adapter_fixture):
        adapter_id = await adapter_fixture("to-activate", status="ready")

        resp = await auth_client.post(f"/vault/training/adapters/{adapter_id}/activate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "active"

    @pytest.mark.asyncio
    async def test_deactivate_adapter(self, auth_client, adapter_fixture):
        adapter_id = await adapter_fixture("to-deactivate", status="active")

        resp = await auth_client.post(f"/vault/training/adapters/{adapter_id}/deactivate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"

    @pytest.mark.asyncio
    async def test_activate_not_found(self, auth_client):
        resp = await auth_client.post("/vault/training/adapters/nonexistent/activate")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_deactivate_not_found(self, auth_client):
        resp = await auth_client.post("/vault/training/adapters/nonexistent/deactivate")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_not_found(self, auth_client):
        resp = await auth_client.delete("/vault/training/adapters/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_gpu_allocation_endpoint(self, auth_client):
        resp = await auth_client.get("/vault/training/gpu-allocation")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "gpu_index" in data[0]

    @pytest.mark.asyncio
    async def test_401_without_auth(self, anon_client):
        resp = await anon_client.get("/vault/training/adapters")
        assert resp.status_code == 401


class TestDatasetValidation:
    @pytest.mark.asyncio
    async def test_validate_valid_chat_dataset(self, auth_client, tmp_path):
        """Valid chat JSONL should pass validation."""
        import shutil
        from pathlib import Path

        fixtures = Path(__file__).parent.parent / "fixtures" / "training"
        dataset_path = tmp_path / "chat.jsonl"
        shutil.copy(fixtures / "sample_chat.jsonl", dataset_path)

        resp = await auth_client.post(
            "/vault/training/validate",
            json={"path": str(dataset_path)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["format"] == "chat"
        assert data["record_count"] == 10

    @pytest.mark.asyncio
    async def test_validate_invalid_dataset(self, auth_client, tmp_path):
        """Invalid JSONL should return findings."""
        import shutil
        from pathlib import Path

        fixtures = Path(__file__).parent.parent / "fixtures" / "training"
        dataset_path = tmp_path / "invalid.jsonl"
        shutil.copy(fixtures / "invalid.jsonl", dataset_path)

        resp = await auth_client.post(
            "/vault/training/validate",
            json={"path": str(dataset_path)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert len(data["findings"]) > 0

    @pytest.mark.asyncio
    async def test_validate_nonexistent_file(self, auth_client):
        resp = await auth_client.post(
            "/vault/training/validate",
            json={"path": "/nonexistent/file.jsonl"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_validate_wrong_extension(self, auth_client, tmp_path):
        """Non-.jsonl files should be rejected."""
        bad_file = tmp_path / "data.csv"
        bad_file.write_text("a,b,c\n1,2,3\n")

        resp = await auth_client.post(
            "/vault/training/validate",
            json={"path": str(bad_file)},
        )
        assert resp.status_code == 400
