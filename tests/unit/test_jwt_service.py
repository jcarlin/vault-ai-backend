"""Unit tests for JWT service."""

import time

import pytest

from app.services.jwt_service import JWTService


class TestJWTService:
    """JWT token creation and validation."""

    def setup_method(self):
        self.jwt = JWTService(
            secret_key="test-secret-key-for-unit-tests",
            algorithm="HS256",
            expiry_seconds=3600,
        )

    def test_create_token_returns_string(self):
        token = self.jwt.create_token(
            user_id="user-123", role="admin", name="Test User"
        )
        assert isinstance(token, str)
        assert len(token) > 0

    def test_decode_valid_token(self):
        token = self.jwt.create_token(
            user_id="user-123", role="admin", name="Test User", auth_source="ldap"
        )
        claims = self.jwt.decode_token(token)
        assert claims is not None
        assert claims["sub"] == "user-123"
        assert claims["role"] == "admin"
        assert claims["name"] == "Test User"
        assert claims["auth_source"] == "ldap"

    def test_decode_expired_token(self):
        jwt_short = JWTService(
            secret_key="test-secret-key-for-unit-tests",
            algorithm="HS256",
            expiry_seconds=-1,  # Already expired
        )
        token = jwt_short.create_token(
            user_id="user-123", role="user", name="Test"
        )
        claims = jwt_short.decode_token(token)
        assert claims is None

    def test_decode_invalid_signature(self):
        token = self.jwt.create_token(
            user_id="user-123", role="user", name="Test"
        )
        # Use different secret to verify
        other_jwt = JWTService(
            secret_key="different-secret-key",
            algorithm="HS256",
            expiry_seconds=3600,
        )
        claims = other_jwt.decode_token(token)
        assert claims is None

    def test_decode_malformed_token(self):
        claims = self.jwt.decode_token("not.a.valid.token")
        assert claims is None

    def test_decode_empty_token(self):
        claims = self.jwt.decode_token("")
        assert claims is None

    def test_token_contains_iat_and_exp(self):
        token = self.jwt.create_token(
            user_id="user-123", role="user", name="Test"
        )
        claims = self.jwt.decode_token(token)
        assert "iat" in claims
        assert "exp" in claims
        assert claims["exp"] - claims["iat"] == 3600

    def test_default_auth_source_is_local(self):
        token = self.jwt.create_token(
            user_id="user-123", role="user", name="Test"
        )
        claims = self.jwt.decode_token(token)
        assert claims["auth_source"] == "local"
