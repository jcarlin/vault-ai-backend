"""Stage 1: File Integrity — magic bytes, format validation, size limits, archive bombs."""

import hashlib
import io
import json
import struct
import zipfile
from pathlib import Path

import magic

from app.services.quarantine.stages import PipelineStage, StageFinding, StageResult


# Expected MIME types for file extensions
EXTENSION_MIME_MAP = {
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/zip"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/zip"},
    ".pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/zip"},
    ".doc": {"application/msword", "application/octet-stream"},
    ".xls": {"application/vnd.ms-excel", "application/octet-stream"},
    ".json": {"application/json", "text/plain", "text/x-json"},
    ".jsonl": {"application/json", "text/plain", "application/x-ndjson"},
    ".txt": {"text/plain"},
    ".csv": {"text/plain", "text/csv", "application/csv"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".gif": {"image/gif"},
    ".webp": {"image/webp"},
    ".svg": {"image/svg+xml", "text/plain", "text/xml"},
    ".safetensors": {"application/octet-stream"},
    ".gguf": {"application/octet-stream"},
    ".zip": {"application/zip"},
    ".tar": {"application/x-tar"},
    ".gz": {"application/gzip", "application/x-gzip"},
    ".tar.gz": {"application/gzip", "application/x-gzip"},
    ".yaml": {"text/plain", "text/x-yaml", "application/x-yaml"},
    ".yml": {"text/plain", "text/x-yaml", "application/x-yaml"},
    ".md": {"text/plain", "text/markdown"},
    ".py": {"text/plain", "text/x-python", "text/x-script.python"},
}


