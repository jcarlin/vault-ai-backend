import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.testclient import TestClient

from app.core.database import ApiKey
from app.core.security import generate_api_key, get_key_prefix, hash_api_key


@pytest_asyncio.fixture
async def ws_app(app_with_db):
    """Register WebSocket router on the test app."""
    from app.api.v1.websocket import router as ws_router

    app_with_db.include_router(ws_router, tags=["WebSocket"])
    yield app_with_db


@pytest_asyncio.fixture
async def ws_api_key(ws_app, db_engine):
    """Create an API key for WebSocket auth and return the raw key."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="ws-test",
            scope="user",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()
    return raw_key


class TestWebSocket:
    @pytest.mark.asyncio
    async def test_invalid_token_rejected(self, ws_app):
        """WebSocket connection with an invalid token should be closed."""
        client = TestClient(ws_app)
        with pytest.raises(Exception):
            # Invalid token should cause WebSocket close with code 4001
            with client.websocket_connect("/ws/system?token=invalid"):
                pass

    @pytest.mark.asyncio
    async def test_missing_token_rejected(self, ws_app):
        """WebSocket connection without a token should be closed."""
        client = TestClient(ws_app)
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/system"):
                pass

    @pytest.mark.asyncio
    async def test_valid_token_receives_metrics(self, ws_app, ws_api_key):
        """WebSocket connection with a valid token should receive metrics."""
        client = TestClient(ws_app)
        with client.websocket_connect(f"/ws/system?token={ws_api_key}") as ws:
            data = ws.receive_json()
            assert "timestamp" in data
            assert "resources" in data
            assert "gpus" in data

            # Verify resources structure
            resources = data["resources"]
            assert "cpu_usage_pct" in resources
            assert "ram_total_mb" in resources

            # gpus should be a list
            assert isinstance(data["gpus"], list)
