from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
import io

from app.core.exceptions import VaultError
from app.dependencies import require_admin
from app.schemas.diagnostics import (
    ArchiveRequest,
    ArchiveResponse,
    BackupRequest,
    BackupResponse,
    DataExportResponse,
    DataPurgeRequest,
    DataPurgeResponse,
    FactoryResetRequest,
    FactoryResetResponse,
    RestoreRequest,
    RestoreResponse,
)
from app.services.diagnostics import DiagnosticsService

router = APIRouter(dependencies=[Depends(require_admin)])


# ── 11.5: Data Export ────────────────────────────────────────────────────────


@router.get("/vault/admin/data/export")
async def export_data() -> DataExportResponse:
    service = DiagnosticsService()
    data = await service.export_data()
    return DataExportResponse(**data)


# ── 11.6: Data Purge ────────────────────────────────────────────────────────


@router.post("/vault/admin/data/purge")
async def purge_data(body: DataPurgeRequest) -> DataPurgeResponse:
    if body.confirmation != "DELETE ALL DATA":
        raise VaultError(
            code="confirmation_required",
            message='You must provide confirmation: "DELETE ALL DATA"',
            status=400,
        )
    service = DiagnosticsService()
    result = await service.purge_data(include_api_keys=body.include_api_keys)
    return DataPurgeResponse(**result)


# ── 11.7: Chat Archive ──────────────────────────────────────────────────────


@router.post("/vault/admin/conversations/archive")
async def archive_conversations(body: ArchiveRequest) -> ArchiveResponse:
    service = DiagnosticsService()
    result = await service.archive_conversations(before=body.before)
    return ArchiveResponse(**result)


# ── 11.4: Factory Reset ─────────────────────────────────────────────────────


@router.post("/vault/admin/factory-reset")
async def factory_reset(body: FactoryResetRequest, request: Request) -> FactoryResetResponse:
    if body.confirmation != "FACTORY RESET":
        raise VaultError(
            code="confirmation_required",
            message='You must provide confirmation: "FACTORY RESET"',
            status=400,
        )
    service = DiagnosticsService()
    result = await service.factory_reset(app_state=request.app.state)
    return FactoryResetResponse(**result)


# ── 11.1: Support Bundle ────────────────────────────────────────────────────


@router.post("/vault/admin/diagnostics/bundle")
async def generate_support_bundle() -> StreamingResponse:
    service = DiagnosticsService()
    bundle_bytes = await service.generate_bundle()
    return StreamingResponse(
        io.BytesIO(bundle_bytes),
        media_type="application/gzip",
        headers={
            "Content-Disposition": "attachment; filename=vault-support-bundle.tar.gz",
            "Content-Length": str(len(bundle_bytes)),
        },
    )


# ── 11.2: Backup ────────────────────────────────────────────────────────────


@router.post("/vault/admin/backup")
async def create_backup(body: BackupRequest) -> BackupResponse:
    service = DiagnosticsService()
    result = await service.create_backup(
        output_path=body.output_path,
        passphrase=body.passphrase,
    )
    return BackupResponse(**result)


# ── 11.3: Restore ───────────────────────────────────────────────────────────


@router.post("/vault/admin/restore")
async def restore_backup(body: RestoreRequest) -> RestoreResponse:
    service = DiagnosticsService()
    result = await service.restore_backup(
        backup_path=body.backup_path,
        passphrase=body.passphrase,
    )
    return RestoreResponse(**result)
