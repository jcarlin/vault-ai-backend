"""Data source CRUD + connectivity service (Epic 22)."""

import json
import os
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.core.database import DataSource, Dataset, async_session as default_session_factory
from app.core.exceptions import NotFoundError, VaultError
from app.schemas.dataset import (
    DataSourceCreate,
    DataSourceList,
    DataSourceResponse,
    DataSourceScanResult,
    DataSourceTestResult,
    DataSourceUpdate,
)
from app.services.dataset.connectors import get_connector

logger = structlog.get_logger()

# Format inference from file extension
_EXT_TO_FORMAT = {
    ".jsonl": "jsonl",
    ".json": "jsonl",
    ".csv": "csv",
    ".parquet": "parquet",
    ".txt": "txt",
    ".pdf": "pdf",
}


def _row_to_response(row: DataSource) -> DataSourceResponse:
    config = json.loads(row.config_json) if row.config_json else {}
    return DataSourceResponse(
        id=row.id,
        name=row.name,
        source_type=row.source_type,
        status=row.status,
        config=config,
        last_scanned_at=row.last_scanned_at.isoformat() + "Z" if row.last_scanned_at else None,
        last_error=row.last_error,
        created_at=row.created_at.isoformat() + "Z" if row.created_at else "",
        updated_at=row.updated_at.isoformat() + "Z" if row.updated_at else "",
    )


def _infer_format(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return _EXT_TO_FORMAT.get(ext, "mixed")


async def _count_records(path: str, fmt: str) -> int:
    """Count records in a file (best-effort)."""
    import asyncio

    def _count():
        try:
            if fmt == "jsonl":
                count = 0
                with open(path, "r") as f:
                    for line in f:
                        if line.strip():
                            count += 1
                return count
            elif fmt == "csv":
                count = 0
                with open(path, "r") as f:
                    for _ in f:
                        count += 1
                return max(0, count - 1)  # subtract header
        except Exception:
            pass
        return 0

    return await asyncio.to_thread(_count)


class DataSourceService:
    def __init__(self, session_factory: async_sessionmaker | None = None):
        self._session_factory = session_factory or default_session_factory

    async def create_source(self, data: DataSourceCreate) -> DataSourceResponse:
        if data.source_type not in ("local", "s3", "smb", "nfs"):
            raise VaultError(
                code="invalid_source_type",
                message=f"Unsupported source type: {data.source_type}",
                status=400,
            )

        row = DataSource(
            id=str(uuid.uuid4()),
            name=data.name,
            source_type=data.source_type,
            status="active",
            config_json=json.dumps(data.config) if data.config else None,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_response(row)

    async def list_sources(self) -> DataSourceList:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DataSource).order_by(DataSource.created_at.desc())
            )
            rows = list(result.scalars().all())
            return DataSourceList(
                sources=[_row_to_response(r) for r in rows],
                total=len(rows),
            )

    async def get_source(self, source_id: str) -> DataSource:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DataSource).where(DataSource.id == source_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Data source '{source_id}' not found.")
            return row

    async def update_source(self, source_id: str, data: DataSourceUpdate) -> DataSourceResponse:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DataSource).where(DataSource.id == source_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Data source '{source_id}' not found.")

            if data.name is not None:
                row.name = data.name
            if data.config is not None:
                row.config_json = json.dumps(data.config)
            if data.status is not None:
                row.status = data.status

            await session.commit()
            await session.refresh(row)
            return _row_to_response(row)

    async def delete_source(self, source_id: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DataSource).where(DataSource.id == source_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Data source '{source_id}' not found.")

            row.status = "disabled"
            await session.commit()

    async def test_source(self, source_id: str) -> DataSourceTestResult:
        source = await self.get_source(source_id)
        try:
            connector = get_connector(source)
            success, message = await connector.test_connection()
            files_found = 0
            if success:
                patterns = settings.vault_datasets_scan_patterns.split(",")
                files = await connector.list_files(patterns)
                files_found = len(files)
            return DataSourceTestResult(
                success=success,
                message=message,
                files_found=files_found,
            )
        except Exception as e:
            return DataSourceTestResult(success=False, message=str(e), files_found=0)

    async def scan_source(self, source_id: str) -> DataSourceScanResult:
        source = await self.get_source(source_id)
        try:
            connector = get_connector(source)
            patterns = settings.vault_datasets_scan_patterns.split(",")
            files = await connector.list_files(patterns)
        except Exception as e:
            # Update source with error
            async with self._session_factory() as session:
                result = await session.execute(
                    select(DataSource).where(DataSource.id == source_id)
                )
                row = result.scalar_one_or_none()
                if row:
                    row.last_error = str(e)
                    await session.commit()
            raise VaultError(
                code="scan_failed",
                message=f"Failed to scan source: {e}",
                status=500,
            )

        discovered = 0
        updated = 0
        errors = []

        async with self._session_factory() as session:
            for file_info in files:
                file_path = file_info["path"]
                relative_path = file_info.get("relative_path", file_path)

                try:
                    # Check if dataset already exists for this source+path
                    existing = await session.execute(
                        select(Dataset).where(
                            Dataset.source_id == source_id,
                            Dataset.source_path == file_path,
                        )
                    )
                    existing_row = existing.scalar_one_or_none()

                    fmt = _infer_format(file_path)
                    name = os.path.splitext(os.path.basename(relative_path))[0]

                    # Count records for supported formats
                    record_count = 0
                    if fmt in ("jsonl", "csv") and source.source_type == "local":
                        record_count = await _count_records(file_path, fmt)

                    if existing_row:
                        existing_row.file_size_bytes = file_info["size"]
                        existing_row.record_count = record_count
                        existing_row.format = fmt
                        updated += 1
                    else:
                        dataset_row = Dataset(
                            id=str(uuid.uuid4()),
                            name=name,
                            dataset_type="other",
                            format=fmt,
                            status="discovered",
                            source_id=source_id,
                            source_path=file_path,
                            file_size_bytes=file_info["size"],
                            record_count=record_count,
                            registered_by="scan",
                        )
                        session.add(dataset_row)
                        discovered += 1

                except Exception as e:
                    errors.append(f"{file_path}: {e}")

            # Update source scan timestamp
            source_result = await session.execute(
                select(DataSource).where(DataSource.id == source_id)
            )
            source_row = source_result.scalar_one_or_none()
            if source_row:
                source_row.last_scanned_at = datetime.now(timezone.utc)
                source_row.last_error = None

            await session.commit()

        logger.info(
            "datasource_scanned",
            source_id=source_id,
            discovered=discovered,
            updated=updated,
            errors=len(errors),
        )

        return DataSourceScanResult(
            source_id=source_id,
            datasets_discovered=discovered,
            datasets_updated=updated,
            errors=errors,
        )
