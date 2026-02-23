"""Eval job CRUD service — manages DB records for evaluation jobs."""

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.database import EvalJob, async_session as default_session_factory
from app.core.exceptions import NotFoundError, VaultError
from app.schemas.eval import (
    EvalCompareEntry,
    EvalCompareResponse,
    EvalConfig,
    EvalJobCreate,
    EvalJobList,
    EvalJobResponse,
    EvalMetricResult,
    EvalResults,
)

# Valid status transitions
VALID_TRANSITIONS = {
    "cancel": {"from": {"queued", "running"}, "to": "cancelled"},
}


def _row_to_response(row: EvalJob) -> EvalJobResponse:
    """Convert an EvalJob ORM row to an EvalJobResponse schema."""
    config = EvalConfig(**json.loads(row.config_json)) if row.config_json else EvalConfig()

    results = None
    if row.results_json:
        raw = json.loads(row.results_json)
        results = EvalResults(**raw)

    return EvalJobResponse(
        id=row.id,
        name=row.name,
        status=row.status,
        progress=row.progress,
        model_id=row.model_id,
        adapter_id=row.adapter_id,
        dataset_id=row.dataset_id,
        dataset_type=row.dataset_type,
        config=config,
        results=results,
        error=row.error,
        total_examples=row.total_examples,
        examples_completed=row.examples_completed,
        started_at=row.started_at.isoformat() + "Z" if row.started_at else None,
        completed_at=row.completed_at.isoformat() + "Z" if row.completed_at else None,
        created_at=row.created_at.isoformat() + "Z" if row.created_at else "",
    )


class EvalService:
    def __init__(self, session_factory: async_sessionmaker | None = None):
        self._session_factory = session_factory or default_session_factory

    async def create_job(self, data: EvalJobCreate) -> EvalJobResponse:
        """Create a new eval job in queued status."""
        row = EvalJob(
            id=str(uuid.uuid4()),
            name=data.name,
            status="queued",
            progress=0.0,
            model_id=data.model_id,
            adapter_id=data.adapter_id,
            dataset_id=data.dataset_id,
            dataset_type="builtin",
            config_json=json.dumps(data.config.model_dump()),
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_response(row)

    async def list_jobs(
        self,
        model_id: str | None = None,
        status: str | None = None,
    ) -> EvalJobList:
        """List eval jobs with optional filters, ordered by created_at descending."""
        async with self._session_factory() as session:
            stmt = select(EvalJob).order_by(EvalJob.created_at.desc())
            if model_id:
                stmt = stmt.where(EvalJob.model_id == model_id)
            if status:
                stmt = stmt.where(EvalJob.status == status)
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            return EvalJobList(
                jobs=[_row_to_response(r) for r in rows],
                total=len(rows),
            )

    async def get_job(self, job_id: str) -> EvalJobResponse:
        """Get a single eval job by ID."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(EvalJob).where(EvalJob.id == job_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Eval job '{job_id}' not found.")
            return _row_to_response(row)

    async def cancel_job(self, job_id: str) -> EvalJobResponse:
        """Cancel a queued or running eval job."""
        rule = VALID_TRANSITIONS["cancel"]
        async with self._session_factory() as session:
            result = await session.execute(
                select(EvalJob).where(EvalJob.id == job_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Eval job '{job_id}' not found.")

            if row.status not in rule["from"]:
                raise VaultError(
                    code="invalid_status_transition",
                    message=f"Cannot cancel a job with status '{row.status}'.",
                    status=409,
                )

            row.status = rule["to"]
            row.completed_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(row)
            return _row_to_response(row)

    async def delete_job(self, job_id: str) -> None:
        """Delete an eval job record."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(EvalJob).where(EvalJob.id == job_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Eval job '{job_id}' not found.")
            await session.delete(row)
            await session.commit()

    async def update_job_status(
        self,
        job_id: str,
        *,
        status: str | None = None,
        progress: float | None = None,
        results_json: str | None = None,
        error: str | None = None,
        total_examples: int | None = None,
        examples_completed: int | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> EvalJobResponse:
        """Update eval job fields from the runner (progress, results, error, etc.)."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(EvalJob).where(EvalJob.id == job_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"Eval job '{job_id}' not found.")

            if status is not None:
                row.status = status
            if progress is not None:
                row.progress = progress
            if results_json is not None:
                row.results_json = results_json
            if error is not None:
                row.error = error
            if total_examples is not None:
                row.total_examples = total_examples
            if examples_completed is not None:
                row.examples_completed = examples_completed
            if started_at is not None:
                row.started_at = started_at
            if completed_at is not None:
                row.completed_at = completed_at

            await session.commit()
            await session.refresh(row)
            return _row_to_response(row)

    async def compare_jobs(self, job_ids: list[str]) -> EvalCompareResponse:
        """Compare 2+ completed eval jobs on the same dataset."""
        if len(job_ids) < 2:
            raise VaultError(
                code="invalid_request",
                message="At least 2 job IDs are required for comparison.",
                status=400,
            )

        async with self._session_factory() as session:
            result = await session.execute(
                select(EvalJob).where(EvalJob.id.in_(job_ids))
            )
            rows = list(result.scalars().all())

        if len(rows) != len(job_ids):
            found = {r.id for r in rows}
            missing = [jid for jid in job_ids if jid not in found]
            raise NotFoundError(f"Eval jobs not found: {', '.join(missing)}")

        # All must be completed
        non_completed = [r.id for r in rows if r.status != "completed"]
        if non_completed:
            raise VaultError(
                code="invalid_request",
                message=f"All jobs must be completed. Not completed: {', '.join(non_completed)}",
                status=400,
            )

        # All must use the same dataset
        datasets = {r.dataset_id for r in rows}
        if len(datasets) > 1:
            raise VaultError(
                code="invalid_request",
                message=f"All jobs must use the same dataset. Found: {', '.join(datasets)}",
                status=400,
            )

        entries = []
        for row in rows:
            metrics = []
            if row.results_json:
                raw = json.loads(row.results_json)
                for m in raw.get("metrics", []):
                    metrics.append(EvalMetricResult(**m))

            label = row.name
            if row.adapter_id:
                label = f"{row.name} (adapter: {row.adapter_id[:8]})"

            entries.append(EvalCompareEntry(
                job_id=row.id,
                model_id=row.model_id,
                adapter_id=row.adapter_id,
                label=label,
                metrics=metrics,
            ))

        return EvalCompareResponse(
            dataset_id=rows[0].dataset_id,
            models=entries,
        )
