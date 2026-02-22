"""Update bundle parsing: manifest validation, checksum verification, component extraction."""

import hashlib
import json
import tarfile
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

from app.core.exceptions import VaultError

logger = structlog.get_logger()

# Components that can appear in a bundle
VALID_COMPONENTS = {
    "backend",
    "frontend",
    "config",
    "containers",
    "packages",
    "signatures",
    "migrations",
    "models",
}


class ManifestFile(BaseModel):
    """A single file entry in the bundle manifest."""

    path: str
    sha256: str
    size: int = 0


class BundleManifest(BaseModel):
    """Parsed manifest.json from an update bundle."""

    version: str
    min_compatible_version: str = "0.0.0"
    created_at: str = ""
    changelog: str = ""
    components: dict[str, bool] = Field(default_factory=dict)
    files: list[ManifestFile] = Field(default_factory=list)


class UpdateBundle:
    """Parses and validates an update bundle (.tar archive)."""

    def __init__(self, bundle_path: Path):
        self._path = bundle_path
        self._manifest: BundleManifest | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def manifest(self) -> BundleManifest | None:
        return self._manifest

    @property
    def version(self) -> str:
        if self._manifest is None:
            raise VaultError(
                code="bundle_not_parsed",
                message="Bundle manifest has not been parsed yet.",
                status=400,
            )
        return self._manifest.version

    @property
    def size_bytes(self) -> int:
        return self._path.stat().st_size if self._path.exists() else 0

    def parse_manifest(self) -> BundleManifest:
        """Extract and parse manifest.json from the tar archive."""
        if not self._path.exists():
            raise VaultError(
                code="bundle_not_found",
                message=f"Bundle file not found: {self._path}",
                status=404,
            )

        try:
            with tarfile.open(str(self._path), "r:") as tar:
                manifest_member = self._find_manifest(tar)
                if manifest_member is None:
                    raise VaultError(
                        code="invalid_bundle",
                        message="Bundle does not contain a manifest.json file.",
                        status=400,
                    )

                f = tar.extractfile(manifest_member)
                if f is None:
                    raise VaultError(
                        code="invalid_bundle",
                        message="Cannot read manifest.json from bundle.",
                        status=400,
                    )

                raw = json.loads(f.read().decode("utf-8"))
                self._manifest = BundleManifest(**raw)
                logger.info(
                    "bundle_manifest_parsed",
                    version=self._manifest.version,
                    components=self._manifest.components,
                )
                return self._manifest

        except tarfile.TarError as e:
            raise VaultError(
                code="invalid_bundle",
                message=f"Failed to open bundle archive: {e}",
                status=400,
            )
        except json.JSONDecodeError as e:
            raise VaultError(
                code="invalid_bundle",
                message=f"Invalid JSON in manifest.json: {e}",
                status=400,
            )

    def extract_to(self, target_dir: Path) -> None:
        """Extract entire bundle to target directory."""
        if not self._path.exists():
            raise VaultError(
                code="bundle_not_found",
                message=f"Bundle file not found: {self._path}",
                status=404,
            )

        target_dir.mkdir(parents=True, exist_ok=True)

        with tarfile.open(str(self._path), "r:") as tar:
            # Security: filter out absolute paths and path traversal
            safe_members = []
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in member.name:
                    logger.warning(
                        "bundle_path_traversal_blocked",
                        member=member.name,
                    )
                    continue
                safe_members.append(member)

            tar.extractall(path=str(target_dir), members=safe_members)

        logger.info("bundle_extracted", target=str(target_dir), files=len(safe_members))

    def verify_checksums(self, extracted_dir: Path) -> list[str]:
        """Verify SHA-256 checksums of extracted files. Returns list of errors."""
        if self._manifest is None:
            raise VaultError(
                code="bundle_not_parsed",
                message="Call parse_manifest() before verify_checksums().",
                status=400,
            )

        errors = []
        for file_entry in self._manifest.files:
            file_path = extracted_dir / file_entry.path
            if not file_path.exists():
                errors.append(f"Missing file: {file_entry.path}")
                continue

            actual_hash = self._sha256(file_path)
            if actual_hash != file_entry.sha256:
                errors.append(
                    f"Checksum mismatch: {file_entry.path} "
                    f"(expected {file_entry.sha256[:12]}..., got {actual_hash[:12]}...)"
                )

        if errors:
            logger.warning("bundle_checksum_errors", errors=errors)
        else:
            logger.info("bundle_checksums_verified", file_count=len(self._manifest.files))

        return errors

    @staticmethod
    def _sha256(file_path: Path) -> str:
        """Compute SHA-256 hash of a file."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _find_manifest(tar: tarfile.TarFile) -> tarfile.TarInfo | None:
        """Find manifest.json in the tar, accounting for top-level directory prefix."""
        for member in tar.getmembers():
            name = member.name
            # manifest.json or vault-update-X.Y.Z/manifest.json
            if name == "manifest.json" or name.endswith("/manifest.json"):
                parts = name.split("/")
                if len(parts) <= 2:
                    return member
        return None
