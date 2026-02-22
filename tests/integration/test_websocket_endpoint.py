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


@pytest_asyncio.fixture
async def ws_admin_key(ws_app, db_engine):
    """Create an admin-scope API key for WebSocket auth and return the raw key."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="ws-admin-test",
            scope="admin",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()
    return raw_key


class TestWebSocketSystem:
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


class TestWebSocketLogs:
    @pytest.mark.asyncio
    async def test_invalid_token_rejected(self, ws_app):
        """WebSocket /ws/logs with invalid token should close with 4001."""
        client = TestClient(ws_app)
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/logs?token=invalid"):
                pass

    @pytest.mark.asyncio
    async def test_missing_token_rejected(self, ws_app):
        """WebSocket /ws/logs without a token should close with 4001."""
        client = TestClient(ws_app)
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/logs"):
                pass

    @pytest.mark.asyncio
    async def test_user_scope_rejected(self, ws_app, ws_api_key):
        """WebSocket /ws/logs with user-scope token should close with 4003."""
        client = TestClient(ws_app)
        with pytest.raises(Exception):
            with client.websocket_connect(f"/ws/logs?token={ws_api_key}"):
                pass

    @pytest.mark.asyncio
    async def test_admin_non_linux_receives_info(self, ws_app, ws_admin_key):
        """On non-Linux, admin should receive an info message."""
        import platform

        if platform.system() == "Linux":
            pytest.skip("Test is for non-Linux platforms")

        client = TestClient(ws_app)
        with client.websocket_connect(f"/ws/logs?token={ws_admin_key}") as ws:
            data = ws.receive_json()
            assert data["type"] == "info"
            assert "unavailable" in data["message"].lower() or "not running" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_admin_non_linux_no_crash(self, ws_app, ws_admin_key):
        """On non-Linux, connection should complete without crashing."""
        import platform

        if platform.system() == "Linux":
            pytest.skip("Test is for non-Linux platforms")

        client = TestClient(ws_app)
        # Should not raise — connection accepted, info sent, then closed cleanly
        with client.websocket_connect(f"/ws/logs?token={ws_admin_key}") as ws:
            data = ws.receive_json()
            assert data is not None
