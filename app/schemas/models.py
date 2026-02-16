from pydantic import BaseModel


class ModelInfo(BaseModel):
    id: str
    name: str
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
