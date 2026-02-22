"""Standalone mock vLLM server for local development and testing.

Run standalone: uvicorn tests.mocks.fake_vllm:app --port 8001
"""

import json
import time
import uuid

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Fake vLLM")

STREAM_TOKENS = ["Hello", " from", " Vault", " AI", "!"]


class _ChatMessage(BaseModel):
    role: str
    content: str


class _ChatRequest(BaseModel):
    model: str = "qwen2.5-32b-awq"
    messages: list[_ChatMessage] = []
    stream: bool = False
    temperature: float = 0.7
    max_tokens: int | None = None
    top_p: float = 1.0
    stop: str | list[str] | None = None


@app.post("/v1/chat/completions")
async def chat_completions(request: _ChatRequest):
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    if request.stream:
        async def generate():
            for i, token in enumerate(STREAM_TOKENS):
                chunk = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": request.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": token} if i > 0 else {"role": "assistant", "content": token},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(chunk)}\n\n"

            # Final chunk with finish_reason
            final_chunk = {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
            }
            yield f"data: {json.dumps(final_chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    # Non-streaming response
    full_text = "".join(STREAM_TOKENS)
    return {
        "id": chat_id,
        "object": "chat.completion",
        "created": created,
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": full_text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": len(STREAM_TOKENS),
            "total_tokens": 10 + len(STREAM_TOKENS),
        },
    }


class _CompletionRequest(BaseModel):
    model: str = "qwen2.5-32b-awq"
    prompt: str | list[str] = "Hello"
    stream: bool = False
    max_tokens: int | None = None
    temperature: float = 0.7
    top_p: float = 1.0
    stop: str | list[str] | None = None
    echo: bool = False
    suffix: str | None = None


@app.post("/v1/completions")
async def text_completions(request: _CompletionRequest):
    comp_id = f"cmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    full_text = "".join(STREAM_TOKENS)

    if request.stream:
        async def generate():
            for token in STREAM_TOKENS:
                chunk = {
                    "id": comp_id,
                    "object": "text_completion",
                    "created": created,
                    "model": request.model,
                    "choices": [{"index": 0, "text": token, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
            final = {
                "id": comp_id,
                "object": "text_completion",
                "created": created,
                "model": request.model,
                "choices": [{"index": 0, "text": "", "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(final)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    return {
        "id": comp_id,
        "object": "text_completion",
        "created": created,
        "model": request.model,
        "choices": [{"index": 0, "text": full_text, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
    }


class _EmbeddingRequest(BaseModel):
    model: str = "nomic-embed-text:latest"
    input: str | list[str] = "test"
    encoding_format: str = "float"


@app.post("/v1/embeddings")
async def create_embeddings(request: _EmbeddingRequest):
    inputs = [request.input] if isinstance(request.input, str) else request.input
    data = [
        {"object": "embedding", "embedding": [0.1] * 384, "index": i}
        for i in range(len(inputs))
    ]
    return {
        "object": "list",
        "data": data,
        "model": request.model,
        "usage": {"prompt_tokens": len(inputs) * 5, "total_tokens": len(inputs) * 5},
    }


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "qwen2.5-32b-awq", "object": "model", "owned_by": "vault"},
        ],
    }


# ── Ollama-compatible endpoints (for dev parity) ────────────────────────────


@app.get("/api/tags")
async def ollama_tags():
    """Ollama /api/tags — rich model metadata."""
    return {
        "models": [
            {
                "name": "qwen2.5-32b-awq:latest",
                "model": "qwen2.5-32b-awq:latest",
                "size": 21474836480,
                "digest": "abc123def456",
                "details": {
                    "family": "qwen2",
                    "parameter_size": "32.5B",
                    "quantization_level": "Q4_0",
                    "format": "gguf",
                },
            },
            {
                "name": "nomic-embed-text:latest",
                "model": "nomic-embed-text:latest",
                "size": 274302450,
                "digest": "def789abc012",
                "details": {
                    "family": "nomic-bert",
                    "parameter_size": "137M",
                    "quantization_level": "F16",
                    "format": "gguf",
                },
            },
        ]
    }


class _ShowRequest(BaseModel):
    name: str


_SHOW_DATA = {
    "qwen2.5-32b-awq:latest": {
        "details": {
            "family": "qwen2",
            "parameter_size": "32.5B",
            "quantization_level": "Q4_0",
            "format": "gguf",
        },
        "model_info": {
            "general.architecture": "qwen2",
            "qwen2.context_length": 32768,
        },
    },
    "nomic-embed-text:latest": {
        "details": {
            "family": "nomic-bert",
            "parameter_size": "137M",
            "quantization_level": "F16",
            "format": "gguf",
        },
        "model_info": {
            "general.architecture": "nomic-bert",
            "nomic-bert.context_length": 8192,
        },
    },
}


@app.post("/api/show")
async def ollama_show(request: _ShowRequest):
    """Ollama /api/show — detailed model info including context length."""
    data = _SHOW_DATA.get(request.name)
    if data is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": f"model '{request.name}' not found"})
    return data


@app.get("/api/ps")
async def ollama_ps():
    """Ollama /api/ps — currently loaded/running models."""
    return {
        "models": [
            {
                "name": "qwen2.5-32b-awq:latest",
                "model": "qwen2.5-32b-awq:latest",
                "size": 21474836480,
            }
        ]
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
