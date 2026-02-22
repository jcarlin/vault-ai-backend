"""ClamAV daemon client wrapper with graceful fallback."""

import socket
import struct
from pathlib import Path

import structlog

logger = structlog.get_logger()

# EICAR test string — standard antivirus test pattern
EICAR_SIGNATURE = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class ClamAVClient:
    """Client for ClamAV daemon via Unix socket.

    Uses the INSTREAM command to send file data to clamd and get scan results.
    Gracefully returns unavailable status if the daemon is not running.
    """

    def __init__(self, socket_path: str = "/var/run/clamav/clamd.ctl"):
        self._socket_path = socket_path

    def is_available(self) -> bool:
        """Check if ClamAV daemon is reachable."""
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(self._socket_path)
            sock.sendall(b"zPING\0")
            response = sock.recv(1024)
            sock.close()
            return response.strip(b"\0").strip() == b"PONG"
        except (OSError, ConnectionRefusedError, FileNotFoundError):
            return False

    def scan_file(self, file_path: Path) -> dict:
        """Scan a file via ClamAV INSTREAM command.

        Returns:
            {"status": "clean"} or {"status": "infected", "threat": "Win.Test.EICAR"}
            or {"status": "unavailable", "message": "..."} if daemon is down.
        """
        try:
            data = file_path.read_bytes()
            return self.scan_bytes(data)
        except (OSError, ConnectionRefusedError, FileNotFoundError) as e:
            logger.warning("clamav_unavailable", error=str(e))
            return {"status": "unavailable", "message": str(e)}

    def scan_bytes(self, data: bytes) -> dict:
        """Scan raw bytes via ClamAV INSTREAM command."""
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(30.0)
            sock.connect(self._socket_path)

            # Send INSTREAM command
            sock.sendall(b"zINSTREAM\0")

            # Send data in chunks (ClamAV protocol: 4-byte big-endian length + data)
            chunk_size = 1024 * 1024  # 1MB chunks
            offset = 0
            while offset < len(data):
                chunk = data[offset:offset + chunk_size]
                sock.sendall(struct.pack(">I", len(chunk)))
                sock.sendall(chunk)
                offset += chunk_size

            # Send zero-length chunk to signal end
            sock.sendall(struct.pack(">I", 0))

            # Read response
            response = b""
            while True:
                buf = sock.recv(4096)
                if not buf:
                    break
                response += buf
                if b"\0" in buf:
                    break

            sock.close()

            # Parse response: "stream: OK" or "stream: Win.Test.EICAR_HDB-1 FOUND"
            result_text = response.strip(b"\0").decode("utf-8", errors="replace").strip()

            if "OK" in result_text:
                return {"status": "clean"}
            elif "FOUND" in result_text:
                # Extract threat name: "stream: ThreatName FOUND"
                parts = result_text.split(":")
                if len(parts) > 1:
                    threat_part = parts[1].strip()
                    threat_name = threat_part.replace("FOUND", "").strip()
                else:
                    threat_name = "Unknown"
                return {"status": "infected", "threat": threat_name}
            else:
                return {"status": "error", "message": result_text}

        except (OSError, ConnectionRefusedError, FileNotFoundError) as e:
            logger.warning("clamav_unavailable", error=str(e))
            return {"status": "unavailable", "message": str(e)}
