"""Unit tests for Stage 4: AI Safety."""

import pytest
from pathlib import Path

from app.services.quarantine.stages.ai_safety import AISafetyStage

FIXTURES = Path(__file__).parent.parent / "fixtures" / "quarantine"


@pytest.fixture
def stage():
    return AISafetyStage()


@pytest.fixture
def default_config():
    return {
        "ai_safety_enabled": True,
        "pii_enabled": True,
        "pii_action": "flag",
        "injection_detection_enabled": True,
        "model_hash_verification": True,
    }


class TestAISafetyStage:
    @pytest.mark.asyncio
    async def test_stage_name(self, stage):
        assert stage.name == "ai_safety"

    @pytest.mark.asyncio
    async def test_master_toggle_disabled(self, stage):
        config = {"ai_safety_enabled": False}
        result = await stage.scan(FIXTURES / "training_chat.jsonl", "training_chat.jsonl", config)
        assert result.passed is True
        assert len(result.findings) == 0

    @pytest.mark.asyncio
    async def test_clean_jsonl_passes(self, stage, default_config):
        result = await stage.scan(FIXTURES / "training_chat.jsonl", "training_chat.jsonl", default_config)
        assert result.passed is True
        critical = [f for f in result.findings if f.severity == "critical"]
        assert len(critical) == 0

    @pytest.mark.asyncio
    async def test_jsonl_with_pii_flags(self, stage, default_config):
        result = await stage.scan(FIXTURES / "training_with_pii.jsonl", "training_with_pii.jsonl", default_config)
        pii_findings = [f for f in result.findings if f.code.startswith("pii_")]
        assert len(pii_findings) > 0

    @pytest.mark.asyncio
    async def test_jsonl_with_injections_flags(self, stage, default_config):
        result = await stage.scan(FIXTURES / "training_with_injections.jsonl", "data.jsonl", default_config)
        injection_findings = [f for f in result.findings if f.code.startswith("injection_")]
        assert len(injection_findings) > 0

    @pytest.mark.asyncio
    async def test_pii_block_mode_fails(self, stage):
        config = {
            "ai_safety_enabled": True,
            "pii_enabled": True,
            "pii_action": "block",
            "injection_detection_enabled": False,
            "model_hash_verification": True,
        }
        result = await stage.scan(FIXTURES / "training_with_pii.jsonl", "data.jsonl", config)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_model_pickle_rejected(self, stage, default_config, tmp_path):
        pkl_file = tmp_path / "model.pkl"
        pkl_file.write_bytes(b"fake pickle data")
        result = await stage.scan(pkl_file, "model.pkl", default_config)
        assert result.passed is False
        critical = [f for f in result.findings if f.severity == "critical"]
        assert len(critical) > 0

    @pytest.mark.asyncio
    async def test_unknown_extension_skipped(self, stage, default_config, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0fake jpeg")
        result = await stage.scan(img, "photo.jpg", default_config)
        assert result.passed is True
        assert len(result.findings) == 0
