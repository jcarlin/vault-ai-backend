from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.schemas.chat import ChatCompletionRequest
from app.schemas.completions import CompletionRequest
from app.schemas.embeddings import EmbeddingRequest
from app.schemas.models import ModelInfo


class InferenceBackend(ABC):
    @abstractmethod
    async def chat_completion(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        """Stream chat completion chunks as SSE lines."""
        ...

    @abstractmethod
    async def text_completion(self, request: CompletionRequest) -> AsyncIterator[str]:
        """Stream text completion chunks as SSE lines."""
        ...

    @abstractmethod
    async def generate_embeddings(self, request: EmbeddingRequest) -> dict:
        """Generate embeddings for input text."""
        ...

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """List available models with auto-discovered metadata."""
        ...

    @abstractmethod
    async def get_model_details(self, model_id: str) -> ModelInfo:
        """Get detailed info for a specific model."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if backend is responsive."""
        ...
