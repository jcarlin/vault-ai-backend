import secrets

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from app.config import settings

logger = structlog.get_logger()

# Paths exempt from access key check (health probes, root info)
ACCESS_GATE_EXEMPT = {"/vault/health", "/"}


class AccessGateMiddleware(BaseHTTPMiddleware):
    """Outermost middleware — validates shared access key for cloud deployments.

    When VAULT_ACCESS_KEY is not set (Cube mode), this middleware is a no-op.
    When set, every request must include a matching X-Vault-Access-Key header,
    except for exempt paths (health probes).
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        expected = settings.vault_access_key
        if not expected:
            # Cube mode — no access gate (None or empty string)
            return await call_next(request)

        if request.url.path in ACCESS_GATE_EXEMPT:
            return await call_next(request)

        provided = request.headers.get("x-vault-access-key", "")
        if not provided or not secrets.compare_digest(provided, expected):
            logger.warning("access_gate_denied", path=request.url.path)
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "access_denied",
                        "message": "Invalid or missing access key.",
                        "status": 403,
                    }
                },
            )

        return await call_next(request)
