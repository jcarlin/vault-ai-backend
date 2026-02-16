import json

import pytest


class TestChatCompletionsEndpoint:
    async def test_streaming_response(self, auth_client):
        """POST /v1/chat/completions with stream=true returns SSE chunks."""
        response = await auth_client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen2.5-32b-awq",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            },
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        # Parse SSE lines — should have data chunks ending with [DONE]
        lines = [ln for ln in response.text.split("\n") if ln.startswith("data: ")]
        assert len(lines) >= 2  # at least one content chunk + [DONE]
        assert lines[-1] == "data: [DONE]"

        # First data chunk should be valid JSON with chat.completion.chunk format
        first_chunk = json.loads(lines[0].removeprefix("data: "))
        assert first_chunk["object"] == "chat.completion.chunk"
        assert first_chunk["model"] == "qwen2.5-32b-awq"

    async def test_non_streaming_response(self, auth_client):
        """POST /v1/chat/completions with stream=false returns JSON."""
        response = await auth_client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen2.5-32b-awq",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "chat.completion"
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert len(data["choices"][0]["message"]["content"]) > 0
        assert data["usage"]["total_tokens"] > 0

    async def test_401_without_auth(self, anon_client):
        """POST /v1/chat/completions without auth returns 401."""
        response = await anon_client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen2.5-32b-awq",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_required"

    async def test_422_missing_messages(self, auth_client):
        """POST /v1/chat/completions without messages returns 422."""
        response = await auth_client.post(
            "/v1/chat/completions",
            json={"model": "qwen2.5-32b-awq"},
        )
        assert response.status_code == 422
