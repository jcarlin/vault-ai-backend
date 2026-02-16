import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.database import TrainingJob, async_session as default_session_factory
from app.core.exceptions import NotFoundError, VaultError
from app.schemas.training import (
    ResourceAllocation,
    TrainingConfig,
    TrainingJobCreate,
    TrainingJobList,
    TrainingJobResponse,
    TrainingMetrics,
)

# Valid status transitions
VALID_TRANSITIONS = {
    "pause": {"from": {"running"}, "to": "paused"},
    "resume": {"from": {"paused"}, "to": "running"},
    "cancel": {"from": {"queued", "running", "paused"}, "to": "cancelled"},
}


def _row_to_response(row: TrainingJob) -> TrainingJobResponse:
    """Convert a TrainingJob ORM row to a TrainingJobResponse schema."""
    config = TrainingConfig(**json.loads(row.config_json)) if row.config_json else TrainingConfig()
    metrics = TrainingMetrics(**json.loads(row.metrics_json)) if row.metrics_json else TrainingMetrics()
    resource = ResourceAllocation(**json.loads(row.resource_json)) if row.resource_json else ResourceAllocation()

    return TrainingJobResponse(
        id=row.id,
        name=row.name,
        status=row.status,
        progress=row.progress,
        model=row.model,
        dataset=row.dataset,
        config=config,
        metrics=metrics,
        resource_allocation=resource,
        error=row.error,
        started_at=row.started_at.isoformat() + "Z" if row.started_at else None,
        completed_at=row.completed_at.isoformat() + "Z" if row.completed_at else None,
        created_at=row.created_at.isoformat() + "Z" if row.created_at else "",
    )


class TrainingService:
    def __init__(self, session_factory: async_sessionmaker | None = None):
        self._session_factory = session_factory or default_session_factory

    async def list_jobs(self) -> TrainingJobList:
        """List all training jobs, sorted by created_at descending."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(TrainingJob).order_by(TrainingJob.created_at.desc())
            )
            rows = list(result.scalars().all())
            return TrainingJobList(
                jobs=[_row_to_response(r) for r in rows],
                total=len(rows),
            )

    async def create_job(self, data: TrainingJobCreate) -> TrainingJobResponse:
        """Create a new training job in queued status."""
        row = TrainingJob(
            id=str(uuid.uuid4()),
            name=data.name,
            status="queued",
            progress=0.0,
            model=data.model,
            dataset=data.dataset,
            config_json=json.dumps(data.config.model_dump()),
            resource_json=json.dumps(data.resource_allocation.model_dump()),
            metrics_json=json.dumps(TrainingMetrics().model_dump()),
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_response(row)

    async def get_job(self, job_id: str) -> TrainingJobResponse:
        """Get a single training job by ID."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(TrainingJob).where(TrainingJob.id == job_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Training job '{job_id}' not found.")
            return _row_to_response(row)

    async def _transition(self, job_id: str, action: str) -> TrainingJobResponse:
        """Perform a status transition on a training job."""
        rule = VALID_TRANSITIONS[action]
        async with self._session_factory() as session:
            result = await session.execute(
                select(TrainingJob).where(TrainingJob.id == job_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Training job '{job_id}' not found.")

            if row.status not in rule["from"]:
                raise VaultError(
                    code="invalid_status_transition",
                    message=f"Cannot {action} a job with status '{row.status}'.",
                    status=409,
                )

            row.status = rule["to"]

            if action == "resume":
                row.started_at = row.started_at or datetime.now(timezone.utc)
            elif action == "cancel":
                row.completed_at = datetime.now(timezone.utc)

            await session.commit()
            await session.refresh(row)
            return _row_to_response(row)

    async def pause_job(self, job_id: str) -> TrainingJobResponse:
        return await self._transition(job_id, "pause")

    async def resume_job(self, job_id: str) -> TrainingJobResponse:
        return await self._transition(job_id, "resume")

    async def cancel_job(self, job_id: str) -> TrainingJobResponse:
        return await self._transition(job_id, "cancel")

    async def delete_job(self, job_id: str) -> None:
        """Delete a training job record."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(TrainingJob).where(TrainingJob.id == job_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Training job '{job_id}' not found.")
            await session.delete(row)
            await session.commit()
