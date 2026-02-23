"""Integration tests for eval WebSocket endpoint."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import ApiKey
from app.core.security import generate_api_key, get_key_prefix, hash_api_key


@pytest.mark.asyncio
async def test_eval_ws_requires_auth(app_with_db):
    """WS /ws/eval/{job_id} — rejects without token."""
    from starlette.testclient import TestClient

    with TestClient(app_with_db) as client:
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/eval/test-job"):
                pass


@pytest.mark.asyncio
async def test_eval_ws_invalid_token(app_with_db):
    """WS /ws/eval/{job_id} — rejects with invalid token."""
    from starlette.testclient import TestClient

    with TestClient(app_with_db) as client:
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/eval/test-job?token=invalid"):
                pass


@pytest.mark.asyncio
async def test_eval_ws_unknown_job(app_with_db, db_engine):
    """WS /ws/eval/{job_id} — sends waiting for unknown job (no status data)."""
    from starlette.testclient import TestClient

    # Create a valid API key
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="ws-test",
            scope="admin",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    with TestClient(app_with_db) as client:
        with client.websocket_connect(f"/ws/eval/nonexistent-job?token={raw_key}") as ws:
            data = ws.receive_json()
            # No status data for this job, so we get "waiting"
            assert data["type"] == "waiting"


@pytest.mark.asyncio
async def test_eval_ws_waiting_state(app_with_db, db_engine):
    """WS /ws/eval/{job_id} — sends waiting when no status data."""
    from unittest.mock import MagicMock
    from starlette.testclient import TestClient

    # Create a valid API key
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="ws-test",
            scope="admin",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    # Mock eval runner that returns no status
    mock_runner = MagicMock()
    mock_runner.get_latest_status.return_value = None
    app_with_db.state.eval_runner = mock_runner

    with TestClient(app_with_db) as client:
        with client.websocket_connect(f"/ws/eval/test-job?token={raw_key}") as ws:
            data = ws.receive_json()
            assert data["type"] == "waiting"
