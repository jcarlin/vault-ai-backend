"""Dataset CRUD + management service (Epic 22)."""

import asyncio
import csv
import io
import json
import os
import shutil
import uuid
from pathlib import Path

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.core.database import Dataset, async_session as default_session_factory
from app.core.exceptions import NotFoundError, VaultError
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

logger = structlog.get_logger()


def _row_to_response(row: Dataset) -> DatasetResponse:
    tags = json.loads(row.tags_json) if row.tags_json else []
    metadata = json.loads(row.metadata_json) if row.metadata_json else {}
    validation = json.loads(row.validation_json) if row.validation_json else None
    return DatasetResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        dataset_type=row.dataset_type,
        format=row.format,
        status=row.status,
        source_id=row.source_id,
        source_path=row.source_path,
        file_size_bytes=row.file_size_bytes,
        record_count=row.record_count,
        tags=tags,
        metadata=metadata,
        quarantine_job_id=row.quarantine_job_id,
        validation=validation,
        registered_by=row.registered_by,
        created_at=row.created_at.isoformat() + "Z" if row.created_at else "",
        updated_at=row.updated_at.isoformat() + "Z" if row.updated_at else "",
    )


class DatasetService:
    def __init__(self, session_factory: async_sessionmaker | None = None):
        self._session_factory = session_factory or default_session_factory

    async def list_datasets(
        self,
        dataset_type: str | None = None,
        status: str | None = None,
        source_id: str | None = None,
        tags: list[str] | None = None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> DatasetList:
        async with self._session_factory() as session:
            stmt = select(Dataset).order_by(Dataset.created_at.desc())

            if dataset_type:
                stmt = stmt.where(Dataset.dataset_type == dataset_type)
            if status:
                stmt = stmt.where(Dataset.status == status)
            if source_id:
                stmt = stmt.where(Dataset.source_id == source_id)
            if search:
                stmt = stmt.where(Dataset.name.ilike(f"%{search}%"))
            if tags:
                for tag in tags:
                    stmt = stmt.where(Dataset.tags_json.ilike(f'%"{tag}"%'))

            # Get total count before pagination
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = await session.scalar(count_stmt)

            # Apply pagination
            stmt = stmt.offset(offset).limit(limit)
            result = await session.execute(stmt)
            rows = list(result.scalars().all())

            return DatasetList(
                datasets=[_row_to_response(r) for r in rows],
                total=total or 0,
            )

    async def get_dataset(self, dataset_id: str) -> DatasetResponse:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Dataset).where(Dataset.id == dataset_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Dataset '{dataset_id}' not found.")
            return _row_to_response(row)

    async def create_dataset(self, data: DatasetCreate) -> DatasetResponse:
        row = Dataset(
            id=str(uuid.uuid4()),
            name=data.name,
            description=data.description,
            dataset_type=data.dataset_type,
            format=data.format,
            status="registered",
            source_path=data.source_path,
            tags_json=json.dumps(data.tags) if data.tags else None,
            registered_by="manual",
        )

        # Try to get file size if local path exists
        source_path = Path(data.source_path)
        if source_path.exists():
            row.file_size_bytes = source_path.stat().st_size

        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_response(row)

    async def upload_dataset(
        self,
        file_content: bytes,
        filename: str,
        name: str | None = None,
        description: str | None = None,
        dataset_type: str = "other",
        tags: list[str] | None = None,
    ) -> DatasetUploadResponse:
        datasets_dir = Path(settings.vault_datasets_dir)
        datasets_dir.mkdir(parents=True, exist_ok=True)

        # Infer format from extension
        ext = os.path.splitext(filename)[1].lower()
        fmt_map = {".jsonl": "jsonl", ".json": "jsonl", ".csv": "csv", ".parquet": "parquet", ".txt": "txt", ".pdf": "pdf"}
        fmt = fmt_map.get(ext, "mixed")

        dataset_id = str(uuid.uuid4())
        dest_path = datasets_dir / f"{dataset_id}{ext}"

        def _write():
            with open(dest_path, "wb") as f:
                f.write(file_content)

        await asyncio.to_thread(_write)

        display_name = name or os.path.splitext(filename)[0]
        file_size = len(file_content)

        row = Dataset(
            id=dataset_id,
            name=display_name,
            description=description,
            dataset_type=dataset_type,
            format=fmt,
            status="uploaded",
            source_path=str(dest_path),
            file_size_bytes=file_size,
            tags_json=json.dumps(tags) if tags else None,
            registered_by="upload",
        )

        async with self._session_factory() as session:
            session.add(row)
            await session.commit()

        return DatasetUploadResponse(
            id=dataset_id,
            name=display_name,
            format=fmt,
            file_size_bytes=file_size,
            status="uploaded",
        )

    async def update_dataset(self, dataset_id: str, data: DatasetUpdate) -> DatasetResponse:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Dataset).where(Dataset.id == dataset_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Dataset '{dataset_id}' not found.")

            if data.name is not None:
                row.name = data.name
            if data.description is not None:
                row.description = data.description
            if data.dataset_type is not None:
                row.dataset_type = data.dataset_type
            if data.tags is not None:
                row.tags_json = json.dumps(data.tags)

            await session.commit()
            await session.refresh(row)
            return _row_to_response(row)

    async def delete_dataset(self, dataset_id: str, delete_file: bool = False) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Dataset).where(Dataset.id == dataset_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Dataset '{dataset_id}' not found.")

            if delete_file and row.source_path:
                path = Path(row.source_path)
                if path.exists():
                    await asyncio.to_thread(path.unlink)

            await session.delete(row)
            await session.commit()

    async def validate_dataset(self, dataset_id: str) -> DatasetValidateResponse:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Dataset).where(Dataset.id == dataset_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Dataset '{dataset_id}' not found.")

            source_path = row.source_path
            fmt = row.format

        errors = []
        warnings = []
        record_count = 0
        format_detected = fmt

        def _validate():
            nonlocal errors, warnings, record_count, format_detected
            path = Path(source_path)

            if not path.exists():
                errors.append(f"File not found: {source_path}")
                return

            if path.stat().st_size == 0:
                errors.append("File is empty")
                return

            if fmt == "jsonl":
                count = 0
                line_errors = 0
                with open(path, "r") as f:
                    for i, line in enumerate(f, 1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            json.loads(line)
                            count += 1
                        except json.JSONDecodeError:
                            line_errors += 1
                            if line_errors <= 3:
                                errors.append(f"Invalid JSON at line {i}")
                record_count = count
                if line_errors > 3:
                    errors.append(f"... and {line_errors - 3} more JSON errors")

            elif fmt == "csv":
                with open(path, "r") as f:
                    reader = csv.reader(f)
                    header = next(reader, None)
                    if header is None:
                        errors.append("CSV file has no header row")
                        return
                    if len(header) < 2:
                        warnings.append("CSV has only one column")
                    count = 0
                    for _ in reader:
                        count += 1
                    record_count = count

            elif fmt == "txt":
                with open(path, "r") as f:
                    count = sum(1 for line in f if line.strip())
                record_count = count

            else:
                warnings.append(f"Format '{fmt}' validation is basic (file existence only)")
                record_count = 0

        await asyncio.to_thread(_validate)

        valid = len(errors) == 0

        # Update the dataset record with validation results
        validation_data = {
            "valid": valid,
            "errors": errors,
            "warnings": warnings,
            "record_count": record_count,
        }
        async with self._session_factory() as session:
            result = await session.execute(
                select(Dataset).where(Dataset.id == dataset_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.validation_json = json.dumps(validation_data)
                row.record_count = record_count
                row.status = "validated" if valid else "invalid"
                await session.commit()

        return DatasetValidateResponse(
            id=dataset_id,
            valid=valid,
            errors=errors,
            warnings=warnings,
            record_count=record_count,
            format_detected=format_detected,
        )

    async def preview_dataset(self, dataset_id: str, limit: int = 10) -> DatasetPreview:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Dataset).where(Dataset.id == dataset_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Dataset '{dataset_id}' not found.")

            source_path = row.source_path
            fmt = row.format
            name = row.name
            record_count = row.record_count

        records = []

        def _preview():
            nonlocal records
            path = Path(source_path)
            if not path.exists():
                return

            if fmt == "jsonl":
                with open(path, "r") as f:
                    count = 0
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            records.append(json.loads(line))
                            count += 1
                            if count >= limit:
                                break
                        except json.JSONDecodeError:
                            continue

            elif fmt == "csv":
                with open(path, "r") as f:
                    reader = csv.DictReader(f)
                    count = 0
                    for row_dict in reader:
                        records.append(dict(row_dict))
                        count += 1
                        if count >= limit:
                            break

            elif fmt == "txt":
                with open(path, "r") as f:
                    count = 0
                    for line in f:
                        line = line.strip()
                        if line:
                            records.append({"text": line})
                            count += 1
                            if count >= limit:
                                break

        await asyncio.to_thread(_preview)

        return DatasetPreview(
            id=dataset_id,
            name=name,
            format=fmt,
            total_records=record_count,
            preview_records=records,
        )

    async def list_by_type(self, dataset_type: str) -> DatasetList:
        return await self.list_datasets(dataset_type=dataset_type, limit=1000)

    async def get_stats(self) -> DatasetStats:
        async with self._session_factory() as session:
            result = await session.execute(select(Dataset))
            rows = list(result.scalars().all())

        by_type: dict[str, int] = {}
        by_format: dict[str, int] = {}
        by_status: dict[str, int] = {}
        total_size = 0

        for row in rows:
            by_type[row.dataset_type] = by_type.get(row.dataset_type, 0) + 1
            by_format[row.format] = by_format.get(row.format, 0) + 1
            by_status[row.status] = by_status.get(row.status, 0) + 1
            total_size += row.file_size_bytes

        return DatasetStats(
            total_datasets=len(rows),
            by_type=by_type,
            by_format=by_format,
            by_status=by_status,
            total_size_bytes=total_size,
        )

    async def resolve_dataset_path(self, id_or_path: str) -> str:
        """Resolve a dataset ID or path to a filesystem path.

        Checks in order:
        1. UUID lookup in datasets table
        2. Builtin eval dataset path
        3. Raw path passthrough
        """
        # Try UUID lookup
        try:
            uuid.UUID(id_or_path)
            # It's a valid UUID, look it up
            async with self._session_factory() as session:
                result = await session.execute(
                    select(Dataset).where(Dataset.id == id_or_path)
                )
                row = result.scalar_one_or_none()
                if row:
                    return row.source_path
        except ValueError:
            pass  # Not a UUID

        # Builtin eval dataset check
        datasets_dir = getattr(settings, "vault_eval_datasets_dir", "data/eval-datasets")
        builtin_path = Path(datasets_dir) / f"{id_or_path}.jsonl"
        if builtin_path.exists():
            return str(builtin_path)

        # Raw path passthrough
        return id_or_path
