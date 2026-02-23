"""Unit tests for EvalService CRUD and state machine."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.core.exceptions import NotFoundError, VaultError
from app.schemas.eval import EvalConfig, EvalJobCreate
from app.services.eval.service import EvalService


@pytest_asyncio.fixture
async def eval_service():
    """EvalService backed by in-memory SQLite."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    service = EvalService(session_factory=session_factory)
    yield service
    await engine.dispose()


@pytest.fixture
def create_data() -> EvalJobCreate:
    return EvalJobCreate(
        name="Test Eval",
        model_id="qwen2.5-32b-awq",
        dataset_id="mmlu-mini",
        config=EvalConfig(metrics=["accuracy"]),
    )


@pytest.mark.asyncio
async def test_create_job(eval_service, create_data):
    job = await eval_service.create_job(create_data)
    assert job.status == "queued"
    assert job.name == "Test Eval"
    assert job.model_id == "qwen2.5-32b-awq"
    assert job.progress == 0.0


@pytest.mark.asyncio
async def test_get_job(eval_service, create_data):
    created = await eval_service.create_job(create_data)
    fetched = await eval_service.get_job(created.id)
    assert fetched.id == created.id
    assert fetched.name == created.name


@pytest.mark.asyncio
async def test_get_job_not_found(eval_service):
    with pytest.raises(NotFoundError):
        await eval_service.get_job("nonexistent")


@pytest.mark.asyncio
async def test_list_jobs_empty(eval_service):
    result = await eval_service.list_jobs()
    assert result.total == 0
    assert result.jobs == []


@pytest.mark.asyncio
async def test_list_jobs(eval_service, create_data):
    await eval_service.create_job(create_data)
    await eval_service.create_job(create_data)
    result = await eval_service.list_jobs()
    assert result.total == 2


@pytest.mark.asyncio
async def test_list_jobs_filter_model(eval_service):
    await eval_service.create_job(
        EvalJobCreate(name="A", model_id="model-a", dataset_id="ds")
    )
    await eval_service.create_job(
        EvalJobCreate(name="B", model_id="model-b", dataset_id="ds")
    )
    result = await eval_service.list_jobs(model_id="model-a")
    assert result.total == 1
    assert result.jobs[0].model_id == "model-a"


@pytest.mark.asyncio
async def test_list_jobs_filter_status(eval_service, create_data):
    await eval_service.create_job(create_data)
    result = await eval_service.list_jobs(status="queued")
    assert result.total == 1
    result = await eval_service.list_jobs(status="completed")
    assert result.total == 0


@pytest.mark.asyncio
async def test_cancel_job_from_queued(eval_service, create_data):
    job = await eval_service.create_job(create_data)
    cancelled = await eval_service.cancel_job(job.id)
    assert cancelled.status == "cancelled"
    assert cancelled.completed_at is not None


@pytest.mark.asyncio
async def test_cancel_job_invalid_status(eval_service, create_data):
    job = await eval_service.create_job(create_data)
    await eval_service.cancel_job(job.id)
    with pytest.raises(VaultError) as exc_info:
        await eval_service.cancel_job(job.id)
    assert exc_info.value.status == 409


@pytest.mark.asyncio
async def test_delete_job(eval_service, create_data):
    job = await eval_service.create_job(create_data)
    await eval_service.delete_job(job.id)
    with pytest.raises(NotFoundError):
        await eval_service.get_job(job.id)


@pytest.mark.asyncio
async def test_delete_job_not_found(eval_service):
    with pytest.raises(NotFoundError):
        await eval_service.delete_job("nonexistent")


@pytest.mark.asyncio
async def test_update_job_status(eval_service, create_data):
    job = await eval_service.create_job(create_data)
    updated = await eval_service.update_job_status(
        job.id, status="running", progress=50.0, examples_completed=100, total_examples=200
    )
    assert updated.status == "running"
    assert updated.progress == 50.0
    assert updated.examples_completed == 100


@pytest.mark.asyncio
async def test_compare_jobs_requires_two(eval_service, create_data):
    job = await eval_service.create_job(create_data)
    with pytest.raises(VaultError) as exc_info:
        await eval_service.compare_jobs([job.id])
    assert exc_info.value.status == 400


@pytest.mark.asyncio
async def test_compare_jobs_not_found(eval_service):
    with pytest.raises(NotFoundError):
        await eval_service.compare_jobs(["a", "b"])


@pytest.mark.asyncio
async def test_compare_jobs_must_be_completed(eval_service, create_data):
    j1 = await eval_service.create_job(create_data)
    j2 = await eval_service.create_job(create_data)
    with pytest.raises(VaultError) as exc_info:
        await eval_service.compare_jobs([j1.id, j2.id])
    assert exc_info.value.status == 400
    assert "completed" in exc_info.value.message


@pytest.mark.asyncio
async def test_compare_jobs_same_dataset(eval_service):
    j1 = await eval_service.create_job(
        EvalJobCreate(name="A", model_id="m", dataset_id="ds1")
    )
    j2 = await eval_service.create_job(
        EvalJobCreate(name="B", model_id="m", dataset_id="ds2")
    )
    # Mark both as completed
    import json
    await eval_service.update_job_status(j1.id, status="completed", results_json=json.dumps({"metrics": []}))
    await eval_service.update_job_status(j2.id, status="completed", results_json=json.dumps({"metrics": []}))
    with pytest.raises(VaultError) as exc_info:
        await eval_service.compare_jobs([j1.id, j2.id])
    assert "same dataset" in exc_info.value.message


@pytest.mark.asyncio
async def test_compare_jobs_success(eval_service):
    import json
    j1 = await eval_service.create_job(
        EvalJobCreate(name="Base", model_id="m1", dataset_id="mmlu")
    )
    j2 = await eval_service.create_job(
        EvalJobCreate(name="Tuned", model_id="m1", adapter_id="a1", dataset_id="mmlu")
    )
    results_json = json.dumps({"metrics": [{"metric": "accuracy", "score": 0.85}]})
    await eval_service.update_job_status(j1.id, status="completed", results_json=results_json)
    await eval_service.update_job_status(j2.id, status="completed", results_json=results_json)
    compare = await eval_service.compare_jobs([j1.id, j2.id])
    assert compare.dataset_id == "mmlu"
    assert len(compare.models) == 2
