"""Unit tests for Stage 1: File Integrity."""

import json
import struct
import zipfile
import io
from pathlib import Path

import pytest

from app.services.quarantine.stages.file_integrity import FileIntegrityStage


@pytest.fixture
def stage():
    return FileIntegrityStage()


@pytest.fixture
def default_config():
    return {
        "max_file_size": 1073741824,
        "max_batch_files": 100,
        "max_compression_ratio": 100,
        "max_archive_depth": 3,
    }


FIXTURES = Path(__file__).parent.parent / "fixtures" / "quarantine"


class TestMimeValidation:
    @pytest.mark.asyncio
    async def test_clean_pdf_passes(self, stage, default_config):
        result = await stage.scan(FIXTURES / "clean.pdf", "clean.pdf", default_config)
        # PDF magic bytes should match
        high_findings = [f for f in result.findings if f.severity in ("high", "critical")]
        # No high/critical MIME findings for a valid PDF
        mime_high = [f for f in high_findings if f.code == "mime_mismatch"]
        assert len(mime_high) == 0

    @pytest.mark.asyncio
    async def test_mime_mismatch_detected(self, stage, default_config):
        result = await stage.scan(FIXTURES / "fake.pdf", "fake.pdf", default_config)
        mime_findings = [f for f in result.findings if f.code == "mime_mismatch"]
        assert len(mime_findings) > 0
        assert mime_findings[0].severity == "high"
        assert not result.passed

    @pytest.mark.asyncio
    async def test_text_file_passes(self, stage, default_config):
        result = await stage.scan(FIXTURES / "clean.txt", "clean.txt", default_config)
        assert result.passed

    @pytest.mark.asyncio
    async def test_json_file_passes(self, stage, default_config):
        result = await stage.scan(FIXTURES / "clean.json", "data.json", default_config)
        # JSON is detected as text/plain by libmagic, which is in our allowed set
        high_findings = [f for f in result.findings if f.severity in ("high", "critical")]
        assert len(high_findings) == 0


class TestFormatValidation:
    @pytest.mark.asyncio
    async def test_valid_json(self, stage, default_config):
        result = await stage.scan(FIXTURES / "clean.json", "data.json", default_config)
        json_findings = [f for f in result.findings if f.code == "invalid_json"]
        assert len(json_findings) == 0

    @pytest.mark.asyncio
    async def test_invalid_json_detected(self, stage, default_config):
        result = await stage.scan(FIXTURES / "invalid_json.txt", "data.json", default_config)
        json_findings = [f for f in result.findings if f.code == "invalid_json"]
        assert len(json_findings) > 0

    @pytest.mark.asyncio
    async def test_valid_jsonl(self, stage, default_config):
        result = await stage.scan(FIXTURES / "clean.jsonl", "data.jsonl", default_config)
        jsonl_findings = [f for f in result.findings if "jsonl" in f.code]
        assert len(jsonl_findings) == 0

    @pytest.mark.asyncio
    async def test_valid_safetensors(self, stage, default_config):
        result = await stage.scan(FIXTURES / "clean.safetensors", "model.safetensors", default_config)
        st_findings = [f for f in result.findings if "safetensors" in f.code and f.severity in ("high", "critical")]
        assert len(st_findings) == 0

    @pytest.mark.asyncio
    async def test_suspicious_safetensors_metadata(self, stage, default_config):
        result = await stage.scan(FIXTURES / "suspicious.safetensors", "model.safetensors", default_config)
        sus_findings = [f for f in result.findings if f.code == "safetensors_suspicious_metadata"]
        assert len(sus_findings) > 0
        assert sus_findings[0].severity == "critical"

    @pytest.mark.asyncio
    async def test_pdf_invalid_header(self, stage, default_config, tmp_path):
        bad_pdf = tmp_path / "bad.pdf"
        bad_pdf.write_bytes(b"NOT_A_PDF_CONTENT")
        result = await stage.scan(bad_pdf, "bad.pdf", default_config)
        header_findings = [f for f in result.findings if f.code == "invalid_pdf_header"]
        assert len(header_findings) > 0


class TestSizeLimits:
    @pytest.mark.asyncio
    async def test_file_within_limit(self, stage, default_config):
        result = await stage.scan(FIXTURES / "clean.txt", "clean.txt", default_config)
        size_findings = [f for f in result.findings if f.code == "file_too_large"]
        assert len(size_findings) == 0

    @pytest.mark.asyncio
    async def test_file_exceeds_limit(self, stage, tmp_path):
        config = {"max_file_size": 10}  # 10 bytes
        big_file = tmp_path / "big.txt"
        big_file.write_bytes(b"x" * 100)
        result = await stage.scan(big_file, "big.txt", config)
        size_findings = [f for f in result.findings if f.code == "file_too_large"]
        assert len(size_findings) > 0
        assert not result.passed


class TestArchiveBombs:
    @pytest.mark.asyncio
    async def test_archive_bomb_detected(self, stage):
        config = {"max_compression_ratio": 50, "max_archive_depth": 3, "max_file_size": 1073741824}
        result = await stage.scan(FIXTURES / "archive_bomb.zip", "bomb.zip", config)
        bomb_findings = [f for f in result.findings if f.code == "archive_bomb_ratio"]
        assert len(bomb_findings) > 0
        assert bomb_findings[0].severity == "critical"
        assert not result.passed

    @pytest.mark.asyncio
    async def test_clean_zip_passes(self, stage, default_config):
        result = await stage.scan(FIXTURES / "clean.zip", "clean.zip", default_config)
        bomb_findings = [f for f in result.findings if "archive_bomb" in f.code]
        assert len(bomb_findings) == 0

    @pytest.mark.asyncio
    async def test_nested_zip_depth(self, stage, tmp_path):
        """Create a zip-in-zip-in-zip and verify depth detection."""
        # Create inner zip
        inner_buf = io.BytesIO()
        with zipfile.ZipFile(inner_buf, "w") as zf:
            zf.writestr("deep.txt", "deep file")
        inner_buf.seek(0)

        # Create middle zip containing inner
        mid_buf = io.BytesIO()
        with zipfile.ZipFile(mid_buf, "w") as zf:
            zf.writestr("inner.zip", inner_buf.read())
        mid_buf.seek(0)

        # Create outer zip containing middle
        outer_buf = io.BytesIO()
        with zipfile.ZipFile(outer_buf, "w") as zf:
            zf.writestr("middle.zip", mid_buf.read())
        outer_buf.seek(0)

        # Wrap in one more level
        final_buf = io.BytesIO()
        with zipfile.ZipFile(final_buf, "w") as zf:
            zf.writestr("outer.zip", outer_buf.read())
        final_buf.seek(0)

        nested = tmp_path / "nested.zip"
        nested.write_bytes(final_buf.read())

        config = {"max_compression_ratio": 1000, "max_archive_depth": 2, "max_file_size": 1073741824}
        result = await stage.scan(nested, "nested.zip", config)
        depth_findings = [f for f in result.findings if f.code == "archive_bomb_depth"]
        assert len(depth_findings) > 0
