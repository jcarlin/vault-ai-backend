import json

import pytest


class TestCompletionsEndpoint:
    async def test_non_streaming(self, auth_client):
        """POST /v1/completions with stream=false returns JSON."""
        response = await auth_client.post(
            "/v1/completions",
            json={
                "model": "qwen2.5-32b-awq",
                "prompt": "Hello",
                "stream": False,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "text_completion"
        assert len(data["choices"]) == 1
        assert data["choices"][0]["text"] == "Hello from Vault AI!"
        assert data["choices"][0]["finish_reason"] == "stop"
        assert data["usage"]["total_tokens"] == 10

    async def test_streaming(self, auth_client):
        """POST /v1/completions with stream=true returns SSE chunks."""
        response = await auth_client.post(
            "/v1/completions",
            json={
                "model": "qwen2.5-32b-awq",
                "prompt": "Hello",
                "stream": True,
            },
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        lines = [ln for ln in response.text.split("\n") if ln.startswith("data: ")]
        assert len(lines) >= 2
        assert lines[-1] == "data: [DONE]"

        # First data chunk should be valid JSON with text_completion format
        first_chunk = json.loads(lines[0].removeprefix("data: "))
        assert first_chunk["object"] == "text_completion"
        assert first_chunk["model"] == "qwen2.5-32b-awq"

    async def test_unauthenticated(self, anon_client):
        """POST /v1/completions without auth returns 401."""
        response = await anon_client.post(
            "/v1/completions",
            json={"model": "qwen2.5-32b-awq", "prompt": "Hello"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_required"
