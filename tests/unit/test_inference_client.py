import json

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.core.exceptions import BackendUnavailableError
from app.schemas.chat import ChatCompletionRequest, ChatMessage
from app.services.inference.vllm_client import VLLMBackend, _classify_model_type
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
    assert len(models) == 2

    # Chat model
    chat_model = next(m for m in models if m.id == "qwen2.5-32b-awq:latest")
    assert chat_model.name == "qwen2.5-32b-awq"
    assert chat_model.type == "chat"
    assert chat_model.status == "running"
    assert chat_model.parameters == "32.5B"
    assert chat_model.quantization == "Q4_0"
    assert chat_model.family == "qwen2"
    assert chat_model.context_window == 32768

    # Embedding model
    embed_model = next(m for m in models if m.id == "nomic-embed-text:latest")
    assert embed_model.name == "nomic-embed-text"
    assert embed_model.type == "embedding"
    assert embed_model.status == "available"
    assert embed_model.parameters == "137M"
    assert embed_model.family == "nomic-bert"


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


# ── Model type classification tests ──────────────────────────────────────────


class TestClassifyModelType:
    def test_chat_by_default(self):
        assert _classify_model_type(None, "qwen2.5-32b-awq") == "chat"

    def test_chat_family(self):
        assert _classify_model_type("qwen2", "qwen2.5-32b-awq:latest") == "chat"

    def test_embedding_family_bert(self):
        assert _classify_model_type("bert", "some-bert-model") == "embedding"

    def test_embedding_family_nomic_bert(self):
        assert _classify_model_type("nomic-bert", "nomic-embed-text:latest") == "embedding"

    def test_embedding_family_mxbai(self):
        assert _classify_model_type("mxbai", "mxbai-embed-large") == "embedding"

    def test_embedding_name_keyword_embed(self):
        assert _classify_model_type(None, "nomic-embed-text:latest") == "embedding"

    def test_embedding_name_keyword_bge(self):
        assert _classify_model_type(None, "bge-large-en-v1.5") == "embedding"

    def test_embedding_name_keyword_e5(self):
        assert _classify_model_type(None, "e5-large-v2") == "embedding"

    def test_embedding_name_keyword_gte(self):
        assert _classify_model_type(None, "gte-base") == "embedding"

    def test_unknown_family_defaults_chat(self):
        assert _classify_model_type("llama", "llama-3.3-8b:latest") == "chat"


# ── Running status tests ─────────────────────────────────────────────────────


async def test_running_status_from_api_ps(vllm_backend: VLLMBackend):
    """Models listed in /api/ps are marked as running."""
    models = await vllm_backend.list_models()
    running = [m for m in models if m.status == "running"]
    available = [m for m in models if m.status == "available"]
    assert len(running) == 1
    assert running[0].id == "qwen2.5-32b-awq:latest"
    assert len(available) == 1
    assert available[0].id == "nomic-embed-text:latest"
