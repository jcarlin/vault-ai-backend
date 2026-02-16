from fastapi import Request
from fastapi.responses import JSONResponse


class VaultError(Exception):
    """Base exception for Vault API errors."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 500,
        details: dict | None = None,
    ):
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict:
        result = {
            "error": {
                "code": self.code,
                "message": self.message,
                "status": self.status,
            }
        }
        if self.details:
            result["error"]["details"] = self.details
        return result


class AuthenticationError(VaultError):
    def __init__(self, message: str = "Invalid or missing API key.", details: dict | None = None):
        super().__init__(code="authentication_required", message=message, status=401, details=details)


class AuthorizationError(VaultError):
    def __init__(self, message: str = "Insufficient permissions.", details: dict | None = None):
        super().__init__(code="insufficient_permissions", message=message, status=403, details=details)


class NotFoundError(VaultError):
    def __init__(self, message: str = "Resource not found.", details: dict | None = None):
        super().__init__(code="not_found", message=message, status=404, details=details)


class BackendUnavailableError(VaultError):
    def __init__(self, message: str = "Inference backend is unavailable.", details: dict | None = None):
        super().__init__(
            code="backend_unavailable",
            message=message,
            status=503,
            details=details or {"suggestion": "The vLLM inference engine may be starting up. Try again shortly."},
        )


async def vault_error_handler(request: Request, exc: VaultError) -> JSONResponse:
    """Global exception handler for VaultError and subclasses."""
    return JSONResponse(status_code=exc.status, content=exc.to_dict())
