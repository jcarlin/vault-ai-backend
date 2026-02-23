"""Data source management endpoints (Epic 22)."""

from fastapi import APIRouter, Depends, Request

import app.core.database as db_module
from app.dependencies import require_admin
from app.schemas.dataset import (
    DataSourceCreate,
    DataSourceList,
    DataSourceResponse,
    DataSourceScanResult,
    DataSourceTestResult,
    DataSourceUpdate,
)
from app.services.dataset.source_service import DataSourceService

router = APIRouter()


def _get_service() -> DataSourceService:
    return DataSourceService(session_factory=db_module.async_session)


@router.post(
    "/vault/admin/datasources",
    response_model=DataSourceResponse,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
async def create_datasource(data: DataSourceCreate, request: Request):
    """Create a new data source."""
    service = _get_service()
    return await service.create_source(data)


@router.get(
    "/vault/admin/datasources",
    response_model=DataSourceList,
    dependencies=[Depends(require_admin)],
)
async def list_datasources(request: Request):
    """List all data sources."""
    service = _get_service()
    return await service.list_sources()


@router.put(
    "/vault/admin/datasources/{source_id}",
    response_model=DataSourceResponse,
    dependencies=[Depends(require_admin)],
)
async def update_datasource(source_id: str, data: DataSourceUpdate, request: Request):
    """Update a data source configuration."""
    service = _get_service()
    return await service.update_source(source_id, data)


@router.delete(
    "/vault/admin/datasources/{source_id}",
    status_code=204,
    dependencies=[Depends(require_admin)],
)
async def delete_datasource(source_id: str, request: Request):
    """Soft-delete a data source (sets status=disabled)."""
    service = _get_service()
    await service.delete_source(source_id)


@router.post(
    "/vault/admin/datasources/{source_id}/test",
    response_model=DataSourceTestResult,
    dependencies=[Depends(require_admin)],
)
async def test_datasource(source_id: str, request: Request):
    """Test connectivity to a data source."""
    service = _get_service()
    return await service.test_source(source_id)


@router.post(
    "/vault/admin/datasources/{source_id}/scan",
    response_model=DataSourceScanResult,
    dependencies=[Depends(require_admin)],
)
async def scan_datasource(source_id: str, request: Request):
    """Scan a data source to discover datasets."""
    service = _get_service()
    return await service.scan_source(source_id)
