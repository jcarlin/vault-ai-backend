"""Fake ClamAV client for testing — detects EICAR test string, reports clean otherwise."""

from pathlib import Path

# Standard EICAR test signature
EICAR_SIGNATURE = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class FakeClamAVClient:
    """Mock ClamAV daemon that detects EICAR test string."""

    def __init__(self, available: bool = True):
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def scan_file(self, file_path: Path) -> dict:
        if not self._available:
            return {"status": "unavailable", "message": "ClamAV daemon not running (mock)"}
        data = file_path.read_bytes()
        return self.scan_bytes(data)

    def scan_bytes(self, data: bytes) -> dict:
        if not self._available:
            return {"status": "unavailable", "message": "ClamAV daemon not running (mock)"}
        if EICAR_SIGNATURE in data:
            return {"status": "infected", "threat": "Win.Test.EICAR_HDB-1"}
        return {"status": "clean"}
