"""Unit tests for TrainingDataAnalyzer."""

from pathlib import Path

import pytest

from app.services.quarantine.checkers.training_analyzer import TrainingDataAnalyzer

FIXTURES = Path(__file__).parent.parent / "fixtures" / "quarantine"


@pytest.fixture
def analyzer():
    return TrainingDataAnalyzer()


@pytest.fixture
def default_config():
    return {}


class TestTrainingDataAnalyzer:
    @pytest.mark.asyncio
    async def test_clean_data_no_findings(self, analyzer, default_config):
        findings = await analyzer.analyze(FIXTURES / "training_chat.jsonl", default_config)
        high_findings = [f for f in findings if f.severity in ("high", "critical")]
        assert len(high_findings) == 0

    @pytest.mark.asyncio
    async def test_high_duplicate_rate_detected(self, analyzer, default_config):
        findings = await analyzer.analyze(FIXTURES / "training_duplicates.jsonl", default_config)
        dup_findings = [f for f in findings if f.code == "training_high_duplicate_rate"]
        assert len(dup_findings) > 0

    @pytest.mark.asyncio
    async def test_zero_variance_detection(self, analyzer, default_config, tmp_path):
        f = tmp_path / "same_length.jsonl"
        lines = [f'{{"text": "abcde{i:04d}"}}\n' for i in range(20)]
        f.write_text("".join(lines))
        findings = await analyzer.analyze(f, default_config)
        zero_var = [f for f in findings if f.code == "training_zero_variance"]
        assert len(zero_var) > 0

    @pytest.mark.asyncio
    async def test_short_content_detection(self, analyzer, default_config, tmp_path):
        f = tmp_path / "short.jsonl"
        lines = ['{"text": "ab"}\n'] * 5 + ['{"text": "This is a normal length sentence."}\n'] * 5
        f.write_text("".join(lines))
        findings = await analyzer.analyze(f, default_config)
        short = [finding for finding in findings if finding.code == "training_short_content"]
        assert len(short) > 0

    @pytest.mark.asyncio
    async def test_length_outlier_detection(self, analyzer, default_config, tmp_path):
        f = tmp_path / "outlier.jsonl"
        normal = ['{"text": "A normal sentence here."}\n'] * 19
        outlier = ['{"text": "' + "x" * 5000 + '"}\n']
        f.write_text("".join(normal + outlier))
        findings = await analyzer.analyze(f, default_config)
        outliers = [finding for finding in findings if finding.code == "training_length_outlier"]
        assert len(outliers) > 0

    @pytest.mark.asyncio
    async def test_clean_data_returns_empty(self, analyzer, default_config, tmp_path):
        f = tmp_path / "clean.jsonl"
        lines = [
            f'{{"text": "Sentence number {i} with varying content and length for testing purposes."}}\n'
            for i in range(20)
        ]
        f.write_text("".join(lines))
        findings = await analyzer.analyze(f, default_config)
        # Should have no high/critical findings
        serious = [finding for finding in findings if finding.severity in ("high", "critical")]
        assert len(serious) == 0

    @pytest.mark.asyncio
    async def test_empty_file_graceful(self, analyzer, default_config, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        findings = await analyzer.analyze(f, default_config)
        assert findings == []

    @pytest.mark.asyncio
    async def test_class_imbalance_detection(self, analyzer, default_config, tmp_path):
        f = tmp_path / "imbalanced.jsonl"
        same_instruction = '{"instruction": "Translate to French", "output": "Bonjour"}\n'
        lines = [same_instruction] * 19 + ['{"instruction": "Summarize this text", "output": "Summary here."}\n']
        f.write_text("".join(lines))
        findings = await analyzer.analyze(f, default_config)
        imbalance = [finding for finding in findings if finding.code == "training_class_imbalance"]
        assert len(imbalance) > 0
