"""Test fixtures and helpers for update mechanism tests."""

import hashlib
import io
import json
import tarfile
from pathlib import Path


def make_test_bundle(
    tmp_path: Path,
    version: str = "1.2.0",
    files: dict[str, bytes] | None = None,
    manifest_override: dict | None = None,
    include_manifest: bool = True,
    corrupt_manifest: bool = False,
) -> Path:
    """Create a minimal test .tar bundle.

    Args:
        tmp_path: pytest tmp_path fixture.
        version: Semantic version string for the bundle.
        files: Dict of {relative_path: content_bytes} for files in the bundle.
        manifest_override: Override entire manifest dict.
        include_manifest: If False, omit manifest.json from the bundle.
        corrupt_manifest: If True, write invalid JSON as the manifest.

    Returns:
        Path to the created .tar bundle file.
    """
    bundle_dir = f"vault-update-{version}"
    bundle_path = tmp_path / f"vault-update-{version}.tar"

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tar:
        # Add test files
        real_files = files or {"backend/main.py": b"print('hello')"}
        manifest_files = []
        for path, content in real_files.items():
            full_path = f"{bundle_dir}/{path}"
            info = tarfile.TarInfo(name=full_path)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
            manifest_files.append({
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            })

        # Add manifest
        if include_manifest:
            if corrupt_manifest:
                manifest_bytes = b"NOT VALID JSON {{{"
            else:
                manifest = manifest_override or {
                    "version": version,
                    "min_compatible_version": "1.0.0",
                    "created_at": "2026-02-20T00:00:00Z",
                    "changelog": "Test update",
                    "components": {"backend": True, "frontend": False},
                    "files": manifest_files,
                }
                manifest_bytes = json.dumps(manifest).encode()

            info = tarfile.TarInfo(name=f"{bundle_dir}/manifest.json")
            info.size = len(manifest_bytes)
            tar.addfile(info, io.BytesIO(manifest_bytes))

    bundle_path.write_bytes(buf.getvalue())
    return bundle_path


def make_path_traversal_bundle(tmp_path: Path, version: str = "1.2.0") -> Path:
    """Create a bundle with path traversal members (../etc/passwd style)."""
    bundle_dir = f"vault-update-{version}"
    bundle_path = tmp_path / f"vault-update-{version}.tar"

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tar:
        # Normal file
        normal_content = b"safe content"
        info = tarfile.TarInfo(name=f"{bundle_dir}/backend/safe.py")
        info.size = len(normal_content)
        tar.addfile(info, io.BytesIO(normal_content))

        # Absolute path member (malicious)
        bad_content = b"malicious"
        info = tarfile.TarInfo(name="/etc/passwd")
        info.size = len(bad_content)
        tar.addfile(info, io.BytesIO(bad_content))

        # Path traversal member (malicious)
        info2 = tarfile.TarInfo(name=f"{bundle_dir}/../../etc/shadow")
        info2.size = len(bad_content)
        tar.addfile(info2, io.BytesIO(bad_content))

        # Manifest
        manifest = {
            "version": version,
            "min_compatible_version": "1.0.0",
            "created_at": "2026-02-20T00:00:00Z",
            "changelog": "Test update",
            "components": {"backend": True},
            "files": [{
                "path": "backend/safe.py",
                "sha256": hashlib.sha256(normal_content).hexdigest(),
                "size": len(normal_content),
            }],
        }
        manifest_bytes = json.dumps(manifest).encode()
        info = tarfile.TarInfo(name=f"{bundle_dir}/manifest.json")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))

    bundle_path.write_bytes(buf.getvalue())
    return bundle_path
