"""Unit tests for DatasetService (Epic 22)."""

import json
import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, Dataset
from app.services.dataset.dataset_service import DatasetService


@pytest_asyncio.fixture
async def session_factory():
    """In-memory SQLite engine + session for unit tests."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def service(session_factory):
    return DatasetService(session_factory=session_factory)


@pytest_asyncio.fixture
async def sample_dataset(tmp_path):
    """Create a sample JSONL file on disk."""
    path = tmp_path / "sample.jsonl"
    path.write_text('{"text": "hello"}\n{"text": "world"}\n{"text": "test"}\n')
    return path


@pytest_asyncio.fixture
async def sample_csv(tmp_path):
    """Create a sample CSV file on disk."""
    path = tmp_path / "sample.csv"
    path.write_text("prompt,response\nhello,world\nfoo,bar\n")
    return path


# ── create_dataset ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_dataset(service, sample_dataset):
    from app.schemas.dataset import DatasetCreate
    result = await service.create_dataset(DatasetCreate(
        name="Test Dataset",
        source_path=str(sample_dataset),
        dataset_type="training",
        format="jsonl",
        tags=["test"],
    ))
    assert result.name == "Test Dataset"
    assert result.dataset_type == "training"
    assert result.format == "jsonl"
    assert result.status == "registered"
    assert result.registered_by == "manual"
    assert result.tags == ["test"]
    assert result.file_size_bytes > 0


@pytest.mark.asyncio
async def test_create_dataset_no_file(service):
    from app.schemas.dataset import DatasetCreate
    result = await service.create_dataset(DatasetCreate(
        name="Remote",
        source_path="/nonexistent/path.csv",
        format="csv",
    ))
    assert result.file_size_bytes == 0


# ── get_dataset ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_dataset(service, sample_dataset):
    from app.schemas.dataset import DatasetCreate
    created = await service.create_dataset(DatasetCreate(
        name="Get Test", source_path=str(sample_dataset), format="jsonl",
    ))
    fetched = await service.get_dataset(created.id)
    assert fetched.id == created.id
    assert fetched.name == "Get Test"


@pytest.mark.asyncio
async def test_get_dataset_not_found(service):
    from app.core.exceptions import NotFoundError
    with pytest.raises(NotFoundError):
        await service.get_dataset("nonexistent-uuid")


# ── list_datasets ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_datasets_empty(service):
    result = await service.list_datasets()
    assert result.total == 0
    assert result.datasets == []


@pytest.mark.asyncio
async def test_list_datasets_with_filter(service, sample_dataset):
    from app.schemas.dataset import DatasetCreate
    await service.create_dataset(DatasetCreate(
        name="Train", source_path=str(sample_dataset),
        dataset_type="training", format="jsonl",
    ))
    await service.create_dataset(DatasetCreate(
        name="Eval", source_path=str(sample_dataset),
        dataset_type="eval", format="jsonl",
    ))

    result = await service.list_datasets(dataset_type="training")
    assert result.total == 1
    assert result.datasets[0].name == "Train"


@pytest.mark.asyncio
async def test_list_datasets_search(service, sample_dataset):
    from app.schemas.dataset import DatasetCreate
    await service.create_dataset(DatasetCreate(
        name="Alpha Dataset", source_path=str(sample_dataset), format="jsonl",
    ))
    await service.create_dataset(DatasetCreate(
        name="Beta Dataset", source_path=str(sample_dataset), format="jsonl",
    ))

    result = await service.list_datasets(search="Alpha")
    assert result.total == 1


@pytest.mark.asyncio
async def test_list_datasets_pagination(service, sample_dataset):
    from app.schemas.dataset import DatasetCreate
    for i in range(5):
        await service.create_dataset(DatasetCreate(
            name=f"DS-{i}", source_path=str(sample_dataset), format="jsonl",
        ))
    result = await service.list_datasets(offset=2, limit=2)
    assert len(result.datasets) == 2
    assert result.total == 5


# ── update_dataset ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_dataset(service, sample_dataset):
    from app.schemas.dataset import DatasetCreate, DatasetUpdate
    created = await service.create_dataset(DatasetCreate(
        name="Original", source_path=str(sample_dataset), format="jsonl",
    ))
    updated = await service.update_dataset(created.id, DatasetUpdate(
        name="Updated", tags=["new-tag"],
    ))
    assert updated.name == "Updated"
    assert updated.tags == ["new-tag"]


# ── delete_dataset ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_dataset(service, sample_dataset):
    from app.schemas.dataset import DatasetCreate
    created = await service.create_dataset(DatasetCreate(
        name="ToDelete", source_path=str(sample_dataset), format="jsonl",
    ))
    await service.delete_dataset(created.id)
    from app.core.exceptions import NotFoundError
    with pytest.raises(NotFoundError):
        await service.get_dataset(created.id)


@pytest.mark.asyncio
async def test_delete_dataset_with_file(service, tmp_path):
    from app.schemas.dataset import DatasetCreate
    file_path = tmp_path / "deleteme.jsonl"
    file_path.write_text('{"a": 1}\n')
    created = await service.create_dataset(DatasetCreate(
        name="Delete File", source_path=str(file_path), format="jsonl",
    ))
    assert file_path.exists()
    await service.delete_dataset(created.id, delete_file=True)
    assert not file_path.exists()


# ── upload_dataset ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_dataset(service, tmp_path):
    from app.config import settings
    original_dir = settings.vault_datasets_dir
    settings.vault_datasets_dir = str(tmp_path / "uploads")

    content = b'{"text": "uploaded"}\n'
    result = await service.upload_dataset(
        file_content=content,
        filename="my_data.jsonl",
        name="My Upload",
        dataset_type="training",
    )
    assert result.name == "My Upload"
    assert result.format == "jsonl"
    assert result.file_size_bytes == len(content)
    assert result.status == "uploaded"

    settings.vault_datasets_dir = original_dir


# ── validate_dataset ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_jsonl(service, sample_dataset):
    from app.schemas.dataset import DatasetCreate
    created = await service.create_dataset(DatasetCreate(
        name="Validate", source_path=str(sample_dataset), format="jsonl",
    ))
    result = await service.validate_dataset(created.id)
    assert result.valid is True
    assert result.record_count == 3
    assert result.errors == []


@pytest.mark.asyncio
async def test_validate_invalid_jsonl(service, tmp_path):
    from app.schemas.dataset import DatasetCreate
    bad_file = tmp_path / "bad.jsonl"
    bad_file.write_text('{"valid": true}\nnot json\n')
    created = await service.create_dataset(DatasetCreate(
        name="Bad JSONL", source_path=str(bad_file), format="jsonl",
    ))
    result = await service.validate_dataset(created.id)
    assert result.valid is False
    assert len(result.errors) > 0


@pytest.mark.asyncio
async def test_validate_csv(service, sample_csv):
    from app.schemas.dataset import DatasetCreate
    created = await service.create_dataset(DatasetCreate(
        name="CSV", source_path=str(sample_csv), format="csv",
    ))
    result = await service.validate_dataset(created.id)
    assert result.valid is True
    assert result.record_count == 2  # 2 data rows, 1 header


@pytest.mark.asyncio
async def test_validate_missing_file(service):
    from app.schemas.dataset import DatasetCreate
    created = await service.create_dataset(DatasetCreate(
        name="Missing", source_path="/nonexistent/data.jsonl", format="jsonl",
    ))
    result = await service.validate_dataset(created.id)
    assert result.valid is False
    assert any("not found" in e.lower() or "File not found" in e for e in result.errors)


# ── preview_dataset ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preview_jsonl(service, sample_dataset):
    from app.schemas.dataset import DatasetCreate
    created = await service.create_dataset(DatasetCreate(
        name="Preview", source_path=str(sample_dataset), format="jsonl",
    ))
    result = await service.preview_dataset(created.id, limit=2)
    assert len(result.preview_records) == 2
    assert result.preview_records[0]["text"] == "hello"


@pytest.mark.asyncio
async def test_preview_csv(service, sample_csv):
    from app.schemas.dataset import DatasetCreate
    created = await service.create_dataset(DatasetCreate(
        name="CSV Preview", source_path=str(sample_csv), format="csv",
    ))
    result = await service.preview_dataset(created.id, limit=5)
    assert len(result.preview_records) == 2
    assert "prompt" in result.preview_records[0]


# ── get_stats ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_stats(service, sample_dataset):
    from app.schemas.dataset import DatasetCreate
    await service.create_dataset(DatasetCreate(
        name="A", source_path=str(sample_dataset), dataset_type="training", format="jsonl",
    ))
    await service.create_dataset(DatasetCreate(
        name="B", source_path=str(sample_dataset), dataset_type="eval", format="csv",
    ))
    stats = await service.get_stats()
    assert stats.total_datasets == 2
    assert stats.by_type["training"] == 1
    assert stats.by_type["eval"] == 1
    assert stats.by_format["jsonl"] == 1
    assert stats.by_format["csv"] == 1


# ── list_by_type ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_by_type(service, sample_dataset):
    from app.schemas.dataset import DatasetCreate
    await service.create_dataset(DatasetCreate(
        name="Train1", source_path=str(sample_dataset), dataset_type="training", format="jsonl",
    ))
    await service.create_dataset(DatasetCreate(
        name="Eval1", source_path=str(sample_dataset), dataset_type="eval", format="jsonl",
    ))
    result = await service.list_by_type("training")
    assert result.total == 1
    assert result.datasets[0].name == "Train1"


# ── resolve_dataset_path ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_by_uuid(service, sample_dataset):
    from app.schemas.dataset import DatasetCreate
    created = await service.create_dataset(DatasetCreate(
        name="Resolve", source_path=str(sample_dataset), format="jsonl",
    ))
    resolved = await service.resolve_dataset_path(created.id)
    assert resolved == str(sample_dataset)


@pytest.mark.asyncio
async def test_resolve_passthrough(service):
    result = await service.resolve_dataset_path("/some/arbitrary/path.jsonl")
    assert result == "/some/arbitrary/path.jsonl"
