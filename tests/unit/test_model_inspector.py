"""Unit tests for model inspector — config.json parsing for various architectures."""

import json
from pathlib import Path

import pytest

from app.services.devmode import inspect_model
from app.config import settings


@pytest.fixture
def model_dir(tmp_path):
    """Create a temporary model directory and patch settings to use it."""
    model_path = tmp_path / "test-model"
    model_path.mkdir()
    return model_path


@pytest.fixture
def manifest_file(tmp_path, model_dir):
    """Create a manifest pointing to the model dir."""
    manifest = {"models": [{"id": "test-model", "path": str(model_dir)}]}
    manifest_path = tmp_path / "models.json"
    manifest_path.write_text(json.dumps(manifest))
    original = settings.vault_models_manifest
    settings.vault_models_manifest = str(manifest_path)
    yield manifest_path
    settings.vault_models_manifest = original


class TestInspectQwen2:
    async def test_qwen2_architecture(self, model_dir, manifest_file):
        config = {
            "model_type": "qwen2",
            "num_hidden_layers": 64,
            "hidden_size": 5120,
            "num_attention_heads": 40,
            "num_key_value_heads": 8,
            "intermediate_size": 27648,
            "vocab_size": 152064,
            "max_position_embeddings": 32768,
            "rope_theta": 1000000.0,
            "torch_dtype": "bfloat16",
        }
        (model_dir / "config.json").write_text(json.dumps(config))
        (model_dir / "model.safetensors").write_bytes(b"\x00" * 100)

        result = await inspect_model("test-model")

        assert result.architecture.model_type == "qwen2"
        assert result.architecture.num_hidden_layers == 64
        assert result.architecture.hidden_size == 5120
        assert result.architecture.num_key_value_heads == 8
        assert result.architecture.rope_theta == 1000000.0

    async def test_awq_quantization(self, model_dir, manifest_file):
        (model_dir / "config.json").write_text(json.dumps({"model_type": "qwen2"}))
        quant = {
            "quant_method": "awq",
            "bits": 4,
            "group_size": 128,
            "zero_point": True,
            "version": "gemm",
        }
        (model_dir / "quantize_config.json").write_text(json.dumps(quant))
        (model_dir / "model.safetensors").write_bytes(b"\x00")

        result = await inspect_model("test-model")

        assert result.quantization is not None
        assert result.quantization.method == "awq"
        assert result.quantization.bits == 4
        assert result.quantization.group_size == 128


class TestInspectLlama:
    async def test_llama_architecture(self, model_dir, manifest_file):
        config = {
            "model_type": "llama",
            "num_hidden_layers": 32,
            "hidden_size": 4096,
            "num_attention_heads": 32,
            "num_key_value_heads": 8,
            "intermediate_size": 14336,
            "vocab_size": 128256,
            "max_position_embeddings": 131072,
            "torch_dtype": "bfloat16",
        }
        (model_dir / "config.json").write_text(json.dumps(config))
        (model_dir / "model.safetensors").write_bytes(b"\x00")

        result = await inspect_model("test-model")

        assert result.architecture.model_type == "llama"
        assert result.architecture.num_hidden_layers == 32
        assert result.architecture.max_position_embeddings == 131072


class TestInspectMistral:
    async def test_mistral_architecture(self, model_dir, manifest_file):
        config = {
            "model_type": "mistral",
            "num_hidden_layers": 32,
            "hidden_size": 4096,
            "num_attention_heads": 32,
            "num_key_value_heads": 8,
            "intermediate_size": 14336,
            "vocab_size": 32000,
            "max_position_embeddings": 32768,
        }
        (model_dir / "config.json").write_text(json.dumps(config))
        (model_dir / "model.safetensors").write_bytes(b"\x00")

        result = await inspect_model("test-model")

        assert result.architecture.model_type == "mistral"
        assert result.architecture.vocab_size == 32000


class TestInspectFiles:
    async def test_safetensors_count(self, model_dir, manifest_file):
        (model_dir / "config.json").write_text("{}")
        for i in range(5):
            (model_dir / f"model-{i:05d}.safetensors").write_bytes(b"\x00" * (1024 * i + 1))

        result = await inspect_model("test-model")

        assert result.files.safetensors_count == 5
        assert result.files.total_size_bytes > 0

    async def test_tokenizer_detection(self, model_dir, manifest_file):
        (model_dir / "config.json").write_text("{}")
        (model_dir / "tokenizer.json").write_text("{}")
        (model_dir / "model.safetensors").write_bytes(b"\x00")

        result = await inspect_model("test-model")
        assert result.files.has_tokenizer is True

    async def test_no_tokenizer(self, model_dir, manifest_file):
        (model_dir / "config.json").write_text("{}")
        (model_dir / "model.safetensors").write_bytes(b"\x00")

        result = await inspect_model("test-model")
        assert result.files.has_tokenizer is False

    async def test_inline_quantization_config(self, model_dir, manifest_file):
        """Test quantization info extracted from config.json's quantization_config field."""
        config = {
            "model_type": "llama",
            "quantization_config": {
                "quant_method": "gptq",
                "bits": 4,
                "group_size": 128,
            },
        }
        (model_dir / "config.json").write_text(json.dumps(config))
        (model_dir / "model.safetensors").write_bytes(b"\x00")

        result = await inspect_model("test-model")

        assert result.quantization is not None
        assert result.quantization.method == "gptq"
        assert result.quantization.bits == 4


class TestInspectNotFound:
    async def test_model_not_found(self):
        from app.core.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            await inspect_model("nonexistent-model-xyz")

    async def test_raw_config_returned(self, model_dir, manifest_file):
        config = {"model_type": "custom", "custom_field": 42}
        (model_dir / "config.json").write_text(json.dumps(config))
        (model_dir / "model.safetensors").write_bytes(b"\x00")

        result = await inspect_model("test-model")
        assert result.raw_config["custom_field"] == 42
