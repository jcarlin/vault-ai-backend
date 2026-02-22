import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import ApiKey
from app.core.security import generate_api_key, hash_api_key, get_key_prefix


@pytest_asyncio.fixture
async def model_app(app_with_db, tmp_path):
    """App with model_management router and temp manifest/models dir."""
    from app.api.v1.model_management import router as model_mgmt_router
    from app.api.v1.model_management import _manager

    # Only add if not already registered
    existing = {r.path for r in app_with_db.routes}
    if "/vault/models" not in existing:
        app_with_db.include_router(model_mgmt_router)

    # Point manager at temp directories
    models_dir = tmp_path / "models"
    models_dir.mkdir()

    manifest_path = tmp_path / "models.json"
    manifest_path.write_text(json.dumps({
        "models": [
            {
                "id": "qwen2.5-32b-awq",
                "name": "Qwen 2.5 32B (AWQ Quantized)",
                "parameters": "32B",
                "quantization": "AWQ 4-bit",
                "context_window": 32768,
                "vram_required_gb": 20,
                "description": "Best balance of capability and speed.",
                "path": str(models_dir / "qwen2.5-32b-awq"),
            },
            {
                "id": "llama-3.3-8b-q4",
                "name": "Llama 3.3 8B (4-bit)",
                "parameters": "8B",
                "quantization": "AWQ 4-bit",
                "context_window": 131072,
                "vram_required_gb": 6,
                "description": "Fast model for simple tasks.",
                "path": str(models_dir / "llama-3.3-8b-q4"),
            },
        ]
    }))

    gpu_config_path = tmp_path / "gpu-config.yaml"

    _manager._models_dir = models_dir
    _manager._manifest_path = manifest_path
    _manager._gpu_config_path = gpu_config_path

    yield app_with_db


@pytest_asyncio.fixture
async def admin_client(model_app, db_engine):
    """Authenticated client with admin-scoped API key."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="model-mgmt-admin-test",
            scope="admin",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=model_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def user_client(model_app, db_engine):
    """Authenticated client with user-scoped (non-admin) API key."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="model-mgmt-user-test",
            scope="user",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=model_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


class TestListModels:
    @pytest.mark.asyncio
    async def test_list_models(self, admin_client):
        response = await admin_client.get("/vault/models")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        ids = [m["id"] for m in data]
        assert "qwen2.5-32b-awq" in ids
        assert "llama-3.3-8b-q4" in ids

    @pytest.mark.asyncio
    async def test_list_models_user_scope_allowed(self, user_client):
        response = await user_client.get("/vault/models")
        assert response.status_code == 200


class TestGetModelDetail:
    @pytest.mark.asyncio
    async def test_get_existing_model(self, admin_client):
        response = await admin_client.get("/vault/models/qwen2.5-32b-awq")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "qwen2.5-32b-awq"
        assert data["name"] == "Qwen 2.5 32B (AWQ Quantized)"
        assert data["parameters"] == "32B"

    @pytest.mark.asyncio
    async def test_get_nonexistent_model(self, admin_client):
        response = await admin_client.get("/vault/models/nonexistent-model")
        assert response.status_code == 404


