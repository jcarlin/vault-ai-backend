import time
from datetime import datetime, timezone

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from app.core.database import ApiKey, AuditLog, async_session
from app.core.exceptions import AuthenticationError
from app.core.security import hash_api_key

logger = structlog.get_logger()

# Paths that skip authentication
PUBLIC_PATHS = {"/vault/health", "/", "/docs", "/openapi.json", "/redoc", "/metrics"}

# Paths that skip authentication even with a Bearer token prefix check
# (login and ldap-enabled must be accessible without a valid token)
AUTH_PUBLIC_PATHS = {"/vault/auth/login", "/vault/auth/ldap-enabled"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Validates Bearer token on every request except public paths.

    Supports dual-auth: API keys (vault_sk_*) and JWT tokens (eyJ*).
    Differentiates by token prefix.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Setup wizard endpoints: unauthenticated when pending, 404 when complete
        if request.url.path.startswith("/vault/setup/"):
            if getattr(request.app.state, "setup_complete", False):
                return JSONResponse(
                    status_code=404,
                    content={"error": {"code": "not_found", "message": "Setup has already been completed.", "status": 404}},
                )
            return await call_next(request)

        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # Auth-related public paths (login, ldap-enabled check)
        if request.url.path in AUTH_PUBLIC_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            error = AuthenticationError("Missing or malformed Authorization header.")
            return JSONResponse(status_code=error.status, content=error.to_dict())

        token = auth_header.removeprefix("Bearer ").strip()

        # Route 1: API key (vault_sk_*)
        if token.startswith("vault_sk_"):
            return await self._authenticate_api_key(request, call_next, token)

        # Route 2: JWT token (try decode)
        return await self._authenticate_jwt(request, call_next, token)

    async def _authenticate_api_key(
        self, request: Request, call_next: RequestResponseEndpoint, token: str
    ) -> Response:
        """Validate an API key token."""
        token_hash = hash_api_key(token)

        from sqlalchemy import select, update

        async with async_session() as session:
            result = await session.execute(
                select(ApiKey).where(ApiKey.key_hash == token_hash, ApiKey.is_active == True)  # noqa: E712
            )
            key_row = result.scalar_one_or_none()

            if key_row is None:
                error = AuthenticationError("Invalid or revoked API key.")
                return JSONResponse(status_code=error.status, content=error.to_dict())

            # Store key info on request state for downstream use
            request.state.auth_type = "key"
            request.state.api_key_id = key_row.id
            request.state.api_key_prefix = key_row.key_prefix
            request.state.api_key_scope = key_row.scope

            # Update last_used_at
            await session.execute(
                update(ApiKey).where(ApiKey.id == key_row.id).values(last_used_at=datetime.now(timezone.utc))
            )
            await session.commit()

        return await call_next(request)

    async def _authenticate_jwt(
        self, request: Request, call_next: RequestResponseEndpoint, token: str
    ) -> Response:
        """Validate a JWT token."""
        from app.services.jwt_service import JWTService

        jwt_service = JWTService()
        claims = jwt_service.decode_token(token)

        if claims is None:
            error = AuthenticationError("Invalid or expired token.")
            return JSONResponse(status_code=error.status, content=error.to_dict())

        # Store JWT info on request state
        request.state.auth_type = "jwt"
        request.state.user_id = claims.get("sub")
        request.state.user_role = claims.get("role", "user")
        request.state.user_name = claims.get("name", "")
        request.state.auth_source = claims.get("auth_source", "local")

        return await call_next(request)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request as structured JSON and writes to AuditLog table."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        latency_ms = round((time.perf_counter() - start) * 1000, 1)

        key_prefix = getattr(request.state, "api_key_prefix", None)

        logger.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            latency_ms=latency_ms,
            user_key_prefix=key_prefix,
        )

        # Write to AuditLog (best-effort — don't let logging failures break requests)
        try:
            async with async_session() as session:
                audit_entry = AuditLog(
                    action="http_request",
                    method=request.method,
                    path=request.url.path,
                    user_key_prefix=key_prefix,
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                    timestamp=datetime.now(timezone.utc),
                )
                session.add(audit_entry)
                await session.commit()
        except Exception:
            logger.debug("audit_log_write_failed", path=request.url.path)

        return response
