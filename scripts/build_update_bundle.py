#!/usr/bin/env python3
"""Build a signed update bundle for air-gapped deployment.

Usage:
    python scripts/build_update_bundle.py --version 1.1.0 --backend-dir . --output /tmp/bundle
    python scripts/build_update_bundle.py --version 1.1.0 --backend-dir . --frontend-dir ../vault-ai-frontend --output /tmp/bundle --sign --gpg-key ~/.gnupg/vault-signing-key
"""

import hashlib
import io
import json
import os
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import typer

app = typer.Typer(help="Build Vault AI update bundles")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _add_directory_to_tar(
    tar: tarfile.TarFile,
    source_dir: Path,
    archive_prefix: str,
    component_name: str,
    manifest_files: list,
    exclude_patterns: set | None = None,
):
    """Add a directory tree to the tar, recording files in manifest."""
    exclude = exclude_patterns or {
        ".git", "__pycache__", ".pytest_cache", "*.pyc",
        ".venv", ".env", "node_modules", ".next",
        "data", "*.db", "*.db-journal",
    }

    for root, dirs, files in os.walk(source_dir):
        # Filter excluded dirs
        dirs[:] = [d for d in dirs if d not in exclude and not d.startswith(".")]

        for fname in files:
            if any(fname.endswith(pat.lstrip("*")) for pat in exclude if pat.startswith("*")):
                continue

            file_path = Path(root) / fname
            rel_path = file_path.relative_to(source_dir)
            archive_path = f"{archive_prefix}/{component_name}/{rel_path}"

            tar.add(str(file_path), arcname=archive_path)
            manifest_files.append({
                "path": f"{component_name}/{rel_path}",
                "sha256": _sha256(file_path),
                "size": file_path.stat().st_size,
            })


@app.command()
def build(
    version: str = typer.Option(..., "--version", help="Bundle version (semver)"),
    output: str = typer.Option(..., "--output", help="Output directory"),
    backend_dir: str = typer.Option(None, "--backend-dir", help="Backend source directory"),
    frontend_dir: str = typer.Option(None, "--frontend-dir", help="Frontend build directory"),
    config_dir: str = typer.Option(None, "--config-dir", help="Config files directory"),
    containers_dir: str = typer.Option(None, "--containers-dir", help="Container images directory"),
    signatures_dir: str = typer.Option(None, "--signatures-dir", help="ClamAV/YARA signatures directory"),
    migrations_dir: str = typer.Option(None, "--migrations-dir", help="Alembic migrations directory"),
    changelog: str = typer.Option("", "--changelog", help="Changelog text"),
    min_version: str = typer.Option("1.0.0", "--min-version", help="Minimum compatible version"),
    sign: bool = typer.Option(False, "--sign", help="Sign with GPG"),
    gpg_key: str = typer.Option(None, "--gpg-key", help="GPG key ID or path for signing"),
):
    """Build a Vault AI update bundle (.tar + .sig)."""
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)

    bundle_name = f"vault-update-{version}"
    tar_path = output_path / f"{bundle_name}.tar"

    manifest_files: list[dict] = []
    components: dict[str, bool] = {}

    typer.echo(f"Building update bundle v{version}...")

    with tarfile.open(str(tar_path), "w:") as tar:
        # Add components
        if backend_dir:
            typer.echo("  Adding backend...")
            _add_directory_to_tar(
                tar, Path(backend_dir), bundle_name, "backend", manifest_files,
            )
            components["backend"] = True

        if frontend_dir:
            typer.echo("  Adding frontend...")
            _add_directory_to_tar(
                tar, Path(frontend_dir), bundle_name, "frontend", manifest_files,
            )
            components["frontend"] = True

        if config_dir:
            typer.echo("  Adding config...")
            _add_directory_to_tar(
                tar, Path(config_dir), bundle_name, "config", manifest_files,
            )
            components["config"] = True

        if containers_dir:
            typer.echo("  Adding containers...")
            _add_directory_to_tar(
                tar, Path(containers_dir), bundle_name, "containers", manifest_files,
            )
            components["containers"] = True

        if signatures_dir:
            typer.echo("  Adding signatures...")
            _add_directory_to_tar(
                tar, Path(signatures_dir), bundle_name, "signatures", manifest_files,
            )
            components["signatures"] = True

        if migrations_dir:
            typer.echo("  Adding migrations...")
            _add_directory_to_tar(
                tar, Path(migrations_dir), bundle_name, "migrations", manifest_files,
            )
            components["migrations"] = True

        # Build and add manifest
        manifest = {
            "version": version,
            "min_compatible_version": min_version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "changelog": changelog,
            "components": components,
            "files": manifest_files,
        }
        manifest_bytes = json.dumps(manifest, indent=2).encode()
        info = tarfile.TarInfo(name=f"{bundle_name}/manifest.json")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))

    typer.echo(f"  Bundle: {tar_path} ({tar_path.stat().st_size / 1024 / 1024:.1f} MB)")

    # GPG signing
    if sign:
        sig_path = output_path / f"{bundle_name}.tar.sig"
        typer.echo("  Signing with GPG...")
        cmd = ["gpg", "--detach-sign", "--armor"]
        if gpg_key:
            cmd.extend(["--default-key", gpg_key])
        cmd.extend(["--output", str(sig_path), str(tar_path)])
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            typer.echo(f"  GPG signing failed: {result.stderr}", err=True)
            raise typer.Exit(1)
        typer.echo(f"  Signature: {sig_path}")

    typer.echo(f"\nBundle built successfully: {tar_path}")
    typer.echo(f"Components: {', '.join(components.keys())}")
    typer.echo(f"Files: {len(manifest_files)}")


if __name__ == "__main__":
    app()
