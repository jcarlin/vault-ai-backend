from fastapi import APIRouter, Depends, Request

from app.dependencies import require_admin
from app.schemas.model_management import (
    ActiveModelsResponse,
    ModelImportRequest,
    ModelImportResponse,
    ModelLoadRequest,
    ModelLoadResponse,
    VaultModelDetail,
    VaultModelInfo,
)
from app.services.model_manager import ModelManager

router = APIRouter()
_manager = ModelManager()


@router.get("/vault/models")
async def list_vault_models(request: Request) -> list[VaultModelInfo]:
    backend = request.app.state.inference_backend
    models = await _manager.list_models(backend=backend)
    return [VaultModelInfo(**m) for m in models]


@router.get("/vault/models/active")
async def active_models(request: Request) -> ActiveModelsResponse:
    backend = request.app.state.inference_backend
    result = await _manager.get_active_models(backend=backend)
    return ActiveModelsResponse(
        models=[VaultModelDetail(**m) for m in result["models"]],
        gpu_allocation=result["gpu_allocation"],
    )


@router.get("/vault/models/{model_id}")
async def get_vault_model(model_id: str, request: Request) -> VaultModelDetail:
    backend = request.app.state.inference_backend
    model = await _manager.get_model(model_id, backend=backend)
    return VaultModelDetail(**model)


@router.post("/vault/models/{model_id}/load", status_code=202, dependencies=[Depends(require_admin)])
async def load_model(model_id: str, body: ModelLoadRequest | None = None) -> ModelLoadResponse:
    gpu_index = body.gpu_index if body else 0
    result = await _manager.load_model(model_id, gpu_index=gpu_index)
    return ModelLoadResponse(**result)


@router.post("/vault/models/{model_id}/unload", status_code=202, dependencies=[Depends(require_admin)])
async def unload_model(model_id: str) -> ModelLoadResponse:
    result = await _manager.unload_model(model_id)
    return ModelLoadResponse(**result)


@router.post("/vault/models/import", status_code=202, dependencies=[Depends(require_admin)])
async def import_model(body: ModelImportRequest) -> ModelImportResponse:
    result = await _manager.import_model(body.source_path, model_id=body.model_id)
    return ModelImportResponse(**result)


@router.delete("/vault/models/{model_id}", dependencies=[Depends(require_admin)])
async def delete_model(model_id: str, request: Request) -> dict:
    backend = request.app.state.inference_backend
    result = await _manager.delete_model(model_id, backend=backend)
    return result
