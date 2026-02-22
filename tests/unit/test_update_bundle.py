"""Unit tests for the UpdateBundle class (app/services/update/bundle.py)."""

import hashlib
import json
from pathlib import Path

import pytest

from app.core.exceptions import VaultError
from app.services.update.bundle import UpdateBundle
from tests.fixtures.updates import make_path_traversal_bundle, make_test_bundle


class TestParseManifest:
    def test_parse_valid_manifest(self, tmp_path):
        """Parse a well-formed manifest from a .tar bundle."""
        bundle_path = make_test_bundle(tmp_path, version="2.1.0")
        bundle = UpdateBundle(bundle_path)
        manifest = bundle.parse_manifest()

        assert manifest.version == "2.1.0"
        assert manifest.changelog == "Test update"
        assert manifest.min_compatible_version == "1.0.0"
        assert manifest.created_at == "2026-02-20T00:00:00Z"

    def test_extract_components_from_manifest(self, tmp_path):
        """Components dict is correctly extracted from the manifest."""
        bundle_path = make_test_bundle(
            tmp_path,
            manifest_override={
                "version": "1.5.0",
                "components": {"backend": True, "frontend": True, "config": False},
                "files": [],
            },
        )
        bundle = UpdateBundle(bundle_path)
        manifest = bundle.parse_manifest()

        assert manifest.components == {"backend": True, "frontend": True, "config": False}
        assert manifest.components["backend"] is True
        assert manifest.components["config"] is False

    def test_version_extraction(self, tmp_path):
        """Version property returns the parsed version string."""
        bundle_path = make_test_bundle(tmp_path, version="3.0.1")
        bundle = UpdateBundle(bundle_path)
        bundle.parse_manifest()

        assert bundle.version == "3.0.1"

    def test_version_before_parse_raises(self, tmp_path):
        """Accessing version before parse_manifest() raises VaultError."""
        bundle_path = make_test_bundle(tmp_path)
        bundle = UpdateBundle(bundle_path)

        with pytest.raises(VaultError, match="not been parsed"):
            _ = bundle.version

    def test_malformed_manifest_raises(self, tmp_path):
        """Invalid JSON in manifest.json raises VaultError."""
        bundle_path = make_test_bundle(tmp_path, corrupt_manifest=True)
        bundle = UpdateBundle(bundle_path)

        with pytest.raises(VaultError, match="Invalid JSON"):
            bundle.parse_manifest()

    def test_missing_manifest_raises(self, tmp_path):
        """Bundle without manifest.json raises VaultError."""
        bundle_path = make_test_bundle(tmp_path, include_manifest=False)
        bundle = UpdateBundle(bundle_path)

        with pytest.raises(VaultError, match="does not contain a manifest.json"):
            bundle.parse_manifest()

    def test_nonexistent_bundle_path_raises(self, tmp_path):
        """Non-existent bundle path raises VaultError."""
        fake_path = tmp_path / "does-not-exist.tar"
        bundle = UpdateBundle(fake_path)

        with pytest.raises(VaultError, match="not found"):
            bundle.parse_manifest()


class TestChecksumVerification:
    def test_checksum_passes_for_valid_files(self, tmp_path):
        """Checksum verification passes when extracted files match manifest hashes."""
        content = b"print('hello world')"
        files = {"backend/app.py": content}
        bundle_path = make_test_bundle(tmp_path, files=files)

        bundle = UpdateBundle(bundle_path)
        bundle.parse_manifest()

        # Extract to a directory
        extract_dir = tmp_path / "extracted"
        bundle.extract_to(extract_dir)

        # Find the content directory (nested under vault-update-X.Y.Z/)
        content_dir = extract_dir / "vault-update-1.2.0"

        errors = bundle.verify_checksums(content_dir)
        assert errors == []

    def test_checksum_fails_for_tampered_file(self, tmp_path):
        """Checksum verification fails when file content has been modified."""
        content = b"original content"
        files = {"backend/app.py": content}
        bundle_path = make_test_bundle(tmp_path, files=files)

        bundle = UpdateBundle(bundle_path)
        bundle.parse_manifest()

        # Extract then tamper
        extract_dir = tmp_path / "extracted"
        bundle.extract_to(extract_dir)

        content_dir = extract_dir / "vault-update-1.2.0"
        tampered_file = content_dir / "backend" / "app.py"
        tampered_file.write_bytes(b"tampered content")

        errors = bundle.verify_checksums(content_dir)
        assert len(errors) == 1
        assert "Checksum mismatch" in errors[0]
        assert "backend/app.py" in errors[0]


class TestExtraction:
    def test_path_traversal_members_filtered(self, tmp_path):
        """Absolute paths and '..' path traversal members are filtered out during extraction."""
        bundle_path = make_path_traversal_bundle(tmp_path, version="1.0.0")
        bundle = UpdateBundle(bundle_path)

        extract_dir = tmp_path / "safe_extract"
        bundle.extract_to(extract_dir)

        # The safe file should be extracted
        safe_file = extract_dir / "vault-update-1.0.0" / "backend" / "safe.py"
        assert safe_file.exists()
        assert safe_file.read_bytes() == b"safe content"

        # Malicious paths should NOT exist in the extract directory
        assert not (extract_dir / "etc" / "passwd").exists()
        assert not (extract_dir / "etc" / "shadow").exists()

        # Also check that nothing was written to /etc/ (absolute path)
        # (The tar library with our filter should never extract absolute paths)
