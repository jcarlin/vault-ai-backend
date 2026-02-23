"""Evaluation & benchmarking endpoints (Epic 17)."""

import json
from pathlib import Path

from fastapi import APIRouter, Query, Request

import app.core.database as db_module
from app.config import settings
from app.core.exceptions import VaultError
from app.schemas.eval import (
    EvalCompareResponse,
    EvalDatasetInfo,
    EvalDatasetList,
    EvalJobCreate,
    EvalJobList,
    EvalJobResponse,
    QuickEvalRequest,
    QuickEvalResponse,
)
from app.services.eval.config import EvalRunConfig
from app.services.eval.quick import run_quick_eval
from app.services.eval.service import EvalService

router = APIRouter()


def _get_service() -> EvalService:
    return EvalService(session_factory=db_module.async_session)


def _resolve_dataset_path(dataset_id: str) -> tuple[str, str]:
    """Resolve dataset ID to filesystem path and type.

    Returns (path, dataset_type).
    """
    datasets_dir = getattr(settings, "vault_eval_datasets_dir", "data/eval-datasets")
    builtin_path = Path(datasets_dir) / f"{dataset_id}.jsonl"
    if builtin_path.exists():
        return str(builtin_path), "builtin"

    # Try as a direct path for custom datasets
    custom_path = Path(dataset_id)
    if custom_path.exists():
        return str(custom_path), "custom"

    raise VaultError(
        code="dataset_not_found",
        message=f"Dataset '{dataset_id}' not found.",
        status=404,
    )


@router.post("/vault/eval/jobs", response_model=EvalJobResponse, status_code=201)
async def create_eval_job(data: EvalJobCreate, request: Request):
    """Submit an evaluation job."""
    service = _get_service()

    # Validate dataset exists
    dataset_path, dataset_type = _resolve_dataset_path(data.dataset_id)

    job = await service.create_job(data)

    # Try to start the runner if available
    runner = getattr(request.app.state, "eval_runner", None)
    if runner is not None:
        try:
            status_dir = getattr(settings, "vault_eval_status_dir", "/tmp/vault-eval")
            job_status_dir = str(Path(status_dir) / job.id)

            run_config = EvalRunConfig(
                job_id=job.id,
                model_id=data.model_id,
                adapter_id=data.adapter_id,
                dataset_path=dataset_path,
                dataset_type=dataset_type,
                status_dir=job_status_dir,
                api_base_url=f"http://localhost:8000",
                metrics=data.config.metrics,
                num_samples=data.config.num_samples,
                few_shot=data.config.few_shot,
                batch_size=data.config.batch_size,
                max_tokens=data.config.max_tokens,
                temperature=data.config.temperature,
            )

            await runner.start_job(job.id, run_config)
            job = await service.get_job(job.id)

        except VaultError:
            raise
        except Exception as e:
            await service.update_job_status(
                job.id,
                error=f"Failed to start eval: {e}",
            )
            job = await service.get_job(job.id)

    return job


@router.get("/vault/eval/jobs", response_model=EvalJobList)
async def list_eval_jobs(
    model_id: str = Query(default=None),
    status: str = Query(default=None),
):
    """List eval jobs with optional filters."""
    service = _get_service()
    return await service.list_jobs(model_id=model_id, status=status)


@router.get("/vault/eval/jobs/{job_id}", response_model=EvalJobResponse)
async def get_eval_job(job_id: str):
    """Get eval job details including results."""
    service = _get_service()
    return await service.get_job(job_id)


@router.post("/vault/eval/jobs/{job_id}/cancel", response_model=EvalJobResponse)
async def cancel_eval_job(job_id: str, request: Request):
    """Cancel a queued or running eval job."""
    service = _get_service()

    # Signal the runner if available
    runner = getattr(request.app.state, "eval_runner", None)
    if runner is not None:
        try:
            await runner.cancel_job(job_id)
        except RuntimeError:
            pass  # Job may not be active in runner

    return await service.cancel_job(job_id)


@router.delete("/vault/eval/jobs/{job_id}", status_code=204)
async def delete_eval_job(job_id: str):
    """Delete an eval job record."""
    service = _get_service()
    await service.delete_job(job_id)


@router.get("/vault/eval/compare", response_model=EvalCompareResponse)
async def compare_eval_jobs(
    job_ids: str = Query(..., description="Comma-separated job IDs"),
):
    """Compare 2+ completed eval jobs."""
    ids = [jid.strip() for jid in job_ids.split(",") if jid.strip()]
    service = _get_service()
    return await service.compare_jobs(ids)


@router.post("/vault/eval/quick", response_model=QuickEvalResponse)
async def quick_eval(data: QuickEvalRequest, request: Request):
    """Run synchronous quick eval on up to 50 test cases."""
    if len(data.test_cases) > 50:
        raise VaultError(
            code="invalid_request",
            message="Quick eval supports a maximum of 50 test cases.",
            status=400,
        )

    # Get API key from the request for passing to inference calls
    auth_header = request.headers.get("authorization", "")
    api_key = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""

    backend = getattr(request.app.state, "inference_backend", None)
    api_base_url = backend.base_url if backend else "http://localhost:8000"

    return await run_quick_eval(
        request=data,
        api_base_url=api_base_url,
        api_key=api_key,
    )


@router.get("/vault/eval/datasets", response_model=EvalDatasetList)
async def list_eval_datasets():
    """List available eval datasets (builtin + custom)."""
    datasets_dir = getattr(settings, "vault_eval_datasets_dir", "data/eval-datasets")
    manifest_path = Path(datasets_dir) / "manifest.json"

    datasets = []
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            for entry in manifest.get("datasets", []):
                datasets.append(EvalDatasetInfo(**entry))
        except (json.JSONDecodeError, Exception):
            pass

    return EvalDatasetList(datasets=datasets, total=len(datasets))
