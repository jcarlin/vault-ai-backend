from fastapi import APIRouter, Query
from sqlalchemy import func as sa_func, select

import app.core.database as db_module
from app.core.database import AuditLog
from app.schemas.activity import ActivityFeed, ActivityItem

router = APIRouter()


def _classify_action(row: AuditLog) -> str:
    """Map audit log action to activity type."""
    if row.path and "chat" in row.path:
        return "inference"
    return "system"


def _build_title(row: AuditLog) -> str:
    if row.path and "chat" in row.path:
        return "Chat completion request"
    if row.path and "models" in row.path:
        return "Model list query"
    if row.path and "health" in row.path:
        return "Health check"
    return f"{row.method or ''} {row.path or 'unknown'}".strip()


def _build_description(row: AuditLog) -> str:
    parts = []
    if row.model:
        parts.append(f"model={row.model}")
    if row.status_code:
        parts.append(f"status={row.status_code}")
    if row.latency_ms is not None:
        parts.append(f"{row.latency_ms}ms")
    return ", ".join(parts) if parts else "No details"


@router.get("/vault/activity")
async def activity(
    limit: int = Query(default=20, ge=1, le=100),
) -> ActivityFeed:
    """Recent activity feed from the audit log."""
    async with db_module.async_session() as session:
        stmt = (
            select(AuditLog)
            .order_by(AuditLog.timestamp.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()

        count_result = await session.execute(
            select(sa_func.count()).select_from(AuditLog)
        )
        total = count_result.scalar() or 0

    items = [
        ActivityItem(
            id=str(row.id),
            type=_classify_action(row),
            title=_build_title(row),
            description=_build_description(row),
            timestamp=row.timestamp.isoformat() if row.timestamp else "",
            user=row.user_key_prefix,
        )
        for row in rows
    ]

    return ActivityFeed(items=items, total=total)
