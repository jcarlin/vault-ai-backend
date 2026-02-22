import json

import pytest
import pytest_asyncio

from app.services.model_manager import ModelManager


@pytest_asyncio.fixture
async def manager(tmp_path):
    """ModelManager wired to temp directories."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()

    manifest_path = tmp_path / "models.json"
    manifest_path.write_text(json.dumps({
        "models": [
            {
                "id": "test-model-7b",
                "name": "Test Model 7B",
                "parameters": "7B",
                "quantization": "AWQ 4-bit",
                "context_window": 4096,
                "vram_required_gb": 5.0,
                "path": str(models_dir / "test-model-7b"),
            }
        ]
    }))

    gpu_config_path = tmp_path / "gpu-config.yaml"

    mgr = ModelManager()
    mgr._models_dir = models_dir
    mgr._manifest_path = manifest_path
    mgr._gpu_config_path = gpu_config_path
    return mgr


class TestListModels:
    @pytest.mark.asyncio
    async def test_list_returns_manifest_models(self, manager):
        models = await manager.list_models()
        assert len(models) == 1
        assert models[0]["id"] == "test-model-7b"
        assert models[0]["status"] == "available"

    @pytest.mark.asyncio
    async def test_list_empty_manifest(self, tmp_path):
        mgr = ModelManager()
        mgr._manifest_path = tmp_path / "nonexistent.json"
        mgr._models_dir = tmp_path
        mgr._gpu_config_path = tmp_path / "gpu.yaml"
        models = await mgr.list_models()
        assert models == []


class TestGetModel:
    @pytest.mark.asyncio
    async def test_get_existing_model(self, manager):
        model = await manager.get_model("test-model-7b")
        assert model["id"] == "test-model-7b"
        assert model["name"] == "Test Model 7B"

    @pytest.mark.asyncio
    async def test_get_nonexistent_raises_404(self, manager):
        from app.core.exceptions import NotFoundError
        with pytest.raises(NotFoundError):
            await manager.get_model("nonexistent")


class TestLoadModel:
    @pytest.mark.asyncio
    async def test_load_existing_model(self, manager):
        result = await manager.load_model("test-model-7b", gpu_index=0)
        assert result["status"] == "loading"
        assert result["model_id"] == "test-model-7b"

        # Verify gpu-config was written
        gpu_config = manager._load_gpu_config()
        assert len(gpu_config["models"]) == 1
        assert gpu_config["models"][0]["id"] == "test-model-7b"
        assert gpu_config["models"][0]["gpus"] == [0]

    @pytest.mark.asyncio
    async def test_load_nonexistent_raises_404(self, manager):
        from app.core.exceptions import NotFoundError
        with pytest.raises(NotFoundError):
            await manager.load_model("nonexistent")

    @pytest.mark.asyncio
    async def test_load_with_docker_client(self, manager):
        from tests.mocks.fake_docker import FakeDockerClient
        docker = FakeDockerClient()
        result = await manager.load_model("test-model-7b", docker_client=docker)
        assert result["status"] == "loading"


class TestUnloadModel:
    @pytest.mark.asyncio
    async def test_unload_removes_from_gpu_config(self, manager):
        # Load first
        await manager.load_model("test-model-7b")
        gpu_config = manager._load_gpu_config()
        assert len(gpu_config["models"]) == 1

        # Unload
        result = await manager.unload_model("test-model-7b")
        assert result["status"] == "unloaded"

        gpu_config = manager._load_gpu_config()
        assert len(gpu_config["models"]) == 0


class TestImportModel:
    @pytest.mark.asyncio
    async def test_import_valid_model(self, manager, tmp_path):
        # Create a valid model source directory
        source = tmp_path / "new-model-source"
        source.mkdir()
        (source / "config.json").write_text('{"model_type": "llama"}')
        (source / "model.safetensors").write_bytes(b"fake weights")

        result = await manager.import_model(str(source), model_id="imported-model")
        assert result["status"] == "imported"
        assert result["model_id"] == "imported-model"

        # Verify manifest updated
        manifest = manager._load_manifest()
        ids = [m["id"] for m in manifest]
        assert "imported-model" in ids

    @pytest.mark.asyncio
    async def test_import_uses_dir_name_as_id(self, manager, tmp_path):
        source = tmp_path / "my-cool-model"
        source.mkdir()
        (source / "config.json").write_text('{}')

        result = await manager.import_model(str(source))
        assert result["model_id"] == "my-cool-model"

    @pytest.mark.asyncio
    async def test_import_rejects_pickle_files(self, manager, tmp_path):
        from app.core.exceptions import VaultError

        source = tmp_path / "bad-model"
        source.mkdir()
        (source / "config.json").write_text('{}')
        (source / "weights.pkl").write_bytes(b"malicious")

        with pytest.raises(VaultError) as exc_info:
            await manager.import_model(str(source))
        assert exc_info.value.status == 400
        assert "Dangerous file" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_import_rejects_bin_files(self, manager, tmp_path):
        from app.core.exceptions import VaultError

        source = tmp_path / "bin-model"
        source.mkdir()
        (source / "config.json").write_text('{}')
        (source / "model.bin").write_bytes(b"binary")

        with pytest.raises(VaultError) as exc_info:
            await manager.import_model(str(source))
        assert exc_info.value.status == 400

    @pytest.mark.asyncio
    async def test_import_rejects_no_config_or_safetensors(self, manager, tmp_path):
        from app.core.exceptions import VaultError

        source = tmp_path / "empty-model"
        source.mkdir()
        (source / "readme.txt").write_text("not a model")

        with pytest.raises(VaultError) as exc_info:
            await manager.import_model(str(source))
        assert exc_info.value.status == 400
        assert "config.json" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_import_rejects_nonexistent_path(self, manager, tmp_path):
        from app.core.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            await manager.import_model(str(tmp_path / "does-not-exist"))

    @pytest.mark.asyncio
    async def test_import_rejects_file_not_dir(self, manager, tmp_path):
        from app.core.exceptions import VaultError

        file_path = tmp_path / "afile.txt"
        file_path.write_text("not a dir")

        with pytest.raises(VaultError) as exc_info:
            await manager.import_model(str(file_path))
        assert exc_info.value.status == 400

    @pytest.mark.asyncio
    async def test_import_rejects_duplicate_model_id(self, manager, tmp_path):
        from app.core.exceptions import VaultError

        # Create source with valid model files
        source = tmp_path / "dup-source"
        source.mkdir()
        (source / "config.json").write_text('{}')

        # Create destination that already exists
        dest = manager._models_dir / "test-model-7b"
        dest.mkdir(parents=True)

        with pytest.raises(VaultError) as exc_info:
            await manager.import_model(str(source), model_id="test-model-7b")
        assert exc_info.value.status == 409


class TestDeleteModel:
    @pytest.mark.asyncio
    async def test_delete_available_model(self, manager):
        # Create the model dir on disk
        model_dir = manager._models_dir / "test-model-7b"
        model_dir.mkdir(parents=True)
        (model_dir / "config.json").write_text('{}')

        # Update manifest to include path
        manifest = manager._load_manifest()
        for m in manifest:
            if m["id"] == "test-model-7b":
                m["path"] = str(model_dir)
        manager._save_manifest(manifest)

        result = await manager.delete_model("test-model-7b")
        assert result["status"] == "deleted"

        # Verify removed from manifest
        manifest = manager._load_manifest()
        assert all(m["id"] != "test-model-7b" for m in manifest)

        # Verify removed from disk
        assert not model_dir.exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises_404(self, manager):
        from app.core.exceptions import NotFoundError
        with pytest.raises(NotFoundError):
            await manager.delete_model("nonexistent")


class TestActiveModels:
    @pytest.mark.asyncio
    async def test_active_models_empty(self, manager):
        result = await manager.get_active_models()
        assert result["models"] == []
        assert result["gpu_allocation"] == []

    @pytest.mark.asyncio
    async def test_active_models_after_load(self, manager):
        await manager.load_model("test-model-7b", gpu_index=0)
        result = await manager.get_active_models()
        assert len(result["models"]) == 1
        assert result["models"][0]["id"] == "test-model-7b"
        assert result["models"][0]["status"] == "loaded"
        assert result["models"][0]["gpu_index"] == 0
        assert len(result["gpu_allocation"]) == 1
        assert result["gpu_allocation"][0]["model_id"] == "test-model-7b"