class FileIntegrityStage(PipelineStage):
    """Stage 1: Validate file integrity — MIME type, format structure, size, archive safety."""

    @property
    def name(self) -> str:
        return "file_integrity"

    async def scan(self, file_path: Path, original_filename: str, config: dict) -> StageResult:
        findings = []
        passed = True

        # 1. File size check
        size_finding = self._check_size(file_path, config)
        if size_finding:
            findings.append(size_finding)
            if size_finding.severity in ("high", "critical"):
                passed = False

        # 2. MIME type / magic byte verification
        mime_findings = self._check_mime(file_path, original_filename)
        findings.extend(mime_findings)
        for f in mime_findings:
            if f.severity in ("high", "critical"):
                passed = False

        # 3. Per-format deep validation
        format_findings = self._validate_format(file_path, original_filename)
        findings.extend(format_findings)
        for f in format_findings:
            if f.severity in ("high", "critical"):
                passed = False

        # 4. Archive bomb detection
        archive_findings = self._check_archive_bombs(file_path, original_filename, config)
        findings.extend(archive_findings)
        for f in archive_findings:
            if f.severity in ("high", "critical"):
                passed = False

        return StageResult(passed=passed, findings=findings)

    def _check_size(self, file_path: Path, config: dict) -> StageFinding | None:
        max_size = config.get("max_file_size", 1073741824)
        file_size = file_path.stat().st_size
        if file_size > max_size:
            return StageFinding(
                stage=self.name,
                severity="high",
                code="file_too_large",
                message=f"File size ({file_size} bytes) exceeds maximum ({max_size} bytes).",
                details={"file_size": file_size, "max_size": max_size},
            )
        return None

    def _check_mime(self, file_path: Path, original_filename: str) -> list[StageFinding]:
        findings = []
        ext = self._get_extension(original_filename).lower()

        try:
            detected_mime = magic.from_file(str(file_path), mime=True)
        except Exception as e:
            findings.append(StageFinding(
                stage=self.name,
                severity="medium",
                code="mime_detection_failed",
                message=f"Could not detect MIME type: {e}",
            ))
            return findings

        expected_mimes = EXTENSION_MIME_MAP.get(ext)
        if expected_mimes and detected_mime not in expected_mimes:
            findings.append(StageFinding(
                stage=self.name,
                severity="high",
                code="mime_mismatch",
                message=f"MIME type mismatch: extension '{ext}' expected {expected_mimes}, detected '{detected_mime}'.",
                details={"extension": ext, "expected": list(expected_mimes), "detected": detected_mime},
            ))

        return findings

    def _validate_format(self, file_path: Path, original_filename: str) -> list[StageFinding]:
        """Per-format deep validation."""
        ext = self._get_extension(original_filename).lower()
        findings = []

        try:
            if ext == ".pdf":
                findings.extend(self._validate_pdf(file_path))
            elif ext == ".docx":
                findings.extend(self._validate_docx(file_path))
            elif ext in (".xlsx", ".pptx"):
                findings.extend(self._validate_office_zip(file_path, ext))
            elif ext == ".json":
                findings.extend(self._validate_json(file_path))
            elif ext == ".jsonl":
                findings.extend(self._validate_jsonl(file_path))
            elif ext == ".safetensors":
                findings.extend(self._validate_safetensors(file_path))
        except Exception as e:
            findings.append(StageFinding(
                stage=self.name,
                severity="medium",
                code="format_validation_error",
                message=f"Error validating {ext} format: {e}",
            ))

        return findings

    def _validate_pdf(self, file_path: Path) -> list[StageFinding]:
        findings = []
        content = file_path.read_bytes()

        # Check PDF magic bytes
        if not content.startswith(b"%PDF"):
            findings.append(StageFinding(
                stage=self.name,
                severity="high",
                code="invalid_pdf_header",
                message="File does not start with %PDF magic bytes.",
            ))
            return findings

        # Try to parse with pikepdf (if available)
        try:
            import pikepdf
            with pikepdf.open(file_path) as pdf:
                _ = len(pdf.pages)
        except ImportError:
            pass  # pikepdf not available, skip deep validation
        except Exception as e:
            findings.append(StageFinding(
                stage=self.name,
                severity="medium",
                code="pdf_parse_error",
                message=f"PDF parsing failed: {e}",
            ))

        return findings

    def _validate_docx(self, file_path: Path) -> list[StageFinding]:
        findings = []

        if not zipfile.is_zipfile(str(file_path)):
            findings.append(StageFinding(
                stage=self.name,
                severity="high",
                code="invalid_docx_structure",
                message="DOCX file is not a valid ZIP archive.",
            ))
            return findings

        try:
            with zipfile.ZipFile(str(file_path), "r") as zf:
                names = zf.namelist()
                if "[Content_Types].xml" not in names:
                    findings.append(StageFinding(
                        stage=self.name,
                        severity="high",
                        code="invalid_docx_structure",
                        message="DOCX archive missing [Content_Types].xml.",
                    ))
        except zipfile.BadZipFile as e:
            findings.append(StageFinding(
                stage=self.name,
                severity="high",
                code="invalid_docx_structure",
                message=f"Corrupt ZIP structure: {e}",
            ))

        return findings

    def _validate_office_zip(self, file_path: Path, ext: str) -> list[StageFinding]:
        """Validate Office Open XML formats (xlsx, pptx) — same ZIP structure check."""
        findings = []

        if not zipfile.is_zipfile(str(file_path)):
            findings.append(StageFinding(
                stage=self.name,
                severity="high",
                code=f"invalid_{ext.lstrip('.')}_structure",
                message=f"{ext.upper()} file is not a valid ZIP archive.",
            ))
            return findings

        try:
            with zipfile.ZipFile(str(file_path), "r") as zf:
                names = zf.namelist()
                if "[Content_Types].xml" not in names:
                    findings.append(StageFinding(
                        stage=self.name,
                        severity="high",
                        code=f"invalid_{ext.lstrip('.')}_structure",
                        message=f"{ext.upper()} archive missing [Content_Types].xml.",
                    ))
        except zipfile.BadZipFile as e:
            findings.append(StageFinding(
                stage=self.name,
                severity="high",
                code=f"invalid_{ext.lstrip('.')}_structure",
                message=f"Corrupt ZIP structure: {e}",
            ))

        return findings

    def _validate_json(self, file_path: Path) -> list[StageFinding]:
        findings = []
        try:
            text = file_path.read_text(encoding="utf-8")
            json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            findings.append(StageFinding(
                stage=self.name,
                severity="medium",
                code="invalid_json",
                message=f"Invalid JSON: {e}",
            ))
        return findings

    def _validate_jsonl(self, file_path: Path) -> list[StageFinding]:
        findings = []
        try:
            text = file_path.read_text(encoding="utf-8")
            lines = [line for line in text.strip().split("\n") if line.strip()]
            for i, line in enumerate(lines[:100]):  # Validate first 100 lines
                try:
                    json.loads(line)
                except json.JSONDecodeError as e:
                    findings.append(StageFinding(
                        stage=self.name,
                        severity="medium",
                        code="invalid_jsonl_line",
                        message=f"Invalid JSON on line {i + 1}: {e}",
                    ))
                    break  # One finding is enough
        except UnicodeDecodeError as e:
            findings.append(StageFinding(
                stage=self.name,
                severity="medium",
                code="invalid_jsonl_encoding",
                message=f"JSONL file is not valid UTF-8: {e}",
            ))
        return findings

    def _validate_safetensors(self, file_path: Path) -> list[StageFinding]:
        """Validate safetensors header structure (first 8 bytes = little-endian uint64 header size)."""
        findings = []
        content = file_path.read_bytes()

        if len(content) < 8:
            findings.append(StageFinding(
                stage=self.name,
                severity="high",
                code="invalid_safetensors",
                message="File too small to be a valid safetensors file.",
            ))
            return findings

        header_size = struct.unpack("<Q", content[:8])[0]

        # Sanity check: header shouldn't be larger than the file
        if header_size > len(content) - 8:
            findings.append(StageFinding(
                stage=self.name,
                severity="high",
                code="invalid_safetensors_header",
                message=f"Safetensors header size ({header_size}) exceeds file content.",
            ))
            return findings

        # Try to parse header as JSON
        try:
            header_json = content[8:8 + header_size].decode("utf-8")
            header = json.loads(header_json)
            # Check for suspicious keys that might indicate pickle embedding
            if "__metadata__" in header:
                meta = header["__metadata__"]
                if isinstance(meta, dict):
                    for key in meta:
                        if "pickle" in key.lower() or "exec" in key.lower():
                            findings.append(StageFinding(
                                stage=self.name,
                                severity="critical",
                                code="safetensors_suspicious_metadata",
                                message=f"Suspicious metadata key in safetensors: '{key}'",
                                details={"key": key},
                            ))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            findings.append(StageFinding(
                stage=self.name,
                severity="high",
                code="invalid_safetensors_header",
                message=f"Safetensors header is not valid JSON: {e}",
            ))

        return findings

    def _check_archive_bombs(self, file_path: Path, original_filename: str, config: dict) -> list[StageFinding]:
        """Detect archive bombs (zip bombs) via compression ratio and nesting depth."""
        ext = self._get_extension(original_filename).lower()
        if ext not in (".zip", ".docx", ".xlsx", ".pptx"):
            return []

        findings = []
        max_ratio = config.get("max_compression_ratio", 100)
        max_depth = config.get("max_archive_depth", 3)

        if not zipfile.is_zipfile(str(file_path)):
            return []

        try:
            with zipfile.ZipFile(str(file_path), "r") as zf:
                compressed_size = file_path.stat().st_size
                total_uncompressed = sum(info.file_size for info in zf.infolist())

                # Compression ratio check
                if compressed_size > 0:
                    ratio = total_uncompressed / compressed_size
                    if ratio > max_ratio:
                        findings.append(StageFinding(
                            stage=self.name,
                            severity="critical",
                            code="archive_bomb_ratio",
                            message=f"Compression ratio ({ratio:.1f}:1) exceeds maximum ({max_ratio}:1). Possible archive bomb.",
                            details={"ratio": round(ratio, 1), "max_ratio": max_ratio},
                        ))

                # Nesting depth check (look for zip-in-zip)
                if ext == ".zip":
                    nested_zips = [name for name in zf.namelist() if name.lower().endswith(".zip")]
                    if len(nested_zips) > 0:
                        depth = self._check_zip_depth(zf, current_depth=1, max_depth=max_depth)
                        if depth > max_depth:
                            findings.append(StageFinding(
                                stage=self.name,
                                severity="critical",
                                code="archive_bomb_depth",
                                message=f"Archive nesting depth ({depth}) exceeds maximum ({max_depth}). Possible archive bomb.",
                                details={"depth": depth, "max_depth": max_depth},
                            ))
        except zipfile.BadZipFile:
            pass  # Already caught in format validation

        return findings

    def _check_zip_depth(self, zf: zipfile.ZipFile, current_depth: int, max_depth: int) -> int:
        """Recursively check ZIP nesting depth, up to max_depth."""
        if current_depth > max_depth:
            return current_depth

        max_found = current_depth
        for name in zf.namelist():
            if name.lower().endswith(".zip"):
                try:
                    with zf.open(name) as inner_file:
                        inner_data = inner_file.read(50 * 1024 * 1024)  # Cap at 50MB to prevent DoS
                        inner_zf = zipfile.ZipFile(io.BytesIO(inner_data))
                        depth = self._check_zip_depth(inner_zf, current_depth + 1, max_depth)
                        max_found = max(max_found, depth)
                        inner_zf.close()
                except Exception:
                    pass  # Can't read nested zip, that's fine
        return max_found

    @staticmethod
    def _get_extension(filename: str) -> str:
        """Get file extension, handling double extensions like .tar.gz."""
        name = filename.lower()
        if name.endswith(".tar.gz"):
            return ".tar.gz"
        return Path(filename).suffix
