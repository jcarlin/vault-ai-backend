"""Unit tests for ModelFileValidator."""

import struct
from pathlib import Path

import pytest

from app.services.quarantine.checkers.model_validator import ModelFileValidator


@pytest.fixture
def validator():
    return ModelFileValidator()


@pytest.fixture
def default_config():
    return {}


FIXTURES = Path(__file__).parent.parent / "fixtures" / "quarantine"


class TestModelFileValidator:
    @pytest.mark.asyncio
    async def test_pickle_file_rejected(self, validator, default_config, tmp_path):
        """Pickle files should always be flagged as critical."""
        pkl_file = tmp_path / "model.pkl"
        pkl_file.write_bytes(b"fake pickle data")
        findings = await validator.validate(pkl_file, "model.pkl", default_config)
        assert len(findings) == 1
        assert findings[0].code == "model_dangerous_format"
        assert findings[0].severity == "critical"

    @pytest.mark.asyncio
    async def test_pytorch_file_rejected(self, validator, default_config, tmp_path):
        """PyTorch .pt files should always be flagged as critical."""
        pt_file = tmp_path / "model.pt"
        pt_file.write_bytes(b"fake pytorch data")
        findings = await validator.validate(pt_file, "model.pt", default_config)
        assert len(findings) == 1
        assert findings[0].code == "model_dangerous_format"
        assert findings[0].severity == "critical"

    @pytest.mark.asyncio
    async def test_valid_safetensors(self, validator, default_config):
        """Clean safetensors file should have no critical findings."""
        findings = await validator.validate(
            FIXTURES / "clean.safetensors", "model.safetensors", default_config
        )
        critical = [f for f in findings if f.severity == "critical"]
        assert len(critical) == 0

    @pytest.mark.asyncio
    async def test_suspicious_safetensors_metadata(self, validator, default_config):
        """Safetensors with suspicious metadata keys should be flagged."""
        findings = await validator.validate(
            FIXTURES / "suspicious.safetensors",
            "suspicious.safetensors",
            default_config,
        )
        metadata_findings = [
            f for f in findings if f.code == "model_suspicious_metadata"
        ]
        assert len(metadata_findings) == 1
        assert metadata_findings[0].severity == "medium"

    @pytest.mark.asyncio
    async def test_valid_model_config(self, validator, default_config):
        """Valid model config should produce no findings or only info-level."""
        findings = await validator.validate(
            FIXTURES / "model_config_valid.json",
            "config.json",
            default_config,
        )
        high_crit = [f for f in findings if f.severity in ("high", "critical")]
        assert len(high_crit) == 0

    @pytest.mark.asyncio
    async def test_unknown_architecture(self, validator, default_config):
        """Unknown architecture should produce low-severity finding."""
        findings = await validator.validate(
            FIXTURES / "model_config_unknown.json",
            "config.json",
            default_config,
        )
        arch_findings = [
            f for f in findings if f.code == "model_unknown_architecture"
        ]
        assert len(arch_findings) >= 1
        assert all(f.severity == "low" for f in arch_findings)

    @pytest.mark.asyncio
    async def test_invalid_gguf_magic(self, validator, default_config, tmp_path):
        """File with wrong GGUF magic bytes should be flagged."""
        bad_gguf = tmp_path / "model.gguf"
        bad_gguf.write_bytes(b"BAAD" + struct.pack("<I", 3))
        findings = await validator.validate(bad_gguf, "model.gguf", default_config)
        gguf_findings = [f for f in findings if f.code == "model_invalid_gguf"]
        assert len(gguf_findings) == 1
        assert gguf_findings[0].severity == "high"

    @pytest.mark.asyncio
    async def test_valid_gguf_magic(self, validator, default_config, tmp_path):
        """File with correct GGUF magic + valid version should pass."""
        good_gguf = tmp_path / "model.gguf"
        good_gguf.write_bytes(b"GGUF" + struct.pack("<I", 3))
        findings = await validator.validate(good_gguf, "model.gguf", default_config)
        high_crit = [f for f in findings if f.severity in ("high", "critical")]
        assert len(high_crit) == 0
