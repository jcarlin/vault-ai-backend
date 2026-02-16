import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import ApiKey, Base
from app.core.security import generate_api_key, hash_api_key, get_key_prefix


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_engine():
    """In-memory SQLite engine for tests."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Async session bound to the in-memory engine."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def test_api_key(db_session):
    """Create a test API key and return (raw_key, key_row)."""
    raw_key = generate_api_key()
    key_row = ApiKey(
        key_hash=hash_api_key(raw_key),
        key_prefix=get_key_prefix(raw_key),
        label="test-key",
        scope="admin",
        is_active=True,
    )
    db_session.add(key_row)
    await db_session.commit()
    await db_session.refresh(key_row)
    return raw_key, key_row


@pytest_asyncio.fixture
async def app_with_db(db_engine):
    """FastAPI app wired to the in-memory test database and fake vLLM backend."""
    import app.core.database as db_module
    import app.core.middleware as mw_module

    # Patch the module-level engine and session factory
    original_engine = db_module.engine
    original_session = db_module.async_session
    original_mw_session = mw_module.async_session

    test_session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    db_module.engine = db_engine
    db_module.async_session = test_session_factory

    # Also patch the middleware's imported async_session
    mw_module.async_session = test_session_factory

    from app.main import app

    # Wire inference backend to fake vLLM via in-process ASGITransport
    from tests.mocks.fake_vllm import app as fake_vllm_app
    from app.services.inference.vllm_client import VLLMBackend

    fake_transport = ASGITransport(app=fake_vllm_app)
    fake_http_client = AsyncClient(transport=fake_transport, base_url="http://fake-vllm")
    backend = VLLMBackend(base_url="http://fake-vllm", http_client=fake_http_client)
    app.state.inference_backend = backend

    yield app

    await fake_http_client.aclose()
    db_module.engine = original_engine
    db_module.async_session = original_session
    mw_module.async_session = original_mw_session


@pytest_asyncio.fixture
async def auth_client(app_with_db, db_engine):
    """Authenticated async HTTP client with a valid API key."""
    # Create a key directly in the DB
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="integration-test",
            scope="admin",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def anon_client(app_with_db):
    """Unauthenticated async HTTP client."""
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
