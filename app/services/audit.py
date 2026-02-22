import csv
import io
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.core.database as db_module
from app.core.database import AuditLog


class AuditService:
    def __init__(self, session_factory=None):
        self._session_factory = session_factory or db_module.async_session

    async def query_audit_log(
        self,
        limit=50,
        offset=0,
        user=None,
        action=None,
        method=None,
        path=None,
        start_time=None,
        end_time=None,
        status_code=None,
    ) -> tuple[list[AuditLog], int]:
        """Query audit log with filters. Returns (items, total_count)."""
        async with self._session_factory() as session:
            base = select(AuditLog)

            if user is not None:
                base = base.where(AuditLog.user_key_prefix == user)
            if action is not None:
                base = base.where(AuditLog.action == action)
            if method is not None:
                base = base.where(AuditLog.method == method)
            if path is not None:
                base = base.where(AuditLog.path.contains(path))
            if start_time is not None:
                dt = datetime.fromisoformat(start_time)
                base = base.where(AuditLog.timestamp >= dt)
            if end_time is not None:
                dt = datetime.fromisoformat(end_time)
                base = base.where(AuditLog.timestamp <= dt)
            if status_code is not None:
                base = base.where(AuditLog.status_code == status_code)

            # Total count (without pagination)
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await session.execute(count_stmt)).scalar() or 0

            # Paginated results
            stmt = base.order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit)
            rows = (await session.execute(stmt)).scalars().all()

            return list(rows), total

    async def export_audit_log(self, format="json", **filters) -> str | list[dict]:
        """Export audit log as JSON list or CSV string."""
        rows, _ = await self.query_audit_log(limit=10000, offset=0, **filters)

        entries = [
            {
                "id": r.id,
                "timestamp": r.timestamp.isoformat() + "Z" if r.timestamp else None,
                "action": r.action,
                "method": r.method,
                "path": r.path,
                "user_key_prefix": r.user_key_prefix,
                "model": r.model,
                "status_code": r.status_code,
                "latency_ms": r.latency_ms,
                "tokens_input": r.tokens_input,
                "tokens_output": r.tokens_output,
                "details": r.details,
            }
            for r in rows
        ]

        if format == "csv":
            output = io.StringIO()
            if entries:
                writer = csv.DictWriter(output, fieldnames=entries[0].keys())
                writer.writeheader()
                writer.writerows(entries)
            return output.getvalue()

        return entries

    async def get_audit_stats(self, start_time=None, end_time=None) -> dict:
        """Aggregate stats: requests per user, tokens consumed, model usage, endpoint usage."""
        async with self._session_factory() as session:
            base = select(AuditLog)

            if start_time is not None:
                dt = datetime.fromisoformat(start_time)
                base = base.where(AuditLog.timestamp >= dt)
            if end_time is not None:
                dt = datetime.fromisoformat(end_time)
                base = base.where(AuditLog.timestamp <= dt)

            rows = (await session.execute(base)).scalars().all()

        total_requests = len(rows)
        total_tokens = sum(
            (r.tokens_input or 0) + (r.tokens_output or 0) for r in rows
        )
        avg_latency_ms = (
            round(sum(r.latency_ms or 0 for r in rows) / total_requests, 2)
            if total_requests > 0
            else 0.0
        )

        # Requests by user
        by_user: dict[str, int] = {}
        for r in rows:
            key = r.user_key_prefix or "anonymous"
            by_user[key] = by_user.get(key, 0) + 1
        requests_by_user = [
            {"user": u, "count": c}
            for u, c in sorted(by_user.items(), key=lambda x: -x[1])
        ]

        # Requests by model
        by_model: dict[str, int] = {}
        for r in rows:
            if r.model:
                by_model[r.model] = by_model.get(r.model, 0) + 1
        requests_by_model = [
            {"model": m, "count": c}
            for m, c in sorted(by_model.items(), key=lambda x: -x[1])
        ]

        # Requests by endpoint
        by_endpoint: dict[str, int] = {}
        for r in rows:
            if r.path:
                by_endpoint[r.path] = by_endpoint.get(r.path, 0) + 1
        requests_by_endpoint = [
            {"path": p, "count": c}
            for p, c in sorted(by_endpoint.items(), key=lambda x: -x[1])
        ]

        return {
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "avg_latency_ms": avg_latency_ms,
            "requests_by_user": requests_by_user,
            "requests_by_model": requests_by_model,
            "requests_by_endpoint": requests_by_endpoint,
        }
