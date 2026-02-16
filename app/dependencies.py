from fastapi import Request

from app.services.inference.base import InferenceBackend


def get_inference_backend(request: Request) -> InferenceBackend:
    """Return the inference backend stored on app state during lifespan."""
    return request.app.state.inference_backend
