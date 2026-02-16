import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import ApiKey
from app.core.security import generate_api_key, get_key_prefix, hash_api_key


@pytest_asyncio.fixture
async def insights_app(app_with_db):
    """Register insights and activity routers on the test app."""
    from app.api.v1.insights import router as insights_router
    from app.api.v1.activity import router as activity_router

    app_with_db.include_router(insights_router, tags=["Insights"])
    app_with_db.include_router(activity_router, tags=["Activity"])
    yield app_with_db


@pytest_asyncio.fixture
async def auth_client_insights(insights_app, db_engine):
    """Authenticated client with insights/activity routes registered."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="insights-test",
            scope="admin",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=insights_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def anon_client_insights(insights_app):
    """Unauthenticated client with insights/activity routes registered."""
    transport = ASGITransport(app=insights_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_insights_returns_empty_data(auth_client_insights):
    """With no audit log records, insights should return zeros/empty arrays."""
    resp = await auth_client_insights.get("/vault/insights")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_requests"] == 0
    assert data["total_tokens"] == 0
    assert data["avg_response_time"] == 0.0
    assert data["active_users"] == 0
    assert data["usage_history"] == []
    assert data["model_usage"] == []
    assert isinstance(data["response_time_distribution"], list)
    assert len(data["response_time_distribution"]) > 0


@pytest.mark.asyncio
async def test_insights_accepts_range_param(auth_client_insights):
    resp = await auth_client_insights.get("/vault/insights?range=24h")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_requests"] == 0


@pytest.mark.asyncio
async def test_insights_requires_auth(anon_client_insights):
    resp = await anon_client_insights.get("/vault/insights")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_activity_returns_empty_feed(auth_client_insights):
    """With no audit log records, activity feed should be empty."""
    resp = await auth_client_insights.get("/vault/activity")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_activity_accepts_limit_param(auth_client_insights):
    resp = await auth_client_insights.get("/vault/activity?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_activity_requires_auth(anon_client_insights):
    resp = await anon_client_insights.get("/vault/activity")
    assert resp.status_code == 401
