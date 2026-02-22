"""Update mechanism endpoints — 7 admin-only routes for air-gapped update lifecycle."""

from fastapi import APIRouter, Depends, Query, Request

from app.dependencies import require_admin
from app.schemas.updates import (
    ApplyRequest,
    ApplyResponse,
    HistoryResponse,
    ProgressResponse,
    RollbackRequest,
    RollbackResponse,
    ScanResponse,
    UpdateStatusResponse,
)
from app.services.update.service import UpdateService

router = APIRouter()


def _get_service(request: Request) -> UpdateService:
    return request.app.state.update_service


# ── GET /vault/updates/status ────────────────────────────────────────────────


@router.get(
    "/vault/updates/status",
    dependencies=[Depends(require_admin)],
    response_model=UpdateStatusResponse,
)
async def get_update_status(request: Request) -> UpdateStatusResponse:
    """Current system version, last update info, rollback availability."""
    service = _get_service(request)
    data = await service.get_status()
    return UpdateStatusResponse(**data)


# ── POST /vault/updates/scan ─────────────────────────────────────────────────


@router.post(
    "/vault/updates/scan",
    dependencies=[Depends(require_admin)],
    response_model=ScanResponse,
)
async def scan_for_updates(request: Request) -> ScanResponse:
    """Scan mounted USB/external drives for update bundles."""
    service = _get_service(request)
    data = await service.scan_for_updates()
    return ScanResponse(**data)


# ── GET /vault/updates/pending ───────────────────────────────────────────────


@router.get(
    "/vault/updates/pending",
    dependencies=[Depends(require_admin)],
)
async def get_pending_update(request: Request):
    """Details of the most recently scanned (and validated) bundle."""
    service = _get_service(request)
    pending = await service.get_pending()
    if pending is None:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("No pending update. Run a scan first.")
    return pending


# ── POST /vault/updates/apply ────────────────────────────────────────────────


@router.post(
    "/vault/updates/apply",
    dependencies=[Depends(require_admin)],
    response_model=ApplyResponse,
    status_code=202,
)
async def apply_update(body: ApplyRequest, request: Request) -> ApplyResponse:
    """Start applying the pending update. Returns job ID for progress tracking."""
    service = _get_service(request)
    user_key_prefix = getattr(request.state, "api_key_prefix", None)
    data = await service.apply_update(
        confirmation=body.confirmation,
        create_backup=body.create_backup,
        backup_passphrase=body.backup_passphrase,
        user_key_prefix=user_key_prefix,
    )
    return ApplyResponse(**data)


# ── GET /vault/updates/progress/{job_id} ─────────────────────────────────────


@router.get(
    "/vault/updates/progress/{job_id}",
    dependencies=[Depends(require_admin)],
    response_model=ProgressResponse,
)
async def get_update_progress(job_id: str, request: Request) -> ProgressResponse:
    """Current progress of an apply/rollback job."""
    service = _get_service(request)
    data = await service.get_progress(job_id)
    return ProgressResponse(**data)


# ── POST /vault/updates/rollback ─────────────────────────────────────────────


@router.post(
    "/vault/updates/rollback",
    dependencies=[Depends(require_admin)],
    response_model=RollbackResponse,
    status_code=202,
)
async def rollback_update(body: RollbackRequest, request: Request) -> RollbackResponse:
    """Rollback to the previous version using stored rollback snapshot."""
    service = _get_service(request)
    user_key_prefix = getattr(request.state, "api_key_prefix", None)
    data = await service.rollback(
        confirmation=body.confirmation,
        user_key_prefix=user_key_prefix,
    )
    return RollbackResponse(**data)


# ── GET /vault/updates/history ───────────────────────────────────────────────


@router.get(
    "/vault/updates/history",
    dependencies=[Depends(require_admin)],
    response_model=HistoryResponse,
)
async def get_update_history(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> HistoryResponse:
    """Full update history from UpdateJob records."""
    service = _get_service(request)
    data = await service.get_history(offset=offset, limit=limit)
    return HistoryResponse(**data)
