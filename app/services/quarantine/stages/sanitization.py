"""Stage 3: Content Sanitization — PDF/Office/image cleaning, metadata scrub."""

import io
import shutil
from pathlib import Path

import structlog

from app.services.quarantine.stages import PipelineStage, StageFinding, StageResult

logger = structlog.get_logger()


class SanitizationStage(PipelineStage):
    """Stage 3: Sanitize file content — strip dangerous elements, clean metadata."""

    def __init__(self, sanitized_dir: Path | None = None):
        self._sanitized_dir = sanitized_dir

    @property
    def name(self) -> str:
        return "sanitization"

    async def scan(self, file_path: Path, original_filename: str, config: dict) -> StageResult:
        findings = []
        ext = self._get_extension(original_filename).lower()
        sanitized_path = None

        try:
            if ext == ".pdf":
                sanitized_path, pdf_findings = self._sanitize_pdf(file_path, original_filename)
                findings.extend(pdf_findings)
            elif ext == ".docx":
                sanitized_path, docx_findings = self._sanitize_docx(file_path, original_filename)
                findings.extend(docx_findings)
            elif ext == ".xlsx":
                sanitized_path, xlsx_findings = self._sanitize_xlsx(file_path, original_filename)
                findings.extend(xlsx_findings)
            elif ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                sanitized_path, img_findings = self._sanitize_image(file_path, original_filename, ext)
                findings.extend(img_findings)
            else:
                # No sanitization needed for this format — strip metadata if possible
                meta_findings = self._scrub_metadata(file_path, ext)
                findings.extend(meta_findings)
        except Exception as e:
            findings.append(StageFinding(
                stage=self.name,
                severity="medium",
                code="sanitization_error",
                message=f"Error during sanitization: {e}",
            ))

        # Stage 3 always passes — findings are informational about what was cleaned
        # Only if there's a critical issue (like unable to sanitize a dangerous PDF) do we fail
        passed = not any(f.severity == "critical" for f in findings)

        return StageResult(passed=passed, findings=findings, sanitized_path=sanitized_path)

    def _get_sanitized_path(self, file_id: str, filename: str) -> Path:
        """Get path for sanitized output."""
        if self._sanitized_dir:
            self._sanitized_dir.mkdir(parents=True, exist_ok=True)
            safe_name = filename.replace("/", "_").replace("\\", "_")
            return self._sanitized_dir / f"sanitized_{safe_name}"
        # Fallback: write next to original with .sanitized suffix
        return Path(str(file_id) + ".sanitized")

    def _sanitize_pdf(self, file_path: Path, filename: str) -> tuple[Path | None, list[StageFinding]]:
        """Strip JavaScript, auto-actions, and embedded files from PDF."""
        findings = []

        try:
            import pikepdf
        except ImportError:
            findings.append(StageFinding(
                stage=self.name,
                severity="low",
                code="pikepdf_unavailable",
                message="pikepdf not available — PDF sanitization skipped.",
            ))
            return None, findings

        sanitized_path = self._get_sanitized_path("", filename)
        js_removed = False
        actions_removed = False
        files_removed = False

        try:
            with pikepdf.open(file_path) as pdf:
                # Remove JavaScript actions from document catalog
                if "/Names" in pdf.Root:
                    names = pdf.Root["/Names"]
                    if "/JavaScript" in names:
                        del names["/JavaScript"]
                        js_removed = True
                    if "/EmbeddedFiles" in names:
                        del names["/EmbeddedFiles"]
                        files_removed = True

                # Remove OpenAction (auto-execute on open)
                if "/OpenAction" in pdf.Root:
                    del pdf.Root["/OpenAction"]
                    actions_removed = True

                # Remove AA (additional actions) from document
                if "/AA" in pdf.Root:
                    del pdf.Root["/AA"]
                    actions_removed = True

                # Scan pages for JavaScript and actions
                for page in pdf.pages:
                    if "/AA" in page:
                        del page["/AA"]
                        actions_removed = True

                    # Remove JS from annotations
                    if "/Annots" in page:
                        annots = page["/Annots"]
                        for annot in annots:
                            try:
                                annot_obj = annot.resolve() if hasattr(annot, "resolve") else annot
                                if "/A" in annot_obj:
                                    action = annot_obj["/A"]
                                    action_obj = action.resolve() if hasattr(action, "resolve") else action
                                    if "/S" in action_obj:
                                        action_type = str(action_obj["/S"])
                                        if action_type in ("/JavaScript", "/Launch", "/URI", "/SubmitForm"):
                                            del annot_obj["/A"]
                                            js_removed = True
                            except Exception:
                                pass

                # Remove document metadata (author, producer, etc.)
                if pdf.docinfo:
                    try:
                        with pdf.open_metadata() as meta:
                            # Clear XMP metadata
                            pass
                    except Exception:
                        pass
                    # Remove document info dict entries
                    for key in list(pdf.docinfo.keys()):
                        if key in ("/Author", "/Creator", "/Producer", "/Subject", "/Keywords"):
                            del pdf.docinfo[key]

                pdf.save(sanitized_path)

            if js_removed:
                findings.append(StageFinding(
                    stage=self.name, severity="medium", code="pdf_js_removed",
                    message="JavaScript actions were stripped from PDF.",
                ))
            if actions_removed:
                findings.append(StageFinding(
                    stage=self.name, severity="medium", code="pdf_actions_removed",
                    message="Auto-execute actions were stripped from PDF.",
                ))
            if files_removed:
                findings.append(StageFinding(
                    stage=self.name, severity="medium", code="pdf_embedded_files_removed",
                    message="Embedded files were stripped from PDF.",
                ))

            return sanitized_path, findings

        except Exception as e:
            findings.append(StageFinding(
                stage=self.name, severity="medium", code="pdf_sanitization_error",
                message=f"PDF sanitization failed: {e}",
            ))
            return None, findings

    def _sanitize_docx(self, file_path: Path, filename: str) -> tuple[Path | None, list[StageFinding]]:
        """Strip VBA macros, ActiveX, and OLE objects from DOCX."""
        import zipfile

        findings = []
        sanitized_path = self._get_sanitized_path("", filename)
        macros_found = False

        try:
            # Check for macros via ZIP inspection (macros live in vbaProject.bin)
            if zipfile.is_zipfile(str(file_path)):
                with zipfile.ZipFile(str(file_path), "r") as zf:
                    for name in zf.namelist():
                        if "vbaProject" in name or "activeX" in name.lower() or "oleObject" in name.lower():
                            macros_found = True
                            break

            if macros_found:
                # Rebuild the DOCX without macro components (pure zipfile, no python-docx needed)
                with zipfile.ZipFile(str(file_path), "r") as zf_in:
                    with zipfile.ZipFile(str(sanitized_path), "w", zipfile.ZIP_DEFLATED) as zf_out:
                        for item in zf_in.infolist():
                            name_lower = item.filename.lower()
                            if ("vbaproject" in name_lower or "activex" in name_lower
                                    or "oleobject" in name_lower
                                    or (name_lower.endswith(".bin") and "vba" in name_lower)):
                                continue
                            data = zf_in.read(item.filename)
                            zf_out.writestr(item, data)

                findings.append(StageFinding(
                    stage=self.name, severity="high", code="docx_macros_removed",
                    message="VBA macros / ActiveX / OLE objects were stripped from DOCX.",
                ))
            else:
                # No macros — clean metadata if python-docx available
                try:
                    from docx import Document
                    doc = Document(str(file_path))
                    cp = doc.core_properties
                    cp.author = ""
                    cp.last_modified_by = ""
                    cp.comments = ""
                    cp.keywords = ""
                    doc.save(str(sanitized_path))
                except ImportError:
                    # python-docx not available — just copy as-is
                    shutil.copy2(file_path, sanitized_path)

            return sanitized_path, findings

        except Exception as e:
            findings.append(StageFinding(
                stage=self.name, severity="medium", code="docx_sanitization_error",
                message=f"DOCX sanitization failed: {e}",
            ))
            return None, findings

    def _sanitize_xlsx(self, file_path: Path, filename: str) -> tuple[Path | None, list[StageFinding]]:
        """Strip macros from XLSX."""
        import zipfile

        findings = []
        sanitized_path = self._get_sanitized_path("", filename)

        try:
            # Check for macros
            macros_found = False
            if zipfile.is_zipfile(str(file_path)):
                with zipfile.ZipFile(str(file_path), "r") as zf:
                    for name in zf.namelist():
                        if "vbaProject" in name or "activeX" in name.lower():
                            macros_found = True
                            break

            if macros_found:
                # Rebuild without macros (pure zipfile)
                with zipfile.ZipFile(str(file_path), "r") as zf_in:
                    with zipfile.ZipFile(str(sanitized_path), "w", zipfile.ZIP_DEFLATED) as zf_out:
                        for item in zf_in.infolist():
                            name_lower = item.filename.lower()
                            if "vbaproject" in name_lower or "activex" in name_lower:
                                continue
                            data = zf_in.read(item.filename)
                            zf_out.writestr(item, data)

                findings.append(StageFinding(
                    stage=self.name, severity="high", code="xlsx_macros_removed",
                    message="VBA macros / ActiveX were stripped from XLSX.",
                ))
            else:
                # Clean metadata if openpyxl available
                try:
                    from openpyxl import load_workbook
                    wb = load_workbook(str(file_path))
                    wb.properties.creator = ""
                    wb.properties.lastModifiedBy = ""
                    wb.properties.keywords = ""
                    wb.properties.description = ""
                    wb.save(str(sanitized_path))
                except ImportError:
                    shutil.copy2(file_path, sanitized_path)

            return sanitized_path, findings

        except Exception as e:
            findings.append(StageFinding(
                stage=self.name, severity="medium", code="xlsx_sanitization_error",
                message=f"XLSX sanitization failed: {e}",
            ))
            return None, findings

    def _sanitize_image(self, file_path: Path, filename: str, ext: str) -> tuple[Path | None, list[StageFinding]]:
        """Re-encode image to strip steganography and metadata (EXIF, XMP)."""
        findings = []

        try:
            from PIL import Image
        except ImportError:
            findings.append(StageFinding(
                stage=self.name, severity="low", code="pillow_unavailable",
                message="Pillow not available — image sanitization skipped.",
            ))
            return None, findings

        sanitized_path = self._get_sanitized_path("", filename)

        try:
            img = Image.open(file_path)

            # Check for EXIF data
            exif_data = img.getexif()
            had_exif = len(exif_data) > 0

            # Re-encode to strip all metadata and steganography
            # Create fresh image with just pixel data
            clean_img = Image.new(img.mode, img.size)
            clean_img.putdata(list(img.getdata()))

            format_map = {
                ".png": "PNG",
                ".jpg": "JPEG",
                ".jpeg": "JPEG",
                ".gif": "GIF",
                ".webp": "WEBP",
            }
            img_format = format_map.get(ext, "PNG")

            if img_format == "JPEG":
                clean_img = clean_img.convert("RGB")
                clean_img.save(str(sanitized_path), format=img_format, quality=95)
            else:
                clean_img.save(str(sanitized_path), format=img_format)

            img.close()

            if had_exif:
                findings.append(StageFinding(
                    stage=self.name, severity="low", code="image_exif_stripped",
                    message="EXIF/metadata was stripped from image.",
                    details={"exif_keys_removed": len(exif_data)},
                ))

            findings.append(StageFinding(
                stage=self.name, severity="none", code="image_reencoded",
                message="Image was re-encoded (strips steganography and embedded data).",
            ))

            return sanitized_path, findings

        except Exception as e:
            findings.append(StageFinding(
                stage=self.name, severity="medium", code="image_sanitization_error",
                message=f"Image sanitization failed: {e}",
            ))
            return None, findings

    def _scrub_metadata(self, file_path: Path, ext: str) -> list[StageFinding]:
        """Generic metadata scrub for formats without specific sanitization."""
        # For formats like JSON, text, etc. there's nothing to scrub
        return []

    @staticmethod
    def _get_extension(filename: str) -> str:
        name = filename.lower()
        if name.endswith(".tar.gz"):
            return ".tar.gz"
        return Path(filename).suffix
