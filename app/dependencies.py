from fastapi import Request

from app.core.exceptions import AuthorizationError
from app.services.inference.base import InferenceBackend


def get_inference_backend(request: Request) -> InferenceBackend:
    """Return the inference backend stored on app state during lifespan."""
    return request.app.state.inference_backend


def require_admin(request: Request) -> None:
    """Dependency that enforces admin scope on the current API key."""
    scope = getattr(request.state, "api_key_scope", None)
    if scope != "admin":
        raise AuthorizationError("This endpoint requires an admin-scoped API key.")
