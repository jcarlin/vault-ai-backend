"""Unit tests for the GPGVerifier class (app/services/update/gpg.py).

Note: These tests cover error paths only. Actual GPG crypto verification
would require real keypair generation, which is not practical for unit tests.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.exceptions import VaultError
from app.services.update.gpg import GPGVerifier


class TestIsAvailable:
    def test_returns_false_when_key_missing(self, tmp_path):
        """is_available() returns False when the public key file doesn't exist."""
        nonexistent_key = str(tmp_path / "nonexistent-key.pub")
        verifier = GPGVerifier(public_key_path=nonexistent_key)

        # Need gpg binary to be "found" for the first check,
        # but key file doesn't exist
        with patch("shutil.which", return_value="/usr/bin/gpg"):
            result = verifier.is_available()

        assert result is False

    def test_returns_false_when_gpg_binary_not_found(self, tmp_path):
        """is_available() returns False when gpg binary is not installed."""
        key_file = tmp_path / "vault-key.pub"
        key_file.write_text("fake key")
        verifier = GPGVerifier(public_key_path=str(key_file))

        with patch("shutil.which", return_value=None):
            result = verifier.is_available()

        assert result is False


class TestVerify:
    def test_verify_raises_when_bundle_not_found(self, tmp_path):
        """verify() raises VaultError when the bundle file doesn't exist."""
        key_file = tmp_path / "vault-key.pub"
        key_file.write_text("fake key")
        verifier = GPGVerifier(public_key_path=str(key_file))

        nonexistent_bundle = str(tmp_path / "missing-bundle.tar")
        sig_file = tmp_path / "missing-bundle.tar.sig"
        sig_file.write_bytes(b"fake sig")

        with pytest.raises(VaultError, match="Bundle file not found"):
            verifier.verify(nonexistent_bundle, str(sig_file))

    def test_verify_raises_when_sig_not_found(self, tmp_path):
        """verify() raises VaultError when the signature file doesn't exist."""
        key_file = tmp_path / "vault-key.pub"
        key_file.write_text("fake key")
        verifier = GPGVerifier(public_key_path=str(key_file))

        bundle_file = tmp_path / "vault-update-1.0.0.tar"
        bundle_file.write_bytes(b"bundle content")
        nonexistent_sig = str(tmp_path / "missing.tar.sig")

        with pytest.raises(VaultError, match="Signature file not found"):
            verifier.verify(str(bundle_file), nonexistent_sig)

    def test_verify_raises_when_key_file_missing(self, tmp_path):
        """verify() raises VaultError when the GPG public key doesn't exist."""
        nonexistent_key = str(tmp_path / "no-key.pub")
        verifier = GPGVerifier(public_key_path=nonexistent_key)

        bundle_file = tmp_path / "vault-update-1.0.0.tar"
        bundle_file.write_bytes(b"bundle content")
        sig_file = tmp_path / "vault-update-1.0.0.tar.sig"
        sig_file.write_bytes(b"fake sig")

        # gpg binary exists but key file doesn't
        with patch("shutil.which", return_value="/usr/bin/gpg"):
            with pytest.raises(VaultError, match="GPG public key not found|gpg"):
                verifier.verify(str(bundle_file), str(sig_file))

    def test_verify_raises_when_gpg_binary_missing(self, tmp_path):
        """verify() raises VaultError when gpg binary is not installed."""
        key_file = tmp_path / "vault-key.pub"
        key_file.write_text("fake key")
        verifier = GPGVerifier(public_key_path=str(key_file))

        bundle_file = tmp_path / "vault-update-1.0.0.tar"
        bundle_file.write_bytes(b"bundle content")
        sig_file = tmp_path / "vault-update-1.0.0.tar.sig"
        sig_file.write_bytes(b"fake sig")

        with patch("shutil.which", return_value=None):
            with pytest.raises(VaultError, match="GPG binary not found"):
                verifier.verify(str(bundle_file), str(sig_file))
