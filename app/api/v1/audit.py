from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, StreamingResponse

from app.dependencies import require_admin
from app.schemas.audit import AuditLogEntry, AuditLogResponse, AuditStatsResponse
from app.services.audit import AuditService

router = APIRouter(dependencies=[Depends(require_admin)])


def _format_dt(dt) -> str | None:
    if dt is None:
        return None
    return dt.isoformat() + "Z"


@router.get("/vault/admin/audit")
async def query_audit_log(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: str | None = None,
    action: str | None = None,
    method: str | None = None,
    path: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    status_code: int | None = None,
) -> AuditLogResponse:
    service = AuditService()
    items, total = await service.query_audit_log(
        limit=limit,
        offset=offset,
        user=user,
        action=action,
        method=method,
        path=path,
        start_time=start_time,
        end_time=end_time,
        status_code=status_code,
    )
    return AuditLogResponse(
        items=[
            AuditLogEntry(
                id=r.id,
                timestamp=_format_dt(r.timestamp),
                action=r.action,
                method=r.method,
                path=r.path,
                user_key_prefix=r.user_key_prefix,
                model=r.model,
                status_code=r.status_code,
                latency_ms=r.latency_ms,
                tokens_input=r.tokens_input,
                tokens_output=r.tokens_output,
                details=r.details,
            )
            for r in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/vault/admin/audit/export")
async def export_audit_log(
    format: str = Query("json", pattern="^(json|csv)$"),
    user: str | None = None,
    action: str | None = None,
    method: str | None = None,
    path: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    status_code: int | None = None,
):
    service = AuditService()
    result = await service.export_audit_log(
        format=format,
        user=user,
        action=action,
        method=method,
        path=path,
        start_time=start_time,
        end_time=end_time,
        status_code=status_code,
    )

    if format == "csv":
        return StreamingResponse(
            iter([result]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
        )

    return JSONResponse(content=result)


@router.get("/vault/admin/audit/stats")
async def audit_stats(
    start_time: str | None = None,
    end_time: str | None = None,
) -> AuditStatsResponse:
    service = AuditService()
    stats = await service.get_audit_stats(
        start_time=start_time,
        end_time=end_time,
    )
    return AuditStatsResponse(**stats)
