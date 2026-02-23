"""Dataset management endpoints (Epic 22)."""

from fastapi import APIRouter, File, Form, Query, UploadFile

import app.core.database as db_module
from app.schemas.dataset import (
    DatasetCreate,
    DatasetList,
    DatasetPreview,
    DatasetResponse,
    DatasetStats,
    DatasetUpdate,
    DatasetUploadResponse,
    DatasetValidateResponse,
)
from app.services.dataset.dataset_service import DatasetService

router = APIRouter()


def _get_service() -> DatasetService:
    return DatasetService(session_factory=db_module.async_session)


@router.get("/vault/datasets", response_model=DatasetList)
async def list_datasets(
    type: str = Query(default=None, alias="type"),
    status: str = Query(default=None),
    source_id: str = Query(default=None),
    tags: str = Query(default=None, description="Comma-separated tags"),
    search: str = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List datasets with optional filters."""
    service = _get_service()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    return await service.list_datasets(
        dataset_type=type,
        status=status,
        source_id=source_id,
        tags=tag_list,
        search=search,
        offset=offset,
        limit=limit,
    )


@router.get("/vault/datasets/stats", response_model=DatasetStats)
async def get_dataset_stats():
    """Get aggregate dataset statistics."""
    service = _get_service()
    return await service.get_stats()


@router.get("/vault/datasets/by-type/{dataset_type}", response_model=DatasetList)
async def list_datasets_by_type(dataset_type: str):
    """List datasets filtered by type (training/eval/document/other)."""
    service = _get_service()
    return await service.list_by_type(dataset_type)


@router.get("/vault/datasets/{dataset_id}", response_model=DatasetResponse)
async def get_dataset(dataset_id: str):
    """Get dataset details."""
    service = _get_service()
    return await service.get_dataset(dataset_id)


@router.post("/vault/datasets", response_model=DatasetResponse, status_code=201)
async def create_dataset(data: DatasetCreate):
    """Register a dataset manually (by path reference)."""
    service = _get_service()
    return await service.create_dataset(data)


@router.post("/vault/datasets/upload", response_model=DatasetUploadResponse, status_code=201)
async def upload_dataset(
    file: UploadFile = File(...),
    name: str = Form(default=None),
    description: str = Form(default=None),
    dataset_type: str = Form(default="other"),
    tags: str = Form(default=""),
):
    """Upload a dataset file."""
    content = await file.read()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    service = _get_service()
    return await service.upload_dataset(
        file_content=content,
        filename=file.filename or "upload.dat",
        name=name,
        description=description,
        dataset_type=dataset_type,
        tags=tag_list if tag_list else None,
    )


@router.put("/vault/datasets/{dataset_id}", response_model=DatasetResponse)
async def update_dataset(dataset_id: str, data: DatasetUpdate):
    """Update dataset metadata."""
    service = _get_service()
    return await service.update_dataset(dataset_id, data)


@router.delete("/vault/datasets/{dataset_id}", status_code=204)
async def delete_dataset(
    dataset_id: str,
    delete_file: bool = Query(default=False),
):
    """Remove a dataset from the registry."""
    service = _get_service()
    await service.delete_dataset(dataset_id, delete_file=delete_file)


@router.post("/vault/datasets/{dataset_id}/validate", response_model=DatasetValidateResponse)
async def validate_dataset(dataset_id: str):
    """Trigger format validation on a dataset."""
    service = _get_service()
    return await service.validate_dataset(dataset_id)


@router.get("/vault/datasets/{dataset_id}/preview", response_model=DatasetPreview)
async def preview_dataset(
    dataset_id: str,
    limit: int = Query(default=10, ge=1, le=100),
):
    """Preview first N records of a dataset."""
    service = _get_service()
    return await service.preview_dataset(dataset_id, limit=limit)
