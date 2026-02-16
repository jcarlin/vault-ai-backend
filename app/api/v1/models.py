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
            parameters=model.parameters or extra.get("parameters"),
            quantization=model.quantization or extra.get("quantization"),
            context_window=model.context_window or extra.get("context_window"),
            vram_required_gb=model.vram_required_gb or extra.get("vram_required_gb"),
            description=extra.get("description") or model.description,
            family=model.family,
            size_bytes=model.size_bytes,
        ))
    return enriched


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

    return ModelListResponse(data=models)
