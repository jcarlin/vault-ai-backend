import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import ApiKey
from app.core.security import generate_api_key, hash_api_key, get_key_prefix


@pytest_asyncio.fixture
async def admin_app(app_with_db):
    """App with admin router included."""
    from app.api.v1.admin import router as admin_router

    # Only add if not already registered
    existing = {r.path for r in app_with_db.routes}
    if "/vault/admin/users" not in existing:
        app_with_db.include_router(admin_router)

    yield app_with_db


@pytest_asyncio.fixture
async def auth_client(admin_app, db_engine):
    """Authenticated client hitting the admin-enabled app."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="admin-integration-test",
            scope="admin",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=admin_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def user_client(admin_app, db_engine):
    """Authenticated client with a user-scoped (non-admin) API key."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="user-scope-test",
            scope="user",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=admin_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def anon_client(admin_app):
    """Unauthenticated client hitting the admin-enabled app."""
    transport = ASGITransport(app=admin_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestUserEndpoints:
    async def test_create_user(self, auth_client):
        """POST /vault/admin/users creates a user and returns 201."""
        response = await auth_client.post(
            "/vault/admin/users",
            json={"name": "Alice Test", "email": "alice@vault.local", "role": "user"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Alice Test"
        assert data["email"] == "alice@vault.local"
        assert data["role"] == "user"
        assert data["status"] == "active"
        assert data["api_key_count"] == 0
        assert "id" in data

    async def test_list_users(self, auth_client):
        """GET /vault/admin/users returns the created user."""
        await auth_client.post(
            "/vault/admin/users",
            json={"name": "Bob List", "email": "bob-list@vault.local"},
        )

        response = await auth_client.get("/vault/admin/users")
        assert response.status_code == 200
        users = response.json()
        assert isinstance(users, list)
        assert any(u["email"] == "bob-list@vault.local" for u in users)

    async def test_update_user(self, auth_client):
        """PUT /vault/admin/users/{id} updates user fields."""
        create_resp = await auth_client.post(
            "/vault/admin/users",
            json={"name": "Charlie", "email": "charlie@vault.local"},
        )
        user_id = create_resp.json()["id"]

        response = await auth_client.put(
            f"/vault/admin/users/{user_id}",
            json={"name": "Charlie Updated", "role": "admin"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Charlie Updated"
        assert data["role"] == "admin"

    async def test_deactivate_user(self, auth_client):
        """DELETE /vault/admin/users/{id} sets status to inactive."""
        create_resp = await auth_client.post(
            "/vault/admin/users",
            json={"name": "Dave", "email": "dave@vault.local"},
        )
        user_id = create_resp.json()["id"]

        response = await auth_client.delete(f"/vault/admin/users/{user_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "inactive"

    async def test_duplicate_email_returns_409(self, auth_client):
        """POST /vault/admin/users with duplicate email returns 409."""
        await auth_client.post(
            "/vault/admin/users",
            json={"name": "Eve", "email": "eve@vault.local"},
        )
        response = await auth_client.post(
            "/vault/admin/users",
            json={"name": "Eve Duplicate", "email": "eve@vault.local"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "duplicate_email"


class TestApiKeyEndpoints:
    async def test_list_keys(self, auth_client):
        """GET /vault/admin/keys returns a list of API keys."""
        response = await auth_client.get("/vault/admin/keys")
        assert response.status_code == 200
        keys = response.json()
        assert isinstance(keys, list)
        assert len(keys) >= 1

    async def test_create_key(self, auth_client):
        """POST /vault/admin/keys creates a key and returns the raw key."""
        response = await auth_client.post(
            "/vault/admin/keys",
            json={"label": "test-admin-key", "scope": "admin"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["key"].startswith("vault_sk_")
        assert data["label"] == "test-admin-key"
        assert data["scope"] == "admin"
        assert "id" in data

    async def test_update_key_label(self, auth_client):
        """PUT /vault/admin/keys/{id} updates the key label."""
        create_resp = await auth_client.post(
            "/vault/admin/keys",
            json={"label": "original-label", "scope": "user"},
        )
        key_id = create_resp.json()["id"]

        response = await auth_client.put(
            f"/vault/admin/keys/{key_id}",
            json={"label": "updated-label"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["label"] == "updated-label"
        assert data["id"] == key_id

    async def test_update_key_deactivate(self, auth_client):
        """PUT /vault/admin/keys/{id} can deactivate a key."""
        create_resp = await auth_client.post(
            "/vault/admin/keys",
            json={"label": "to-deactivate", "scope": "user"},
        )
        key_id = create_resp.json()["id"]

        response = await auth_client.put(
            f"/vault/admin/keys/{key_id}",
            json={"is_active": False},
        )
        assert response.status_code == 200
        assert response.json()["is_active"] is False

    async def test_update_key_not_found(self, auth_client):
        """PUT /vault/admin/keys/{id} returns 404 for missing key."""
        response = await auth_client.put(
            "/vault/admin/keys/99999",
            json={"label": "nope"},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    async def test_revoke_key(self, auth_client):
        """DELETE /vault/admin/keys/{id} revokes the key."""
        create_resp = await auth_client.post(
            "/vault/admin/keys",
            json={"label": "to-revoke", "scope": "user"},
        )
        key_id = create_resp.json()["id"]

        response = await auth_client.delete(f"/vault/admin/keys/{key_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "revoked"


class TestConfigEndpoints:
    async def test_get_network_config(self, auth_client):
        """GET /vault/admin/config/network returns network defaults."""
        response = await auth_client.get("/vault/admin/config/network")
        assert response.status_code == 200
        data = response.json()
        assert data["hostname"] == "vault-cube"
        assert "ip_address" in data
        assert data["network_mode"] == "lan"

    async def test_update_network_config(self, auth_client):
        """PUT /vault/admin/config/network updates and returns config."""
        response = await auth_client.put(
            "/vault/admin/config/network",
            json={"hostname": "my-cube", "dns_servers": ["1.1.1.1"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["hostname"] == "my-cube"
        assert data["dns_servers"] == ["1.1.1.1"]

    async def test_get_system_settings(self, auth_client):
        """GET /vault/admin/config/system returns system defaults."""
        response = await auth_client.get("/vault/admin/config/system")
        assert response.status_code == 200
        data = response.json()
        assert data["timezone"] == "UTC"
        assert data["auto_update"] is False
        assert data["session_timeout"] == 3600
        assert data["debug_logging"] is False
        assert data["diagnostics_enabled"] is True

    async def test_update_system_settings(self, auth_client):
        """PUT /vault/admin/config/system updates and returns settings."""
        response = await auth_client.put(
            "/vault/admin/config/system",
            json={"timezone": "America/New_York", "auto_update": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["timezone"] == "America/New_York"
        assert data["auto_update"] is True

    async def test_update_debug_settings(self, auth_client):
        """PUT /vault/admin/config/system can toggle debug_logging and diagnostics_enabled."""
        response = await auth_client.put(
            "/vault/admin/config/system",
            json={"debug_logging": True, "diagnostics_enabled": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["debug_logging"] is True
        assert data["diagnostics_enabled"] is False

        # Verify persisted via GET
        get_resp = await auth_client.get("/vault/admin/config/system")
        assert get_resp.status_code == 200
        assert get_resp.json()["debug_logging"] is True
        assert get_resp.json()["diagnostics_enabled"] is False


class TestAdminAuth:
    async def test_401_without_auth(self, anon_client):
        """Admin endpoints require authentication."""
        response = await anon_client.get("/vault/admin/users")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_required"

    async def test_403_with_user_scope(self, user_client):
        """Admin endpoints reject user-scoped API keys with 403."""
        response = await user_client.get("/vault/admin/users")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "insufficient_permissions"

    async def test_403_user_scope_on_keys(self, user_client):
        """User-scoped keys cannot manage API keys."""
        response = await user_client.get("/vault/admin/keys")
        assert response.status_code == 403

    async def test_403_user_scope_on_config(self, user_client):
        """User-scoped keys cannot access admin config."""
        response = await user_client.get("/vault/admin/config/network")
        assert response.status_code == 403
