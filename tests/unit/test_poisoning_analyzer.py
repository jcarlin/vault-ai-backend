"""Unit tests for DataPoisoningAnalyzer."""

from pathlib import Path

import pytest

from app.services.quarantine.checkers.poisoning_analyzer import DataPoisoningAnalyzer

FIXTURES = Path(__file__).parent.parent / "fixtures" / "quarantine"


@pytest.fixture
def analyzer():
    return DataPoisoningAnalyzer()


@pytest.fixture
def default_config():
    return {}


class TestDataPoisoningAnalyzer:
    @pytest.mark.asyncio
    async def test_clean_data_no_findings(self, analyzer, default_config):
        findings = await analyzer.analyze(FIXTURES / "training_chat.jsonl", default_config)
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_backdoor_pattern_detected(self, analyzer, default_config):
        findings = await analyzer.analyze(FIXTURES / "training_poisoned.jsonl", default_config)
        backdoor = [f for f in findings if f.code == "poisoning_backdoor_pattern"]
        assert len(backdoor) > 0
        assert backdoor[0].severity == "critical"

    @pytest.mark.asyncio
    async def test_repetitive_trigram_detected(self, analyzer, default_config, tmp_path):
        f = tmp_path / "trigrams.jsonl"
        # Same trigram "the quick brown" in >30% of records
        poisoned = '{"text": "the quick brown fox jumps over the lazy dog"}\n'
        clean = '{"text": "Completely different unique content here"}\n'
        lines = [poisoned] * 8 + [clean] * 12
        f.write_text("".join(lines))
        findings = await analyzer.analyze(f, default_config)
        repetitive = [finding for finding in findings if finding.code == "poisoning_repetitive_content"]
        assert len(repetitive) > 0

    @pytest.mark.asyncio
    async def test_statistical_outliers_detected(self, analyzer, default_config, tmp_path):
        f = tmp_path / "outliers.jsonl"
        import json
        # 80 normal records with similar lengths + 5 extreme records (6.25% > 5%)
        normal = [json.dumps({"text": f"Normal sentence number {i} with typical content."}) + "\n" for i in range(80)]
        # Extreme: massively longer text — will be a clear length outlier (z > 3)
        extreme_text = "x " * 5000  # 10000 chars vs ~45 chars normal
        extreme = [json.dumps({"text": extreme_text}) + "\n"] * 5
        f.write_text("".join(normal + extreme))
        findings = await analyzer.analyze(f, default_config)
        outliers = [finding for finding in findings if finding.code == "poisoning_statistical_outliers"]
        assert len(outliers) > 0

    @pytest.mark.asyncio
    async def test_empty_file_graceful(self, analyzer, default_config, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        findings = await analyzer.analyze(f, default_config)
        assert findings == []

    @pytest.mark.asyncio
    async def test_small_file_no_crash(self, analyzer, default_config, tmp_path):
        f = tmp_path / "small.jsonl"
        f.write_text('{"text": "Hello"}\n{"text": "World"}\n')
        findings = await analyzer.analyze(f, default_config)
        # Should not crash, may or may not have findings
        assert isinstance(findings, list)
