from collections.abc import AsyncIterator

import httpx

from app.core.exceptions import BackendUnavailableError
from app.schemas.chat import ChatCompletionRequest
from app.schemas.models import ModelInfo
from app.services.inference.base import InferenceBackend


class VLLMBackend(InferenceBackend):
    def __init__(self, base_url: str, http_client: httpx.AsyncClient | None = None, api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=5.0, pool=5.0)
        )

    async def chat_completion(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        """Proxy chat completion to vLLM, yielding SSE lines."""
        url = f"{self.base_url}/v1/chat/completions"
        payload = request.model_dump(exclude_none=True)

        try:
            if request.stream:
                async with self._client.stream("POST", url, json=payload, headers=self._headers) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.strip():
                            yield line + "\n"
            else:
                response = await self._client.post(url, json=payload, headers=self._headers)
                response.raise_for_status()
                yield response.text
        except httpx.ConnectError as e:
            raise BackendUnavailableError(f"Cannot connect to vLLM at {self.base_url}: {e}")
        except httpx.HTTPStatusError as e:
            raise BackendUnavailableError(f"vLLM returned error: {e.response.status_code}")
        except httpx.TimeoutException:
            raise BackendUnavailableError("vLLM request timed out.")

    async def list_models(self) -> list[ModelInfo]:
        """List available models from vLLM."""
        try:
            response = await self._client.get(f"{self.base_url}/v1/models", headers=self._headers)
            response.raise_for_status()
            data = response.json()
            return [ModelInfo(id=m["id"], name=m.get("id", "")) for m in data.get("data", [])]
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise BackendUnavailableError(f"Cannot reach vLLM: {e}")

    async def health_check(self) -> bool:
        """Check if inference backend is responsive."""
        try:
            # Try vLLM-style health endpoint first
            response = await self._client.get(f"{self.base_url}/health", headers=self._headers)
            if response.status_code == 200:
                return True
        except (httpx.ConnectError, httpx.TimeoutException):
            return False
        try:
            # Fallback: Ollama (and others) return 200 at root
            response = await self._client.get(f"{self.base_url}/", headers=self._headers)
            return response.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()