class TestLoadModel:
    @pytest.mark.asyncio
    async def test_load_model(self, admin_client):
        response = await admin_client.post(
            "/vault/models/qwen2.5-32b-awq/load",
            json={"gpu_index": 0},
        )
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "loading"
        assert data["model_id"] == "qwen2.5-32b-awq"

    @pytest.mark.asyncio
    async def test_load_nonexistent_model(self, admin_client):
        response = await admin_client.post(
            "/vault/models/nonexistent/load",
            json={"gpu_index": 0},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_load_model_no_body(self, admin_client):
        """Load with no request body should default to gpu_index=0."""
        response = await admin_client.post("/vault/models/qwen2.5-32b-awq/load")
        assert response.status_code == 202

    @pytest.mark.asyncio
    async def test_load_requires_admin(self, user_client):
        response = await user_client.post(
            "/vault/models/qwen2.5-32b-awq/load",
            json={"gpu_index": 0},
        )
        assert response.status_code == 403


class TestUnloadModel:
    @pytest.mark.asyncio
    async def test_unload_model(self, admin_client):
        # Load first
        await admin_client.post("/vault/models/qwen2.5-32b-awq/load", json={"gpu_index": 0})

        # Unload
        response = await admin_client.post("/vault/models/qwen2.5-32b-awq/unload")
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "unloaded"

    @pytest.mark.asyncio
    async def test_unload_requires_admin(self, user_client):
        response = await user_client.post("/vault/models/qwen2.5-32b-awq/unload")
        assert response.status_code == 403


class TestActiveModels:
    @pytest.mark.asyncio
    async def test_active_models_empty(self, admin_client):
        response = await admin_client.get("/vault/models/active")
        assert response.status_code == 200
        data = response.json()
        assert data["models"] == []
        assert data["gpu_allocation"] == []

    @pytest.mark.asyncio
    async def test_active_models_after_load(self, admin_client):
        await admin_client.post("/vault/models/qwen2.5-32b-awq/load", json={"gpu_index": 0})
        response = await admin_client.get("/vault/models/active")
        assert response.status_code == 200
        data = response.json()
        assert len(data["models"]) == 1
        assert data["models"][0]["id"] == "qwen2.5-32b-awq"

    @pytest.mark.asyncio
    async def test_active_models_user_scope_allowed(self, user_client):
        response = await user_client.get("/vault/models/active")
        assert response.status_code == 200


class TestImportModel:
    @pytest.mark.asyncio
    async def test_import_valid_model(self, admin_client, tmp_path):
        source = tmp_path / "import-source"
        source.mkdir()
        (source / "config.json").write_text('{"model_type": "llama"}')
        (source / "model.safetensors").write_bytes(b"fake weights")

        response = await admin_client.post(
            "/vault/models/import",
            json={"source_path": str(source), "model_id": "imported-model"},
        )
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "imported"
        assert data["model_id"] == "imported-model"

    @pytest.mark.asyncio
    async def test_import_with_pickle_rejected(self, admin_client, tmp_path):
        source = tmp_path / "pickle-source"
        source.mkdir()
        (source / "config.json").write_text('{}')
        (source / "evil.pkl").write_bytes(b"malicious")

        response = await admin_client.post(
            "/vault/models/import",
            json={"source_path": str(source), "model_id": "pickle-model"},
        )
        assert response.status_code == 400
        assert "Dangerous file" in response.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_import_requires_admin(self, user_client, tmp_path):
        response = await user_client.post(
            "/vault/models/import",
            json={"source_path": str(tmp_path), "model_id": "test"},
        )
        assert response.status_code == 403


class TestDeleteModel:
    @pytest.mark.asyncio
    async def test_delete_available_model(self, admin_client, tmp_path):
        from app.api.v1.model_management import _manager

        # Create model dir on disk
        model_dir = _manager._models_dir / "llama-3.3-8b-q4"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "config.json").write_text('{}')

        response = await admin_client.delete("/vault/models/llama-3.3-8b-q4")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"

        # Verify model is gone from list
        list_response = await admin_client.get("/vault/models")
        ids = [m["id"] for m in list_response.json()]
        assert "llama-3.3-8b-q4" not in ids

    @pytest.mark.asyncio
    async def test_delete_loaded_model_rejected(self, admin_client):
        """Deleting a model that the backend reports as running should 409."""
        # qwen2.5-32b-awq is "running" in fake_vllm's /v1/models response
        response = await admin_client.delete("/vault/models/qwen2.5-32b-awq")
        assert response.status_code == 409
        assert "currently loaded" in response.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_delete_nonexistent_model(self, admin_client):
        response = await admin_client.delete("/vault/models/nonexistent")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_requires_admin(self, user_client):
        response = await user_client.delete("/vault/models/llama-3.3-8b-q4")
        assert response.status_code == 403
