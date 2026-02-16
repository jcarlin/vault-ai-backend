import json

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.core.exceptions import BackendUnavailableError
from app.schemas.chat import ChatCompletionRequest, ChatMessage
from app.services.inference.vllm_client import VLLMBackend
from tests.mocks.fake_vllm import app as fake_vllm_app


@pytest_asyncio.fixture
async def vllm_backend():
    transport = ASGITransport(app=fake_vllm_app)
    client = httpx.AsyncClient(transport=transport, base_url="http://fakevllm")
    backend = VLLMBackend(base_url="http://fakevllm", http_client=client)
    yield backend
    await client.aclose()


def _make_request(stream: bool = False) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="qwen2.5-32b-awq",
        messages=[ChatMessage(role="user", content="Hello")],
        stream=stream,
    )


async def test_streaming_chat(vllm_backend: VLLMBackend):
    """Streaming chat returns SSE-formatted lines with data: prefix."""
    request = _make_request(stream=True)
    chunks = []
    async for line in vllm_backend.chat_completion(request):
        chunks.append(line.strip())

    # Each non-empty line should start with "data: "
    data_lines = [c for c in chunks if c]
    assert len(data_lines) > 0

    # All content lines (not [DONE]) should be valid JSON after stripping "data: "
    for line in data_lines:
        assert line.startswith("data: ")
        payload = line.removeprefix("data: ")
        if payload == "[DONE]":
            continue
        parsed = json.loads(payload)
        assert parsed["object"] == "chat.completion.chunk"
        assert parsed["model"] == "qwen2.5-32b-awq"


async def test_non_streaming_chat(vllm_backend: VLLMBackend):
    """Non-streaming chat returns a full JSON response."""
    request = _make_request(stream=False)
    chunks = []
    async for text in vllm_backend.chat_completion(request):
        chunks.append(text)

    assert len(chunks) == 1
    data = json.loads(chunks[0])
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Hello from Vault AI!"
    assert data["usage"]["prompt_tokens"] == 10


async def test_list_models(vllm_backend: VLLMBackend):
    """List models returns available models with rich metadata from Ollama-style backend."""
    models = await vllm_backend.list_models()
    assert len(models) == 1
    # Ollama /api/tags returns model IDs with :latest suffix
    assert models[0].id == "qwen2.5-32b-awq:latest"
    assert models[0].name == "qwen2.5-32b-awq"
    assert models[0].parameters == "32.5B"
    assert models[0].quantization == "Q4_0"
    assert models[0].family == "qwen2"
    assert models[0].context_window == 32768


async def test_health_check_ok(vllm_backend: VLLMBackend):
    """Health check returns True when vLLM is available."""
    result = await vllm_backend.health_check()
    assert result is True


async def test_health_check_connection_error():
    """Health check returns False when vLLM is unreachable."""
    backend = VLLMBackend(base_url="http://localhost:19999")
    result = await backend.health_check()
    assert result is False
    await backend.close()


async def test_chat_connection_error():
    """Chat completion raises BackendUnavailableError when vLLM is unreachable."""
    backend = VLLMBackend(base_url="http://localhost:19999")
    request = _make_request(stream=False)

    with pytest.raises(BackendUnavailableError):
        async for _ in backend.chat_completion(request):
            pass

    await backend.close()
