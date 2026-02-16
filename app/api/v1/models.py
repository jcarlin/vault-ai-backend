import json
from pathlib import Path

import structlog
from fastapi import APIRouter

from app.config import settings
from app.schemas.models import ModelInfo, ModelListResponse

router = APIRouter()
logger = structlog.get_logger()


@router.get("/v1/models")
async def list_models() -> ModelListResponse:
    """List available models from the local manifest file."""
    manifest_path = Path(settings.vault_models_manifest)

    if not manifest_path.exists():
        logger.warning("models_manifest_not_found", path=str(manifest_path))
        return ModelListResponse(data=[])

    with open(manifest_path) as f:
        data = json.load(f)

    models = [ModelInfo(**m) for m in data.get("models", [])]
    return ModelListResponse(data=models)
