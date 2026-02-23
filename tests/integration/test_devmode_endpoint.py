"""Integration tests for devmode endpoints (enable/disable/status/inspect)."""

import json
from pathlib import Path

import pytest


class TestDevModeEnableDisable:
    async def test_enable_devmode(self, auth_client):
        response = await auth_client.post("/vault/admin/devmode/enable", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert isinstance(data["active_sessions"], list)

    async def test_enable_devmode_with_gpu_allocation(self, auth_client):
        response = await auth_client.post(
            "/vault/admin/devmode/enable",
            json={"gpu_allocation": [0, 1]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["gpu_allocation"] == [0, 1]

    async def test_disable_devmode(self, auth_client):
        # Enable first
        await auth_client.post("/vault/admin/devmode/enable", json={})
        # Then disable
        response = await auth_client.post("/vault/admin/devmode/disable")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["active_sessions"] == []

    async def test_get_status(self, auth_client):
        response = await auth_client.get("/vault/admin/devmode/status")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "gpu_allocation" in data
        assert "active_sessions" in data

    async def test_enable_requires_admin(self, anon_client):
        response = await anon_client.post("/vault/admin/devmode/enable", json={})
        assert response.status_code == 401

    async def test_disable_requires_admin(self, anon_client):
        response = await anon_client.post("/vault/admin/devmode/disable")
        assert response.status_code == 401

    async def test_status_requires_admin(self, anon_client):
        response = await anon_client.get("/vault/admin/devmode/status")
        assert response.status_code == 401


class TestModelInspector:
    async def test_inspect_model_not_found(self, auth_client):
        response = await auth_client.get(
            "/vault/admin/devmode/model/nonexistent-model/inspect"
        )
        assert response.status_code == 404

    async def test_inspect_model_with_config(self, auth_client, tmp_path):
        """Test model inspection with a real config.json file."""
        from app.config import settings

        # Create a temporary model directory with config.json
        model_dir = tmp_path / "test-model"
        model_dir.mkdir()

        config = {
            "model_type": "qwen2",
            "num_hidden_layers": 64,
            "hidden_size": 5120,
            "num_attention_heads": 40,
            "num_key_value_heads": 8,
            "intermediate_size": 27648,
            "vocab_size": 152064,
            "max_position_embeddings": 32768,
            "torch_dtype": "float16",
        }
        (model_dir / "config.json").write_text(json.dumps(config))

        # Create fake safetensors file
        (model_dir / "model-00001-of-00003.safetensors").write_bytes(b"\x00" * 1024)
        (model_dir / "model-00002-of-00003.safetensors").write_bytes(b"\x00" * 1024)
        (model_dir / "model-00003-of-00003.safetensors").write_bytes(b"\x00" * 1024)
        (model_dir / "tokenizer.json").write_text("{}")

        # Create quantize_config.json
        quant_config = {
            "quant_method": "awq",
            "bits": 4,
            "group_size": 128,
            "zero_point": True,
            "version": "gemm",
        }
        (model_dir / "quantize_config.json").write_text(json.dumps(quant_config))

        # Create a temporary manifest pointing to our test model
        manifest = {"models": [{"id": "test-model", "path": str(model_dir)}]}
        manifest_path = tmp_path / "models.json"
        manifest_path.write_text(json.dumps(manifest))

        original_manifest = settings.vault_models_manifest
        settings.vault_models_manifest = str(manifest_path)

        try:
            response = await auth_client.get(
                "/vault/admin/devmode/model/test-model/inspect"
            )
            assert response.status_code == 200
            data = response.json()

            assert data["model_id"] == "test-model"
            assert data["path"] == str(model_dir)

            # Architecture
            arch = data["architecture"]
            assert arch["model_type"] == "qwen2"
            assert arch["num_hidden_layers"] == 64
            assert arch["hidden_size"] == 5120
            assert arch["num_attention_heads"] == 40
            assert arch["num_key_value_heads"] == 8
            assert arch["vocab_size"] == 152064

            # Quantization
            quant = data["quantization"]
            assert quant["method"] == "awq"
            assert quant["bits"] == 4
            assert quant["group_size"] == 128

            # Files
            files = data["files"]
            assert files["safetensors_count"] == 3
            assert files["has_tokenizer"] is True
            assert files["total_size_bytes"] > 0

            # Raw config
            assert data["raw_config"]["model_type"] == "qwen2"

        finally:
            settings.vault_models_manifest = original_manifest

    async def test_inspect_requires_admin(self, anon_client):
        response = await anon_client.get(
            "/vault/admin/devmode/model/some-model/inspect"
        )
        assert response.status_code == 401


class TestTerminalEndpoints:
    async def test_start_terminal_requires_admin(self, anon_client):
        response = await anon_client.post("/vault/admin/devmode/terminal")
        assert response.status_code == 401

    async def test_stop_terminal_requires_admin(self, anon_client):
        response = await anon_client.request(
            "DELETE",
            "/vault/admin/devmode/terminal",
            params={"session_id": "fake"},
        )
        assert response.status_code == 401


class TestPythonEndpoints:
    async def test_start_python_requires_admin(self, anon_client):
        response = await anon_client.post("/vault/admin/devmode/python")
        assert response.status_code == 401

    async def test_stop_python_requires_admin(self, anon_client):
        response = await anon_client.request(
            "DELETE",
            "/vault/admin/devmode/python",
            params={"session_id": "fake"},
        )
        assert response.status_code == 401


class TestJupyterEndpoints:
    async def test_start_jupyter_requires_admin(self, anon_client):
        response = await anon_client.post("/vault/admin/devmode/jupyter")
        assert response.status_code == 401

    async def test_stop_jupyter_requires_admin(self, anon_client):
        response = await anon_client.request(
            "DELETE",
            "/vault/admin/devmode/jupyter",
        )
        assert response.status_code == 401
