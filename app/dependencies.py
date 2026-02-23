from fastapi import Request

from app.core.exceptions import AuthorizationError, VaultError
from app.services.inference.base import InferenceBackend


def get_inference_backend(request: Request) -> InferenceBackend:
    """Return the inference backend stored on app state during lifespan."""
    return request.app.state.inference_backend


def require_admin(request: Request) -> None:
    """Dependency that enforces admin access via either API key scope or JWT role."""
    auth_type = getattr(request.state, "auth_type", None)

    if auth_type == "jwt":
        role = getattr(request.state, "user_role", None)
        if role == "admin":
            return
        raise AuthorizationError("This endpoint requires admin privileges.")

    if auth_type == "key":
        scope = getattr(request.state, "api_key_scope", None)
        if scope == "admin":
            return
        raise AuthorizationError("This endpoint requires an admin-scoped API key.")

    # Fallback: legacy path (auth_type not set = old API key middleware)
    scope = getattr(request.state, "api_key_scope", None)
    if scope == "admin":
        return
    raise AuthorizationError("This endpoint requires admin privileges.")


async def require_devmode() -> None:
    """Dependency that enforces developer mode is enabled."""
    from app.services.devmode import is_devmode_enabled

    if not await is_devmode_enabled():
        raise VaultError(
            code="devmode_disabled",
            message="Developer mode is not enabled.",
            status=403,
        )
