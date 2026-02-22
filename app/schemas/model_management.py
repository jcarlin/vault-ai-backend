from pydantic import BaseModel


class VaultModelInfo(BaseModel):
    id: str
    name: str
    status: str = "available"  # "loaded", "available"
    parameters: str | None = None
    quantization: str | None = None
    context_window: int | None = None
    vram_required_gb: float | None = None
    description: str | None = None
    size_bytes: int | None = None


class VaultModelDetail(VaultModelInfo):
    path: str | None = None
    format: str | None = None
    family: str | None = None
    gpu_index: int | None = None


class ModelLoadRequest(BaseModel):
    gpu_index: int = 0


class ModelLoadResponse(BaseModel):
    status: str = "loading"
    message: str
    model_id: str


class ModelImportRequest(BaseModel):
    source_path: str
    model_id: str | None = None


class ModelImportResponse(BaseModel):
    status: str = "importing"
    message: str
    model_id: str


class ActiveModelsResponse(BaseModel):
    models: list[VaultModelDetail]
    gpu_allocation: list[dict]
