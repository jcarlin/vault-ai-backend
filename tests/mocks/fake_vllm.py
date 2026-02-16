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


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "qwen2.5-32b-awq", "object": "model", "owned_by": "vault"},
        ],
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
