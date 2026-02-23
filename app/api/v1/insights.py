from datetime import datetime, timedelta, timezone
from enum import Enum

from fastapi import APIRouter, Query
from sqlalchemy import func, select

import app.core.database as db_module
from app.core.database import AuditLog
from app.schemas.insights import (
    InsightsResponse,
    ModelUsageStats,
    ResponseTimeDistribution,
    UsageDataPoint,
)

router = APIRouter()

LATENCY_BUCKETS = [
    ("<100ms", 0, 100),
    ("100-500ms", 100, 500),
    ("500ms-1s", 500, 1000),
    ("1-2s", 1000, 2000),
    ("2-5s", 2000, 5000),
    (">5s", 5000, None),
]


class TimeRange(str, Enum):
    day = "24h"
    week = "7d"
    month = "30d"
    quarter = "90d"


def _range_to_timedelta(r: TimeRange) -> timedelta:
    mapping = {
        TimeRange.day: timedelta(hours=24),
        TimeRange.week: timedelta(days=7),
        TimeRange.month: timedelta(days=30),
        TimeRange.quarter: timedelta(days=90),
    }
    return mapping[r]


@router.get("/vault/insights")
async def insights(
    range: TimeRange = Query(default=TimeRange.week, alias="range"),
) -> InsightsResponse:
    """Aggregated usage analytics from the audit log."""
    cutoff = datetime.utcnow() - _range_to_timedelta(range)

    async with db_module.async_session() as session:
        base = select(AuditLog).where(
            AuditLog.action == "http_request",
            AuditLog.timestamp >= cutoff,
        )

        rows = (await session.execute(base)).scalars().all()

    if not rows:
        return InsightsResponse(
            usage_history=[],
            response_time_distribution=[
                ResponseTimeDistribution(range=label, count=0)
                for label, _, _ in LATENCY_BUCKETS
            ],
            model_usage=[],
            total_requests=0,
            total_tokens=0,
            avg_response_time=0.0,
            active_users=0,
        )

    # Totals
    total_requests = len(rows)
    total_tokens = sum((r.tokens_input or 0) + (r.tokens_output or 0) for r in rows)
    avg_response_time = sum(r.latency_ms or 0 for r in rows) / total_requests
    active_users = len({r.user_key_prefix for r in rows if r.user_key_prefix})

    # Usage history grouped by date
    by_date: dict[str, dict] = {}
    for r in rows:
        date_str = r.timestamp.strftime("%Y-%m-%d")
        if date_str not in by_date:
            by_date[date_str] = {"requests": 0, "tokens": 0}
        by_date[date_str]["requests"] += 1
        by_date[date_str]["tokens"] += (r.tokens_input or 0) + (r.tokens_output or 0)
    usage_history = [
        UsageDataPoint(date=d, requests=v["requests"], tokens=v["tokens"])
        for d, v in sorted(by_date.items())
    ]

    # Response time distribution
    response_time_distribution = []
    for label, lo, hi in LATENCY_BUCKETS:
        count = sum(
            1
            for r in rows
            if r.latency_ms is not None
            and r.latency_ms >= lo
            and (hi is None or r.latency_ms < hi)
        )
        response_time_distribution.append(
            ResponseTimeDistribution(range=label, count=count)
        )

    # Model usage
    model_counts: dict[str, int] = {}
    for r in rows:
        if r.model:
            model_counts[r.model] = model_counts.get(r.model, 0) + 1
    model_usage = [
        ModelUsageStats(
            model=m,
            requests=c,
            percentage=round(c / total_requests * 100, 1),
        )
        for m, c in sorted(model_counts.items(), key=lambda x: -x[1])
    ]

    return InsightsResponse(
        usage_history=usage_history,
        response_time_distribution=response_time_distribution,
        model_usage=model_usage,
        total_requests=total_requests,
        total_tokens=total_tokens,
        avg_response_time=round(avg_response_time, 1),
        active_users=active_users,
    )
