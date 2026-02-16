import asyncio
from collections.abc import AsyncIterator

import httpx
import structlog

from app.core.exceptions import BackendUnavailableError
from app.schemas.chat import ChatCompletionRequest
from app.schemas.models import ModelInfo
from app.services.inference.base import InferenceBackend

logger = structlog.get_logger()

# ── Model type classification ────────────────────────────────────────────────

EMBEDDING_FAMILIES = frozenset({"bert", "nomic-bert", "mxbai"})
EMBEDDING_NAME_KEYWORDS = ("embed", "bge", "e5-", "gte-")


def _classify_model_type(family: str | None, model_id: str) -> str:
    """Classify a model as 'chat' or 'embedding' based on family and name heuristics."""
    if family and family.lower() in EMBEDDING_FAMILIES:
        return "embedding"
    model_lower = model_id.lower()
    if any(kw in model_lower for kw in EMBEDDING_NAME_KEYWORDS):
        return "embedding"
    return "chat"


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
        """List available models with auto-discovered metadata.

        Tries Ollama's /api/tags for rich metadata (params, quant, family, size),
        then enriches with /api/show for context_window and /api/ps for running
        status. Falls back to the OpenAI-compatible /v1/models if backend isn't
        Ollama (all vLLM-listed models are assumed running).
        """
        # Try Ollama's /api/tags (rich metadata in one call)
        try:
            response = await self._client.get(f"{self.base_url}/api/tags", headers=self._headers)
            if response.status_code == 200:
                models = self._parse_ollama_tags(response.json())
                # Enrich with context_window + running status (parallel)
                _, running_names = await asyncio.gather(
                    self._enrich_context_windows(models),
                    self._fetch_running_model_names(),
                )
                for model in models:
                    if model.id in running_names:
                        model.status = "running"
                return models
        except (httpx.ConnectError, httpx.TimeoutException):
            pass

        # Fall back to OpenAI-compatible /v1/models
        # vLLM only lists loaded/running models, so mark all as running
        try:
            response = await self._client.get(f"{self.base_url}/v1/models", headers=self._headers)
            response.raise_for_status()
            data = response.json()
            return [ModelInfo(id=m["id"], name=m.get("id", ""), status="running") for m in data.get("data", [])]
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise BackendUnavailableError(f"Cannot reach inference backend: {e}")

    async def get_model_details(self, model_id: str) -> ModelInfo:
        """Get detailed info for a specific model via Ollama /api/show."""
        try:
            response = await self._client.post(
                f"{self.base_url}/api/show",
                json={"name": model_id},
                headers=self._headers,
            )
            if response.status_code == 200:
                return self._parse_ollama_show(model_id, response.json())
        except (httpx.ConnectError, httpx.TimeoutException):
            pass

        # Fallback: just the model ID
        return ModelInfo(id=model_id, name=model_id)

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

    # ── Ollama response parsers ──────────────────────────────────────────────

    def _parse_ollama_tags(self, data: dict) -> list[ModelInfo]:
        """Parse Ollama /api/tags response into ModelInfo list."""
        models = []
        for m in data.get("models", []):
            details = m.get("details", {})
            model_id = m.get("model", m.get("name", ""))
            name = model_id.rsplit(":", 1)[0] if ":" in model_id else model_id

            size_bytes = m.get("size")
            # Rough VRAM estimate: model file size * 1.2 overhead
            vram_gb = round(size_bytes / (1024**3) * 1.2, 1) if size_bytes else None

            models.append(ModelInfo(
                id=model_id,
                name=name,
                type=_classify_model_type(details.get("family"), model_id),
                parameters=details.get("parameter_size"),
                quantization=details.get("quantization_level"),
                family=details.get("family"),
                size_bytes=size_bytes,
                vram_required_gb=vram_gb,
            ))
        return models

    def _parse_ollama_show(self, model_id: str, data: dict) -> ModelInfo:
        """Parse Ollama /api/show response into ModelInfo."""
        details = data.get("details", {})
        model_info = data.get("model_info", {})

        name = model_id.rsplit(":", 1)[0] if ":" in model_id else model_id

        # Context length key varies by architecture (e.g. llama.context_length, qwen2.context_length)
        context_window = None
        for key, value in model_info.items():
            if key.endswith(".context_length"):
                context_window = value
                break

        return ModelInfo(
            id=model_id,
            name=name,
            parameters=details.get("parameter_size"),
            quantization=details.get("quantization_level"),
            family=details.get("family"),
            context_window=context_window,
        )

    async def _fetch_running_model_names(self) -> set[str]:
        """Fetch currently loaded models from Ollama /api/ps (best-effort)."""
        try:
            response = await self._client.get(f"{self.base_url}/api/ps", headers=self._headers)
            if response.status_code == 200:
                return {m.get("model", m.get("name", "")) for m in response.json().get("models", [])}
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        return set()

    async def _enrich_context_windows(self, models: list[ModelInfo]) -> None:
        """Enrich model list with context_window from /api/show (best-effort, parallel)."""
        async def _get_context(model: ModelInfo) -> None:
            try:
                resp = await self._client.post(
                    f"{self.base_url}/api/show",
                    json={"name": model.id},
                    headers=self._headers,
                )
                if resp.status_code == 200:
                    model_info = resp.json().get("model_info", {})
                    for key, value in model_info.items():
                        if key.endswith(".context_length"):
                            model.context_window = value
                            break
            except (httpx.ConnectError, httpx.TimeoutException):
                pass

        await asyncio.gather(*[_get_context(m) for m in models])
