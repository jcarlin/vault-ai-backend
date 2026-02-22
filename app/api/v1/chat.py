import json

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse

from app.dependencies import get_inference_backend
from app.schemas.chat import ChatCompletionRequest
from app.schemas.completions import CompletionRequest
from app.schemas.embeddings import EmbeddingRequest
from app.services.inference.base import InferenceBackend

router = APIRouter()


@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    backend: InferenceBackend = Depends(get_inference_backend),
):
    """Proxy chat completions to the inference backend."""
    if request.stream:
        async def stream_generator():
            async for line in backend.chat_completion(request):
                yield line

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
        )

    # Non-streaming: collect the single JSON response from the backend
    result = ""
    async for chunk in backend.chat_completion(request):
        result += chunk

    return JSONResponse(content=json.loads(result))


@router.post("/v1/completions")
async def text_completions(
    request: CompletionRequest,
    backend: InferenceBackend = Depends(get_inference_backend),
):
    """Proxy text completions to the inference backend."""
    if request.stream:
        async def stream_generator():
            async for line in backend.text_completion(request):
                yield line

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
        )

    result = ""
    async for chunk in backend.text_completion(request):
        result += chunk

    return JSONResponse(content=json.loads(result))


@router.post("/v1/embeddings")
async def create_embeddings(
    request: EmbeddingRequest,
    backend: InferenceBackend = Depends(get_inference_backend),
):
    """Generate embeddings via the inference backend."""
    result = await backend.generate_embeddings(request)
    return JSONResponse(content=result)
