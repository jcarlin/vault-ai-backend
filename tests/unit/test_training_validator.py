"""Unit tests for TrainingDataValidator."""

from pathlib import Path

import pytest

from app.services.quarantine.checkers.training_validator import TrainingDataValidator

FIXTURES = Path(__file__).parent.parent / "fixtures" / "quarantine"


@pytest.fixture
def validator():
    return TrainingDataValidator()


@pytest.fixture
def default_config():
    return {}


class TestTrainingDataValidator:
    @pytest.mark.asyncio
    async def test_valid_chat_format(self, validator, default_config):
        findings = await validator.validate(FIXTURES / "training_chat.jsonl", default_config)
        high_findings = [f for f in findings if f.severity in ("high", "critical")]
        assert len(high_findings) == 0

    @pytest.mark.asyncio
    async def test_invalid_json_lines(self, validator, default_config):
        findings = await validator.validate(FIXTURES / "training_invalid.jsonl", default_config)
        invalid_json = [f for f in findings if f.code == "training_invalid_json"]
        assert len(invalid_json) > 0

    @pytest.mark.asyncio
    async def test_empty_content_detected(self, validator, default_config):
        findings = await validator.validate(FIXTURES / "training_invalid.jsonl", default_config)
        empty = [f for f in findings if f.code == "training_empty_content"]
        assert len(empty) > 0

    @pytest.mark.asyncio
    async def test_missing_content_field(self, validator, default_config):
        findings = await validator.validate(FIXTURES / "training_invalid.jsonl", default_config)
        missing = [f for f in findings if f.code == "training_missing_field"]
        assert len(missing) > 0

    @pytest.mark.asyncio
    async def test_format_detection_chat(self, validator, default_config):
        findings = await validator.validate(FIXTURES / "training_chat.jsonl", default_config)
        format_errors = [f for f in findings if f.code == "training_unknown_format"]
        assert len(format_errors) == 0

    @pytest.mark.asyncio
    async def test_non_list_messages_field(self, validator, default_config):
        findings = await validator.validate(FIXTURES / "training_invalid.jsonl", default_config)
        struct_errors = [f for f in findings if f.code in ("training_invalid_messages", "training_missing_field")]
        assert len(struct_errors) > 0

    @pytest.mark.asyncio
    async def test_text_format_valid(self, validator, default_config, tmp_path):
        f = tmp_path / "text_data.jsonl"
        f.write_text('{"text": "Hello world"}\n{"text": "Another line"}\n')
        findings = await validator.validate(f, default_config)
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_completion_format_valid(self, validator, default_config, tmp_path):
        f = tmp_path / "completion_data.jsonl"
        f.write_text('{"prompt": "What is AI?", "completion": "AI is artificial intelligence."}\n')
        findings = await validator.validate(f, default_config)
        high = [finding for finding in findings if finding.severity in ("high", "critical")]
        assert len(high) == 0
