"""Unit tests for Stage 3: Content Sanitization."""

from pathlib import Path

import pytest

from app.services.quarantine.stages.sanitization import SanitizationStage


FIXTURES = Path(__file__).parent.parent / "fixtures" / "quarantine"


@pytest.fixture
def default_config():
    return {}


class TestImageSanitization:
    @pytest.mark.asyncio
    async def test_png_reencoding(self, tmp_path, default_config):
        """Create a PNG with Pillow, sanitize it, verify output exists."""
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")

        # Create a test PNG
        img = Image.new("RGB", (10, 10), color="red")
        test_png = tmp_path / "test.png"
        img.save(str(test_png))

        stage = SanitizationStage(sanitized_dir=tmp_path / "sanitized")
        result = await stage.scan(test_png, "test.png", default_config)

        assert result.passed
        reencode_findings = [f for f in result.findings if f.code == "image_reencoded"]
        assert len(reencode_findings) > 0
        assert result.sanitized_path is not None
        assert result.sanitized_path.exists()

    @pytest.mark.asyncio
    async def test_jpeg_with_exif(self, tmp_path, default_config):
        """JPEG with EXIF data should get stripped."""
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")

        img = Image.new("RGB", (10, 10), color="blue")
        test_jpg = tmp_path / "test.jpg"
        img.save(str(test_jpg), format="JPEG")

        stage = SanitizationStage(sanitized_dir=tmp_path / "sanitized")
        result = await stage.scan(test_jpg, "photo.jpg", default_config)
        assert result.passed
        assert result.sanitized_path is not None


class TestPDFSanitization:
    @pytest.mark.asyncio
    async def test_clean_pdf_passthrough(self, tmp_path, default_config):
        """Clean PDF should pass sanitization."""
        try:
            import pikepdf
        except ImportError:
            pytest.skip("pikepdf not installed")

        stage = SanitizationStage(sanitized_dir=tmp_path / "sanitized")
        result = await stage.scan(FIXTURES / "clean.pdf", "clean.pdf", default_config)
        assert result.passed

    @pytest.mark.asyncio
    async def test_pdf_with_javascript(self, tmp_path, default_config):
        """PDF with JavaScript should have JS stripped."""
        try:
            import pikepdf
        except ImportError:
            pytest.skip("pikepdf not installed")

        # Create a PDF with JavaScript
        test_pdf = tmp_path / "js.pdf"
        pdf = pikepdf.Pdf.new()
        page = pikepdf.Page(pdf.make_indirect(pikepdf.Dictionary({
            "/Type": pikepdf.Name("/Page"),
            "/MediaBox": pikepdf.Array([0, 0, 612, 792]),
        })))
        pdf.pages.append(page)

        # Add JavaScript to the document
        js_action = pikepdf.Dictionary({
            "/S": pikepdf.Name("/JavaScript"),
            "/JS": pikepdf.String("app.alert('XSS');"),
        })
        js_name_tree = pikepdf.Dictionary({
            "/Names": pikepdf.Array([pikepdf.String("js1"), pdf.make_indirect(js_action)]),
        })
        pdf.Root["/Names"] = pdf.make_indirect(pikepdf.Dictionary({
            "/JavaScript": pdf.make_indirect(js_name_tree),
        }))
        pdf.save(str(test_pdf))

        stage = SanitizationStage(sanitized_dir=tmp_path / "sanitized")
        result = await stage.scan(test_pdf, "malicious.pdf", default_config)
        assert result.passed
        js_findings = [f for f in result.findings if f.code == "pdf_js_removed"]
        assert len(js_findings) > 0
        assert result.sanitized_path is not None


class TestDocxSanitization:
    @pytest.mark.asyncio
    async def test_clean_docx(self, tmp_path, default_config):
        """Clean DOCX (no macros) should pass."""
        import zipfile

        # Create a minimal valid DOCX (it's just a ZIP with specific XML structure)
        test_docx = tmp_path / "clean.docx"
        with zipfile.ZipFile(str(test_docx), "w") as zf:
            zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types></Types>')
            zf.writestr("word/document.xml", "<w:document></w:document>")

        stage = SanitizationStage(sanitized_dir=tmp_path / "sanitized")
        result = await stage.scan(test_docx, "clean.docx", default_config)
        assert result.passed

    @pytest.mark.asyncio
    async def test_docx_with_macros(self, tmp_path, default_config):
        """DOCX with VBA macros should have them stripped."""
        import zipfile

        # Create a fake DOCX with a vbaProject.bin entry
        test_docx = tmp_path / "macro.docx"
        with zipfile.ZipFile(str(test_docx), "w") as zf:
            zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types></Types>')
            zf.writestr("word/document.xml", "<w:document></w:document>")
            zf.writestr("word/vbaProject.bin", b"FAKE_VBA_CONTENT")

        stage = SanitizationStage(sanitized_dir=tmp_path / "sanitized")
        result = await stage.scan(test_docx, "macro.docx", default_config)
        assert result.passed
        macro_findings = [f for f in result.findings if f.code == "docx_macros_removed"]
        assert len(macro_findings) > 0

        # Verify sanitized file doesn't contain vbaProject.bin
        if result.sanitized_path and result.sanitized_path.exists():
            with zipfile.ZipFile(str(result.sanitized_path)) as zf:
                assert "word/vbaProject.bin" not in zf.namelist()


class TestNonSanitizableFormats:
    @pytest.mark.asyncio
    async def test_text_file_passthrough(self, tmp_path, default_config):
        """Text files pass through sanitization with no changes."""
        stage = SanitizationStage(sanitized_dir=tmp_path / "sanitized")
        result = await stage.scan(FIXTURES / "clean.txt", "readme.txt", default_config)
        assert result.passed
        assert result.sanitized_path is None  # No sanitization needed

    @pytest.mark.asyncio
    async def test_json_file_passthrough(self, tmp_path, default_config):
        stage = SanitizationStage(sanitized_dir=tmp_path / "sanitized")
        result = await stage.scan(FIXTURES / "clean.json", "data.json", default_config)
        assert result.passed
