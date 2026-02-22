"""USB/external drive scanner for update bundles."""

import platform
import re
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Default mount points to scan for USB/external drives
DEFAULT_SCAN_PATHS = ["/media", "/mnt", "/run/media"]

# Bundle filename pattern: vault-update-X.Y.Z.tar
BUNDLE_PATTERN = re.compile(r"^vault-update-(.+)\.tar$")


class USBScanner:
    """Scans mounted volumes for update bundles."""

    def __init__(self, scan_paths: list[str] | None = None):
        self._scan_paths = scan_paths or DEFAULT_SCAN_PATHS

    def scan(self) -> list[dict]:
        """Find update bundles on mounted volumes.

        Returns list of dicts with keys: version, bundle_path, sig_path, size_bytes.
        On macOS, returns empty list (no USB automount under /media).
        """
        if platform.system() == "Darwin":
            logger.debug("usb_scan_skipped", reason="macOS development environment")
            return []

        found = []
        for scan_root in self._scan_paths:
            root = Path(scan_root)
            if not root.exists():
                continue

            # Walk one level deep under mount points (e.g. /media/usb/)
            for mount_point in self._enumerate_mount_points(root):
                bundles = self._scan_directory(mount_point)
                found.extend(bundles)

        logger.info("usb_scan_complete", bundles_found=len(found))
        return found

    def scan_path(self, directory: str) -> list[dict]:
        """Scan a specific directory for update bundles (for testing or manual paths)."""
        d = Path(directory)
        if not d.exists():
            return []
        return self._scan_directory(d)

    def _enumerate_mount_points(self, root: Path) -> list[Path]:
        """List directories one level under a scan root."""
        if not root.is_dir():
            return []
        try:
            return [p for p in root.iterdir() if p.is_dir()]
        except PermissionError:
            logger.warning("scan_permission_denied", path=str(root))
            return []

    def _scan_directory(self, directory: Path) -> list[dict]:
        """Find vault-update-*.tar files with matching .sig files in a directory."""
        bundles = []
        try:
            for f in directory.iterdir():
                if not f.is_file():
                    continue
                match = BUNDLE_PATTERN.match(f.name)
                if not match:
                    continue

                version = match.group(1)
                sig_path = f.with_suffix(".tar.sig")

                bundles.append({
                    "version": version,
                    "bundle_path": str(f),
                    "sig_path": str(sig_path) if sig_path.exists() else None,
                    "size_bytes": f.stat().st_size,
                })
        except PermissionError:
            logger.warning("scan_permission_denied", path=str(directory))

        return bundles
