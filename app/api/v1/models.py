import json
from pathlib import Path

import structlog
from fastapi import APIRouter, Request

from app.config import settings
from app.schemas.models import ModelInfo, ModelListResponse

router = APIRouter()
logger = structlog.get_logger()


def _load_manifest() -> list[dict] | None:
    """Load the model manifest file for enrichment (descriptions, friendly names)."""
    manifest_path = Path(settings.vault_models_manifest)
    if not manifest_path.exists():
        return None
    try:
        with open(manifest_path) as f:
            data = json.load(f)
        return data.get("models", [])
    except Exception:
        logger.debug("manifest_load_failed", path=str(manifest_path))
        return None


def _merge_with_manifest(models: list[ModelInfo], manifest: list[dict]) -> list[ModelInfo]:
    """Enrich backend-discovered models with manifest metadata (descriptions, friendly names)."""
    manifest_by_id = {m["id"]: m for m in manifest}
    enriched = []
    for model in models:
        # Try exact match, then try without :latest suffix
        extra = manifest_by_id.get(model.id) or manifest_by_id.get(
            model.id.rsplit(":", 1)[0] if ":" in model.id else model.id
        ) or {}
        enriched.append(ModelInfo(
            id=model.id,
            name=extra.get("name") or model.name,
            type=extra.get("type") or model.type,
            status=model.status,  # Always from live backend
            parameters=model.parameters or extra.get("parameters"),
            quantization=model.quantization or extra.get("quantization"),
            context_window=model.context_window or extra.get("context_window"),
            vram_required_gb=model.vram_required_gb or extra.get("vram_required_gb"),
            description=extra.get("description") or model.description,
            family=model.family,
            size_bytes=model.size_bytes,
        ))
    return enriched


def _model_sort_key(model: ModelInfo) -> tuple[int, int, str]:
    """Sort key: running chat > available chat > running embedding > available embedding."""
    type_rank = 0 if model.type == "chat" else 1
    status_rank = 0 if model.status == "running" else 1
    return (type_rank, status_rank, model.name.lower())


@router.get("/v1/models")
async def list_models(request: Request) -> ModelListResponse:
    """List available models from the inference backend, enriched with manifest metadata."""
    backend = request.app.state.inference_backend
    manifest = _load_manifest()

    # Try live backend first
    try:
        models = await backend.list_models()
    except Exception:
        logger.warning("backend_model_list_failed", msg="Falling back to manifest")
        models = []

    # Enrich with manifest (descriptions, friendly names)
    if models and manifest:
        models = _merge_with_manifest(models, manifest)

    # Fall back to manifest-only if backend returned nothing
    if not models and manifest:
        models = [ModelInfo(**m) for m in manifest]

    models.sort(key=_model_sort_key)
    return ModelListResponse(data=models)


@router.get("/v1/models/{model_id}")
async def get_model(model_id: str, request: Request) -> ModelInfo:
    """Get detailed info for a specific model."""
    from app.core.exceptions import NotFoundError

    backend = request.app.state.inference_backend
    manifest = _load_manifest()

    # Try live backend first
    model = None
    try:
        model = await backend.get_model_details(model_id)
        has_metadata = model.parameters or model.family or model.context_window
        if has_metadata:
            if manifest:
                manifest_entry = next(
                    (m for m in manifest if m["id"] == model_id or m["id"] == model_id.rsplit(":", 1)[0]),
                    None,
                )
                if manifest_entry:
                    model = _merge_with_manifest([model], [manifest_entry])[0]
            return model
    except Exception:
        pass

    # Fallback to manifest only
    if manifest:
        entry = next((m for m in manifest if m["id"] == model_id), None)
        if entry:
            return ModelInfo(**entry)

    # If backend returned a bare model, check if it exists in the models list
    if model is not None:
        try:
            all_models = await backend.list_models()
            if any(m.id == model_id for m in all_models):
                return model
        except Exception:
            pass

    raise NotFoundError(f"Model '{model_id}' not found.")
