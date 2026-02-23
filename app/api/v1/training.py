from pathlib import Path

from fastapi import APIRouter, Request

import app.core.database as db_module
from app.config import settings
from app.core.exceptions import VaultError
from app.schemas.training import (
    DatasetValidationRequest,
    DatasetValidationResponse,
    GPUAllocationStatus,
    TrainingJobCreate,
    TrainingJobList,
    TrainingJobResponse,
)
from app.services.training.config import TrainingRunConfig
from app.services.training.service import TrainingService
from app.services.training.validation import validate_dataset

router = APIRouter()


def _get_service() -> TrainingService:
    return TrainingService(session_factory=db_module.async_session)


@router.get("/vault/training/jobs", response_model=TrainingJobList)
async def list_training_jobs():
    """List all training jobs."""
    service = _get_service()
    return await service.list_jobs()


@router.post("/vault/training/jobs", response_model=TrainingJobResponse, status_code=201)
async def create_training_job(data: TrainingJobCreate, request: Request):
    """Create and start a new training job.

    Validates the dataset, checks GPU availability, creates the DB record,
    and launches the training subprocess.
    """
    service = _get_service()

    # Resolve dataset path through registry if it's a UUID (Epic 22)
    dataset_path = data.dataset
    try:
        from app.services.dataset.dataset_service import DatasetService
        ds_service = DatasetService(session_factory=db_module.async_session)
        dataset_path = await ds_service.resolve_dataset_path(data.dataset)
    except Exception:
        pass

    # Create the DB record first
    job = await service.create_job(data)

    # Try to start the actual training if a runner is available
    runner = getattr(request.app.state, "training_runner", None)
    if runner is not None:
        try:
            scheduler = getattr(request.app.state, "gpu_scheduler", None)
            progress_tracker = getattr(request.app.state, "progress_tracker", None)
            status_dir = progress_tracker.get_status_dir(job.id) if progress_tracker else f"/tmp/vault-training/{job.id}"

            # Resolve model path from manifest
            from app.services.model_manager import ModelManager
            manager = ModelManager()
            manifest = manager._load_manifest()
            model_entry = next((m for m in manifest if m["id"] == data.model), None)
            base_model_path = model_entry["path"] if model_entry else data.model

            # Build run config
            run_config = TrainingRunConfig(
                job_id=job.id,
                base_model_path=base_model_path,
                dataset_path=dataset_path,
                output_dir=str(Path(settings.vault_adapters_dir) / job.id),
                status_dir=status_dir,
                adapter_type=data.adapter_type,
                lora_rank=data.lora_config.rank,
                lora_alpha=data.lora_config.alpha,
                lora_dropout=data.lora_config.dropout,
                lora_target_modules=data.lora_config.target_modules,
                quantization_bits=data.lora_config.quantization_bits,
                epochs=data.config.epochs,
                batch_size=data.config.batch_size,
                learning_rate=data.config.learning_rate,
                warmup_steps=data.config.warmup_steps,
                weight_decay=data.config.weight_decay,
            )

            await runner.start_job(job.id, run_config)

            # Re-fetch to get updated status
            job = await service.get_job(job.id)

        except VaultError:
            raise
        except Exception as e:
            # Job was created but runner failed — leave in queued status
            await service.update_job_status(
                job.id,
                error=f"Failed to start training: {e}",
            )
            job = await service.get_job(job.id)

    return job


@router.get("/vault/training/jobs/{job_id}", response_model=TrainingJobResponse)
async def get_training_job(job_id: str):
    """Get training job details."""
    service = _get_service()
    return await service.get_job(job_id)


@router.post("/vault/training/jobs/{job_id}/pause", response_model=TrainingJobResponse)
async def pause_training_job(job_id: str, request: Request):
    """Pause a running training job."""
    service = _get_service()

    # Signal the runner if available
    runner = getattr(request.app.state, "training_runner", None)
    if runner is not None and runner.active_job_id == job_id:
        await runner.pause_job(job_id)

    return await service.pause_job(job_id)


@router.post("/vault/training/jobs/{job_id}/resume", response_model=TrainingJobResponse)
async def resume_training_job(job_id: str, request: Request):
    """Resume a paused training job."""
    service = _get_service()

    # TODO: restart training subprocess from checkpoint
    # For now, just transition the status
    return await service.resume_job(job_id)


@router.post("/vault/training/jobs/{job_id}/cancel", response_model=TrainingJobResponse)
async def cancel_training_job(job_id: str, request: Request):
    """Cancel a training job."""
    service = _get_service()

    # Signal the runner if available
    runner = getattr(request.app.state, "training_runner", None)
    if runner is not None and runner.active_job_id == job_id:
        await runner.cancel_job(job_id)

    return await service.cancel_job(job_id)


@router.delete("/vault/training/jobs/{job_id}", status_code=204)
async def delete_training_job(job_id: str):
    """Delete a training job record."""
    service = _get_service()
    await service.delete_job(job_id)


# ── Dataset Validation ──────────────────────────────────────────────────────


@router.post("/vault/training/validate", response_model=DatasetValidationResponse)
async def validate_training_dataset(data: DatasetValidationRequest):
    """Validate a dataset for training (dry-run). Checks format, record count, and quality."""
    return await validate_dataset(data.path, session_factory=db_module.async_session)


# ── GPU Allocation ──────────────────────────────────────────────────────────


@router.get("/vault/training/gpu-allocation", response_model=list[GPUAllocationStatus])
async def get_gpu_allocation(request: Request):
    """Get current GPU allocation status."""
    scheduler = getattr(request.app.state, "gpu_scheduler", None)
    if scheduler is None:
        return [GPUAllocationStatus(gpu_index=0, assigned_to="inference")]

    allocations = await scheduler.get_allocation_status()
    return [GPUAllocationStatus(**a) for a in allocations]
