import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.auth import AuthService
from app.core.database import ApiKey
from app.core.security import hash_api_key


@pytest_asyncio.fixture
async def auth_service(db_engine):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    return AuthService(session_factory=session_factory)


@pytest.mark.asyncio
async def test_create_key_returns_raw_key_and_row(auth_service):
    raw_key, key_row = await auth_service.create_key(label="test-key")
    assert raw_key.startswith("vault_sk_")
    assert len(raw_key) == 57  # vault_sk_ (9) + 48 hex chars
    assert key_row.label == "test-key"
    assert key_row.scope == "user"
    assert key_row.is_active is True
    assert key_row.id is not None


@pytest.mark.asyncio
async def test_create_key_admin_scope(auth_service):
    raw_key, key_row = await auth_service.create_key(label="admin-key", scope="admin")
    assert key_row.scope == "admin"
    assert key_row.label == "admin-key"


@pytest.mark.asyncio
async def test_list_keys_returns_created_keys(auth_service):
    await auth_service.create_key(label="key-a")
    await auth_service.create_key(label="key-b")
    keys = await auth_service.list_keys()
    labels = [k.label for k in keys]
    assert "key-a" in labels
    assert "key-b" in labels


@pytest.mark.asyncio
async def test_list_keys_excludes_revoked(auth_service):
    raw_key, key_row = await auth_service.create_key(label="will-revoke")
    await auth_service.revoke_key(key_row.key_prefix)
    keys = await auth_service.list_keys()
    labels = [k.label for k in keys]
    assert "will-revoke" not in labels


@pytest.mark.asyncio
async def test_revoke_key_by_prefix(auth_service):
    raw_key, key_row = await auth_service.create_key(label="prefix-revoke")
    result = await auth_service.revoke_key(key_row.key_prefix)
    assert result is True
    # Verify the key is no longer valid
    validated = await auth_service.validate_key(raw_key)
    assert validated is None


@pytest.mark.asyncio
async def test_revoke_key_by_full_key(auth_service):
    raw_key, key_row = await auth_service.create_key(label="full-revoke")
    result = await auth_service.revoke_key(raw_key)
    assert result is True
    validated = await auth_service.validate_key(raw_key)
    assert validated is None


@pytest.mark.asyncio
async def test_revoke_unknown_key_returns_false(auth_service):
    result = await auth_service.revoke_key("vault_sk_xx")
    assert result is False


@pytest.mark.asyncio
async def test_validate_key_valid(auth_service):
    raw_key, key_row = await auth_service.create_key(label="valid-key")
    validated = await auth_service.validate_key(raw_key)
    assert validated is not None
    assert validated.label == "valid-key"
    assert validated.is_active is True


@pytest.mark.asyncio
async def test_validate_key_invalid_returns_none(auth_service):
    result = await auth_service.validate_key("vault_sk_0000000000000000000000000000000000000000000000aa")
    assert result is None
