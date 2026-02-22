import datetime

import jwt
import structlog

from app.config import settings

logger = structlog.get_logger()


class JWTService:
    """Create and validate JWT tokens for LDAP/local user sessions."""

    def __init__(
        self,
        secret_key: str | None = None,
        algorithm: str | None = None,
        expiry_seconds: int | None = None,
    ):
        self._secret_key = secret_key or settings.vault_secret_key
        self._algorithm = algorithm or settings.vault_jwt_algorithm
        self._expiry_seconds = expiry_seconds or settings.vault_jwt_expiry_seconds

    def create_token(
        self,
        user_id: str,
        role: str,
        name: str,
        auth_source: str = "local",
    ) -> str:
        """Create a signed JWT with user claims."""
        now = datetime.datetime.now(datetime.timezone.utc)
        payload = {
            "sub": user_id,
            "role": role,
            "name": name,
            "auth_source": auth_source,
            "iat": now,
            "exp": now + datetime.timedelta(seconds=self._expiry_seconds),
        }
        return jwt.encode(payload, self._secret_key, algorithm=self._algorithm)

    def decode_token(self, token: str) -> dict | None:
        """Decode and validate a JWT. Returns claims dict or None if invalid/expired."""
        try:
            payload = jwt.decode(
                token, self._secret_key, algorithms=[self._algorithm]
            )
            return payload
        except jwt.ExpiredSignatureError:
            logger.debug("jwt_expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.debug("jwt_invalid", error=str(e))
            return None
