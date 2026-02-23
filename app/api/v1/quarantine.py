"""Quarantine pipeline API endpoints (Epic 9)."""

from fastapi import APIRouter, Depends, Request, UploadFile, File, Query

from app.dependencies import require_admin
from app.schemas.quarantine import (
    FileStatus,
    HeldFilesResponse,
    QuarantineConfig,
    QuarantineConfigUpdate,
    QuarantineStatsResponse,
    ReviewRequest,
    ScanJobStatus,
    ScanPathRequest,
    ScanSubmitResponse,
    SignaturesResponse,
)
from app.services.quarantine.orchestrator import QuarantinePipeline

router = APIRouter()


def _get_pipeline(request: Request) -> QuarantinePipeline:
    pipeline = getattr(request.app.state, "quarantine_pipeline", None)
    if pipeline is None:
        from app.core.exceptions import VaultError
        raise VaultError(
            code="quarantine_unavailable",
            message="Quarantine pipeline is not available on this system.",
            status=503,
            details={"suggestion": "This feature requires ClamAV, YARA, and the quarantine filesystem (Cube only)."},
        )
    return pipeline


# ── POST /vault/quarantine/scan — Submit files for scanning ──────────────


@router.post("/vault/quarantine/scan", status_code=202)
async def submit_scan(
    request: Request,
    files: list[UploadFile] = File(default=None),
    body: ScanPathRequest | None = None,
) -> ScanSubmitResponse:
    """Submit files for quarantine scanning (multipart upload or path)."""
    pipeline = _get_pipeline(request)
    submitted_by = getattr(request.state, "api_key_prefix", None)

    if files:
        # Multipart file upload
        file_data = []
        for f in files:
            content = await f.read()
            file_data.append((f.filename or "unknown", content))
        job_id = await pipeline.submit_scan(file_data, source_type="upload", submitted_by=submitted_by)
        return ScanSubmitResponse(job_id=job_id, total_files=len(file_data))
    elif body and body.path:
        # Path-based scan (USB, model import)
        job_id = await pipeline.submit_scan_path(body.path, source_type="usb_path", submitted_by=submitted_by)
        return ScanSubmitResponse(job_id=job_id, total_files=0, message="Path scan submitted")
    else:
        from app.core.exceptions import VaultError
        raise VaultError(code="validation_error", message="Provide files or a path to scan.", status=400)


# ── GET /vault/quarantine/scan/{job_id} — Scan progress ─────────────────


@router.get("/vault/quarantine/scan/{job_id}")
async def get_scan_status(job_id: str, request: Request) -> ScanJobStatus:
    """Get scan job progress and per-file status."""
    pipeline = _get_pipeline(request)
    data = await pipeline.get_job_status(job_id)
    return ScanJobStatus(**data)


# ── GET /vault/quarantine/held — List held files ─────────────────────────


@router.get("/vault/quarantine/held", dependencies=[Depends(require_admin)])
async def list_held_files(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> HeldFilesResponse:
    """List files flagged and awaiting admin review."""
    pipeline = _get_pipeline(request)
    data = await pipeline.list_held_files(offset=offset, limit=limit)
    return HeldFilesResponse(**data)


# ── GET /vault/quarantine/held/{id} — Held file details ──────────────────


@router.get("/vault/quarantine/held/{file_id}", dependencies=[Depends(require_admin)])
async def get_held_file(file_id: str, request: Request) -> FileStatus:
    """Get detailed info for a single held file."""
    pipeline = _get_pipeline(request)
    data = await pipeline.get_held_file(file_id)
    return FileStatus(**data)


# ── POST /vault/quarantine/held/{id}/approve — Approve held file ─────────


@router.post("/vault/quarantine/held/{file_id}/approve", dependencies=[Depends(require_admin)])
async def approve_held_file(
    file_id: str, body: ReviewRequest, request: Request
) -> FileStatus:
    """Approve a held file — moves to production storage."""
    pipeline = _get_pipeline(request)
    reviewed_by = getattr(request.state, "api_key_prefix", None)
    data = await pipeline.approve_file(file_id, reason=body.reason, reviewed_by=reviewed_by)
    return FileStatus(**data)


# ── POST /vault/quarantine/held/{id}/reject — Reject held file ──────────


@router.post("/vault/quarantine/held/{file_id}/reject", dependencies=[Depends(require_admin)])
async def reject_held_file(
    file_id: str, body: ReviewRequest, request: Request
) -> FileStatus:
    """Reject a held file — deletes from quarantine."""
    pipeline = _get_pipeline(request)
    reviewed_by = getattr(request.state, "api_key_prefix", None)
    data = await pipeline.reject_file(file_id, reason=body.reason, reviewed_by=reviewed_by)
    return FileStatus(**data)


# ── GET /vault/quarantine/signatures — Signature freshness ───────────────


@router.get("/vault/quarantine/signatures", dependencies=[Depends(require_admin)])
async def get_signatures(request: Request) -> SignaturesResponse:
    """Get ClamAV/YARA signature versions and freshness."""
    pipeline = _get_pipeline(request)
    data = await pipeline.get_signature_info()
    return SignaturesResponse(**data)


# ── GET /vault/quarantine/stats — Aggregate statistics ───────────────────


@router.get("/vault/quarantine/stats", dependencies=[Depends(require_admin)])
async def get_quarantine_stats(request: Request) -> QuarantineStatsResponse:
    """Aggregate scan statistics."""
    pipeline = _get_pipeline(request)
    data = await pipeline.get_stats()
    return QuarantineStatsResponse(**data)


# ── PUT /vault/admin/config/quarantine — Configure quarantine ────────────


@router.get("/vault/admin/config/quarantine", dependencies=[Depends(require_admin)])
async def get_quarantine_config(request: Request) -> QuarantineConfig:
    """Get current quarantine configuration."""
    pipeline = _get_pipeline(request)
    data = await pipeline.get_config()
    return QuarantineConfig(**data)


@router.put("/vault/admin/config/quarantine", dependencies=[Depends(require_admin)])
async def update_quarantine_config(
    body: QuarantineConfigUpdate, request: Request
) -> QuarantineConfig:
    """Update quarantine configuration."""
    pipeline = _get_pipeline(request)
    updates = body.model_dump(exclude_none=True)
    data = await pipeline.update_config(updates)
    return QuarantineConfig(**data)
