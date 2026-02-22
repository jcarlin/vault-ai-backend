"""GPG signature verification for update bundles."""

import shutil
import tempfile
from pathlib import Path

import structlog

from app.config import settings
from app.core.exceptions import VaultError

logger = structlog.get_logger()


class GPGVerifier:
    """Verifies detached GPG signatures using python-gnupg."""

    def __init__(self, public_key_path: str | None = None):
        self._public_key_path = Path(
            public_key_path or settings.vault_gpg_public_key_path
        )
        self._gpg = None
        self._key_imported = False

    def _ensure_gpg(self):
        """Lazily initialize GPG instance with isolated keyring."""
        if self._gpg is not None:
            return

        # Check if gpg binary is available
        if shutil.which("gpg") is None:
            raise VaultError(
                code="gpg_unavailable",
                message="GPG binary not found. Install gnupg to verify update signatures.",
                status=503,
            )

        import gnupg

        # Use a temporary keyring to avoid polluting the system keyring
        self._gnupghome = tempfile.mkdtemp(prefix="vault-gpg-")
        self._gpg = gnupg.GPG(gnupghome=self._gnupghome)

    def _ensure_key_imported(self):
        """Import the public key if not already done."""
        self._ensure_gpg()

        if self._key_imported:
            return

        if not self._public_key_path.exists():
            raise VaultError(
                code="gpg_key_missing",
                message=f"GPG public key not found at {self._public_key_path}",
                status=503,
            )

        key_data = self._public_key_path.read_text()
        result = self._gpg.import_keys(key_data)

        if result.count == 0:
            raise VaultError(
                code="gpg_key_import_failed",
                message="Failed to import GPG public key. Check key format.",
                status=503,
            )

        self._key_imported = True
        logger.info(
            "gpg_key_imported",
            fingerprints=result.fingerprints,
            count=result.count,
        )

    def verify(self, bundle_path: str, sig_path: str) -> bool:
        """Verify a detached GPG signature.

        Args:
            bundle_path: Path to the .tar bundle file.
            sig_path: Path to the .tar.sig signature file.

        Returns:
            True if signature is valid.

        Raises:
            VaultError if GPG is unavailable, key missing, or sig file missing.
        """
        bundle = Path(bundle_path)
        sig = Path(sig_path)

        if not bundle.exists():
            raise VaultError(
                code="bundle_not_found",
                message=f"Bundle file not found: {bundle_path}",
                status=404,
            )

        if not sig.exists():
            raise VaultError(
                code="signature_not_found",
                message=f"Signature file not found: {sig_path}",
                status=400,
            )

        self._ensure_key_imported()

        with open(sig, "rb") as sig_file:
            result = self._gpg.verify_file(sig_file, data_filename=str(bundle))

        if result.valid:
            logger.info(
                "gpg_signature_valid",
                bundle=str(bundle),
                fingerprint=result.fingerprint,
            )
            return True
        else:
            logger.warning(
                "gpg_signature_invalid",
                bundle=str(bundle),
                status=result.status,
                stderr=result.stderr if hasattr(result, "stderr") else None,
            )
            return False

    def is_available(self) -> bool:
        """Check if GPG verification is available (binary + key present)."""
        try:
            self._ensure_gpg()
            return self._public_key_path.exists()
        except VaultError:
            return False

    def cleanup(self):
        """Remove temporary keyring."""
        if hasattr(self, "_gnupghome"):
            import shutil as sh

            sh.rmtree(self._gnupghome, ignore_errors=True)
