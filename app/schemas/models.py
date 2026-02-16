from typing import Literal

from pydantic import BaseModel


class ModelInfo(BaseModel):
    id: str
    name: str
    type: Literal["chat", "embedding"] = "chat"
    status: Literal["running", "available"] = "available"
    parameters: str | None = None
    quantization: str | None = None
    context_window: int | None = None
    vram_required_gb: float | None = None
    description: str | None = None
    family: str | None = None
    size_bytes: int | None = None


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelInfo]
