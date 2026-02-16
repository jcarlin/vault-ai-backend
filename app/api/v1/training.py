from fastapi import APIRouter

import app.core.database as db_module
from app.schemas.training import TrainingJobCreate, TrainingJobList, TrainingJobResponse
from app.services.training import TrainingService

router = APIRouter()


def _get_service() -> TrainingService:
    return TrainingService(session_factory=db_module.async_session)


@router.get("/vault/training/jobs", response_model=TrainingJobList)
async def list_training_jobs():
    """List all training jobs."""
    service = _get_service()
    return await service.list_jobs()


@router.post("/vault/training/jobs", response_model=TrainingJobResponse, status_code=201)
async def create_training_job(data: TrainingJobCreate):
    """Create a new training job."""
    service = _get_service()
    return await service.create_job(data)


@router.get("/vault/training/jobs/{job_id}", response_model=TrainingJobResponse)
async def get_training_job(job_id: str):
    """Get training job details."""
    service = _get_service()
    return await service.get_job(job_id)


@router.post("/vault/training/jobs/{job_id}/pause", response_model=TrainingJobResponse)
async def pause_training_job(job_id: str):
    """Pause a running training job."""
    service = _get_service()
    return await service.pause_job(job_id)


@router.post("/vault/training/jobs/{job_id}/resume", response_model=TrainingJobResponse)
async def resume_training_job(job_id: str):
    """Resume a paused training job."""
    service = _get_service()
    return await service.resume_job(job_id)


@router.post("/vault/training/jobs/{job_id}/cancel", response_model=TrainingJobResponse)
async def cancel_training_job(job_id: str):
    """Cancel a training job."""
    service = _get_service()
    return await service.cancel_job(job_id)


@router.delete("/vault/training/jobs/{job_id}", status_code=204)
async def delete_training_job(job_id: str):
    """Delete a training job record."""
    service = _get_service()
    await service.delete_job(job_id)
