import pytest
from pydantic import ValidationError

from app.schemas.chat import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChunkChoice,
    DeltaContent,
    Usage,
)
from app.schemas.health import GpuInfo, HealthResponse
from app.schemas.models import ModelInfo, ModelListResponse


class TestChatMessage:
    def test_valid_roles(self):
        for role in ("system", "user", "assistant"):
            msg = ChatMessage(role=role, content="hello")
            assert msg.role == role

    def test_invalid_role_rejected(self):
        with pytest.raises(ValidationError):
            ChatMessage(role="tool", content="hello")


class TestChatCompletionRequest:
    def test_valid_request(self):
        req = ChatCompletionRequest(
            model="qwen2.5-32b-awq",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        assert req.model == "qwen2.5-32b-awq"
        assert len(req.messages) == 1

    def test_defaults(self):
        req = ChatCompletionRequest(
            model="qwen2.5-32b-awq",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        assert req.stream is False
        assert req.temperature == 0.7
        assert req.max_tokens is None
        assert req.top_p == 1.0
        assert req.stop is None

    def test_rejects_temperature_above_2(self):
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="test",
                messages=[ChatMessage(role="user", content="Hi")],
                temperature=2.5,
            )

    def test_rejects_temperature_below_0(self):
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="test",
                messages=[ChatMessage(role="user", content="Hi")],
                temperature=-0.1,
            )


class TestChatCompletionResponse:
    def test_full_response(self):
        resp = ChatCompletionResponse(
            id="chatcmpl-abc123",
            model="qwen2.5-32b-awq",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Hello!"),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        assert resp.id == "chatcmpl-abc123"
        assert resp.object == "chat.completion"
        assert resp.choices[0].finish_reason == "stop"
        assert resp.usage.total_tokens == 15
        assert isinstance(resp.created, int)


class TestChatCompletionChunk:
    def test_chunk_format(self):
        chunk = ChatCompletionChunk(
            id="chatcmpl-abc123",
            model="qwen2.5-32b-awq",
            choices=[
                ChunkChoice(
                    index=0,
                    delta=DeltaContent(content="Hello"),
                    finish_reason=None,
                )
            ],
        )
        assert chunk.object == "chat.completion.chunk"
        assert chunk.choices[0].delta.content == "Hello"
        assert chunk.choices[0].finish_reason is None


class TestModelInfo:
    def test_minimal_fields(self):
        model = ModelInfo(id="test-model", name="Test Model")
        assert model.id == "test-model"
        assert model.parameters is None
        assert model.quantization is None

    def test_full_fields(self):
        model = ModelInfo(
            id="qwen2.5-32b-awq",
            name="Qwen 2.5 32B",
            parameters="32B",
            quantization="AWQ 4-bit",
            context_window=32768,
            vram_required_gb=20.0,
            description="A great model.",
        )
        assert model.context_window == 32768
        assert model.vram_required_gb == 20.0


class TestModelListResponse:
    def test_multiple_models(self):
        resp = ModelListResponse(
            data=[
                ModelInfo(id="model-a", name="Model A"),
                ModelInfo(id="model-b", name="Model B"),
            ]
        )
        assert resp.object == "list"
        assert len(resp.data) == 2


class TestHealthResponse:
    def test_defaults(self):
        health = HealthResponse(status="ok", vllm_status="connected")
        assert health.gpu_count == 0
        assert health.gpus == []
        assert health.uptime_seconds == 0.0
        assert health.version == "0.1.0"

    def test_with_gpu_info(self):
        gpu = GpuInfo(
            index=0,
            name="RTX 5090",
            memory_total_mb=32768,
            memory_used_mb=8192,
            utilization_pct=45.5,
        )
        health = HealthResponse(
            status="ok",
            vllm_status="connected",
            gpu_count=1,
            gpus=[gpu],
            uptime_seconds=3600.0,
        )
        assert health.gpu_count == 1
        assert health.gpus[0].name == "RTX 5090"
        assert health.gpus[0].utilization_pct == 45.5
