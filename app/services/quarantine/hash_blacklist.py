"""SHA-256 hash blacklist for known-bad files."""

import json
from pathlib import Path

import structlog

logger = structlog.get_logger()


class HashBlacklist:
    """Checks file hashes against a JSON-backed set of known-bad SHA-256 hashes."""

    def __init__(self, blacklist_path: str | None = None):
        self._path = Path(blacklist_path) if blacklist_path else None
        self._hashes: set[str] = set()

    def load(self) -> bool:
        """Load hashes from JSON file.

        Expected format: {"hashes": ["sha256hex1", "sha256hex2", ...]}
        """
        if not self._path or not self._path.exists():
            return False

        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            raw_hashes = data.get("hashes", [])
            self._hashes = {h.lower().strip() for h in raw_hashes if isinstance(h, str)}
            logger.info("hash_blacklist_loaded", count=len(self._hashes))
            return True
        except Exception as e:
            logger.warning("hash_blacklist_load_error", error=str(e))
            return False

    def is_blacklisted(self, sha256_hex: str) -> bool:
        """Check if a SHA-256 hash is in the blacklist."""
        return sha256_hex.lower().strip() in self._hashes

    @property
    def count(self) -> int:
        return len(self._hashes)
