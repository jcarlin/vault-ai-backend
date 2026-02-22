"""Integration tests for Epic 14 — auth endpoints, LDAP config, group mappings."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import ApiKey, Base, User
from app.core.security import generate_api_key, get_key_prefix, hash_api_key


@pytest_asyncio.fixture
async def auth_db_engine():
    """Separate in-memory DB engine for auth tests."""
    from sqlalchemy.ext.asyncio import create_async_engine
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def auth_app(auth_db_engine):
    """FastAPI app wired to a fresh test DB for auth tests."""
    import app.core.database as db_module
    import app.core.middleware as mw_module
    from app.config import settings

    original_engine = db_module.engine
    original_session = db_module.async_session
    original_mw_session = mw_module.async_session
    original_access_key = settings.vault_access_key
    original_deployment_mode = settings.vault_deployment_mode
    settings.vault_access_key = None
    settings.vault_deployment_mode = "cube"

    test_session_factory = async_sessionmaker(auth_db_engine, class_=AsyncSession, expire_on_commit=False)
    db_module.engine = auth_db_engine
    db_module.async_session = test_session_factory
    mw_module.async_session = test_session_factory

    from app.main import app

    # Wire fake vLLM
    from tests.mocks.fake_vllm import app as fake_vllm_app
    from app.services.inference.vllm_client import VLLMBackend
    fake_transport = ASGITransport(app=fake_vllm_app)
    fake_http_client = AsyncClient(transport=fake_transport, base_url="http://fake-vllm")
    backend = VLLMBackend(base_url="http://fake-vllm", http_client=fake_http_client)
    app.state.inference_backend = backend
    app.state.setup_complete = True

    yield app

    await fake_http_client.aclose()
    db_module.engine = original_engine
    db_module.async_session = original_session
    mw_module.async_session = original_mw_session
    settings.vault_access_key = original_access_key
    settings.vault_deployment_mode = original_deployment_mode


@pytest_asyncio.fixture
async def admin_client(auth_app, auth_db_engine):
    """Authenticated admin API key client."""
    session_factory = async_sessionmaker(auth_db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="auth-test-admin",
            scope="admin",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=auth_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def anon_client(auth_app):
    """Unauthenticated client."""
    transport = ASGITransport(app=auth_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ── Auth Endpoints ───────────────────────────────────────────────────────────


class TestLdapEnabledCheck:

    @pytest.mark.asyncio
    async def test_ldap_enabled_returns_false_by_default(self, anon_client):
        resp = await anon_client.get("/vault/auth/ldap-enabled")
        assert resp.status_code == 200
        assert resp.json()["ldap_enabled"] is False

    @pytest.mark.asyncio
    async def test_ldap_enabled_no_auth_required(self, anon_client):
        resp = await anon_client.get("/vault/auth/ldap-enabled")
        assert resp.status_code == 200


class TestLoginEndpoint:

    @pytest.mark.asyncio
    async def test_login_no_auth_required(self, anon_client):
        """Login endpoint should be accessible without auth."""
        resp = await anon_client.post("/vault/auth/login", json={
            "username": "test", "password": "wrong"
        })
        # Should get 401 (invalid credentials), not 403 (missing auth)
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_local_user_with_password(self, admin_client, anon_client):
        """Local user with password can log in and get JWT."""
        # Create a local user with password via admin endpoint
        resp = await admin_client.post("/vault/admin/users", json={
            "name": "Local Admin",
            "email": "admin@local.test",
            "role": "admin",
            "password": "SecurePass123!",
            "auth_source": "local",
        })
        assert resp.status_code == 201

        # Log in with those credentials
        resp = await anon_client.post("/vault/auth/login", json={
            "username": "admin@local.test",
            "password": "SecurePass123!",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] > 0
        assert data["user"]["name"] == "Local Admin"
        assert data["user"]["role"] == "admin"
        assert data["user"]["auth_source"] == "local"

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, admin_client, anon_client):
        """Wrong password returns 401."""
        await admin_client.post("/vault/admin/users", json={
            "name": "Test User",
            "email": "test@local.test",
            "role": "user",
            "password": "correct-password",
        })

        resp = await anon_client.post("/vault/auth/login", json={
            "username": "test@local.test",
            "password": "wrong-password",
        })
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(self, anon_client):
        resp = await anon_client.post("/vault/auth/login", json={
            "username": "nobody@test.com",
            "password": "whatever",
        })
        assert resp.status_code == 401


class TestAuthMe:

    @pytest.mark.asyncio
    async def test_me_with_api_key(self, admin_client):
        resp = await admin_client.get("/vault/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["auth_type"] == "key"
        assert data["key_prefix"] is not None
        assert data["key_scope"] == "admin"

    @pytest.mark.asyncio
    async def test_me_with_jwt(self, admin_client, anon_client, auth_app):
        """JWT-authenticated user gets their identity from /auth/me."""
        # Create local user
        await admin_client.post("/vault/admin/users", json={
            "name": "JWT User",
            "email": "jwt@test.com",
            "role": "user",
            "password": "password123",
        })

        # Login to get JWT
        resp = await anon_client.post("/vault/auth/login", json={
            "username": "jwt@test.com",
            "password": "password123",
        })
        assert resp.status_code == 200
        token = resp.json()["token"]

        # Use JWT to call /auth/me
        transport = ASGITransport(app=auth_app)
        async with AsyncClient(transport=transport, base_url="http://test") as jwt_client:
            jwt_client.headers["Authorization"] = f"Bearer {token}"
            resp = await jwt_client.get("/vault/auth/me")
            assert resp.status_code == 200
            data = resp.json()
            assert data["auth_type"] == "jwt"
            assert data["user"]["name"] == "JWT User"
            assert data["user"]["email"] == "jwt@test.com"
            assert data["user"]["role"] == "user"

    @pytest.mark.asyncio
    async def test_me_unauthenticated(self, anon_client):
        resp = await anon_client.get("/vault/auth/me")
        assert resp.status_code == 401


# ── JWT Middleware ────────────────────────────────────────────────────────────


class TestJWTMiddleware:

    @pytest.mark.asyncio
    async def test_jwt_accesses_regular_endpoints(self, admin_client, anon_client, auth_app):
        """A JWT token can access authenticated endpoints."""
        await admin_client.post("/vault/admin/users", json={
            "name": "Regular User",
            "email": "regular@test.com",
            "role": "user",
            "password": "password123",
        })

        resp = await anon_client.post("/vault/auth/login", json={
            "username": "regular@test.com",
            "password": "password123",
        })
        token = resp.json()["token"]

        transport = ASGITransport(app=auth_app)
        async with AsyncClient(transport=transport, base_url="http://test") as jwt_client:
            jwt_client.headers["Authorization"] = f"Bearer {token}"
            # User-scope endpoints should work
            resp = await jwt_client.get("/vault/health")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_jwt_user_cannot_access_admin(self, admin_client, anon_client, auth_app):
        """User-role JWT gets 403 on admin endpoints."""
        await admin_client.post("/vault/admin/users", json={
            "name": "Non Admin",
            "email": "nonadmin@test.com",
            "role": "user",
            "password": "password123",
        })

        resp = await anon_client.post("/vault/auth/login", json={
            "username": "nonadmin@test.com",
            "password": "password123",
        })
        token = resp.json()["token"]

        transport = ASGITransport(app=auth_app)
        async with AsyncClient(transport=transport, base_url="http://test") as jwt_client:
            jwt_client.headers["Authorization"] = f"Bearer {token}"
            resp = await jwt_client.get("/vault/admin/users")
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_jwt_admin_accesses_admin_endpoints(self, admin_client, anon_client, auth_app):
        """Admin-role JWT can access admin endpoints."""
        await admin_client.post("/vault/admin/users", json={
            "name": "JWT Admin",
            "email": "jwtadmin@test.com",
            "role": "admin",
            "password": "password123",
        })

        resp = await anon_client.post("/vault/auth/login", json={
            "username": "jwtadmin@test.com",
            "password": "password123",
        })
        token = resp.json()["token"]

        transport = ASGITransport(app=auth_app)
        async with AsyncClient(transport=transport, base_url="http://test") as jwt_client:
            jwt_client.headers["Authorization"] = f"Bearer {token}"
            resp = await jwt_client.get("/vault/admin/users")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_invalid_jwt_returns_401(self, auth_app):
        transport = ASGITransport(app=auth_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.headers["Authorization"] = "Bearer eyJinvalid.token.here"
            resp = await client.get("/vault/auth/me")
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_api_key_still_works(self, admin_client):
        """Existing API key auth path is completely unchanged."""
        resp = await admin_client.get("/vault/admin/users")
        assert resp.status_code == 200


# ── LDAP Config Endpoints ────────────────────────────────────────────────────


class TestLdapConfigEndpoints:

    @pytest.mark.asyncio
    async def test_get_ldap_config_defaults(self, admin_client):
        resp = await admin_client.get("/vault/admin/config/ldap")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert "url" in data
        assert "bind_dn" in data

    @pytest.mark.asyncio
    async def test_update_ldap_config(self, admin_client):
        resp = await admin_client.put("/vault/admin/config/ldap", json={
            "enabled": True,
            "url": "ldap://dc.example.com:389",
            "bind_dn": "cn=admin,dc=example,dc=com",
            "bind_password": "secret",
            "user_search_base": "ou=Users,dc=example,dc=com",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["url"] == "ldap://dc.example.com:389"
        assert data["bind_dn"] == "cn=admin,dc=example,dc=com"
        assert data["user_search_base"] == "ou=Users,dc=example,dc=com"

    @pytest.mark.asyncio
    async def test_ldap_config_persists(self, admin_client):
        await admin_client.put("/vault/admin/config/ldap", json={
            "url": "ldap://persist.test:636",
            "use_ssl": True,
        })
        resp = await admin_client.get("/vault/admin/config/ldap")
        data = resp.json()
        assert data["url"] == "ldap://persist.test:636"
        assert data["use_ssl"] is True

    @pytest.mark.asyncio
    async def test_ldap_test_when_disabled(self, admin_client):
        resp = await admin_client.post("/vault/admin/config/ldap/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "not enabled" in data["message"]

    @pytest.mark.asyncio
    async def test_ldap_sync_when_disabled(self, admin_client):
        resp = await admin_client.post("/vault/admin/ldap/sync")
        assert resp.status_code == 400


# ── Group Mapping Endpoints ──────────────────────────────────────────────────


class TestGroupMappingEndpoints:

    @pytest.mark.asyncio
    async def test_list_mappings_empty(self, admin_client):
        resp = await admin_client.get("/vault/admin/ldap/mappings")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_create_mapping(self, admin_client):
        resp = await admin_client.post("/vault/admin/ldap/mappings", json={
            "ldap_group_dn": "cn=admins,ou=Groups,dc=example,dc=com",
            "vault_role": "admin",
            "priority": 10,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["ldap_group_dn"] == "cn=admins,ou=Groups,dc=example,dc=com"
        assert data["vault_role"] == "admin"
        assert data["priority"] == 10

    @pytest.mark.asyncio
    async def test_create_duplicate_mapping_409(self, admin_client):
        await admin_client.post("/vault/admin/ldap/mappings", json={
            "ldap_group_dn": "cn=dup,dc=test",
            "vault_role": "user",
        })
        resp = await admin_client.post("/vault/admin/ldap/mappings", json={
            "ldap_group_dn": "cn=dup,dc=test",
            "vault_role": "admin",
        })
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_update_mapping(self, admin_client):
        resp = await admin_client.post("/vault/admin/ldap/mappings", json={
            "ldap_group_dn": "cn=update-test,dc=test",
            "vault_role": "user",
            "priority": 0,
        })
        mapping_id = resp.json()["id"]

        resp = await admin_client.put(f"/vault/admin/ldap/mappings/{mapping_id}", json={
            "vault_role": "admin",
            "priority": 100,
        })
        assert resp.status_code == 200
        assert resp.json()["vault_role"] == "admin"
        assert resp.json()["priority"] == 100

    @pytest.mark.asyncio
    async def test_delete_mapping(self, admin_client):
        resp = await admin_client.post("/vault/admin/ldap/mappings", json={
            "ldap_group_dn": "cn=delete-me,dc=test",
            "vault_role": "user",
        })
        mapping_id = resp.json()["id"]

        resp = await admin_client.delete(f"/vault/admin/ldap/mappings/{mapping_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        # Verify it's gone
        resp = await admin_client.get("/vault/admin/ldap/mappings")
        ids = [m["id"] for m in resp.json()]
        assert mapping_id not in ids

    @pytest.mark.asyncio
    async def test_update_nonexistent_mapping_404(self, admin_client):
        resp = await admin_client.put("/vault/admin/ldap/mappings/99999", json={
            "vault_role": "admin",
        })
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_nonexistent_mapping_404(self, admin_client):
        resp = await admin_client.delete("/vault/admin/ldap/mappings/99999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_mappings_ordered_by_priority(self, admin_client):
        await admin_client.post("/vault/admin/ldap/mappings", json={
            "ldap_group_dn": "cn=low,dc=test",
            "vault_role": "user",
            "priority": 1,
        })
        await admin_client.post("/vault/admin/ldap/mappings", json={
            "ldap_group_dn": "cn=high,dc=test",
            "vault_role": "admin",
            "priority": 100,
        })

        resp = await admin_client.get("/vault/admin/ldap/mappings")
        mappings = resp.json()
        assert len(mappings) >= 2
        # Highest priority first
        assert mappings[0]["priority"] >= mappings[-1]["priority"]


# ── User Management Extensions ───────────────────────────────────────────────


class TestUserManagementExtensions:

    @pytest.mark.asyncio
    async def test_create_user_with_auth_source(self, admin_client):
        resp = await admin_client.post("/vault/admin/users", json={
            "name": "LDAP User",
            "email": "ldapuser@test.com",
            "role": "user",
            "auth_source": "ldap",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["auth_source"] == "ldap"

    @pytest.mark.asyncio
    async def test_create_user_with_password(self, admin_client):
        resp = await admin_client.post("/vault/admin/users", json={
            "name": "Passworded User",
            "email": "pwuser@test.com",
            "role": "user",
            "password": "MySecretPass!",
        })
        assert resp.status_code == 201
        # Password hash should NOT be in response
        data = resp.json()
        assert "password_hash" not in data
        assert "password" not in data

    @pytest.mark.asyncio
    async def test_list_users_filter_by_auth_source(self, admin_client):
        await admin_client.post("/vault/admin/users", json={
            "name": "Local1", "email": "local1@test.com", "auth_source": "local",
        })
        await admin_client.post("/vault/admin/users", json={
            "name": "LDAP1", "email": "ldap1@test.com", "auth_source": "ldap",
        })

        # Filter local only
        resp = await admin_client.get("/vault/admin/users?auth_source=local")
        local_users = resp.json()
        assert all(u["auth_source"] == "local" for u in local_users)

        # Filter ldap only
        resp = await admin_client.get("/vault/admin/users?auth_source=ldap")
        ldap_users = resp.json()
        assert all(u["auth_source"] == "ldap" for u in ldap_users)

    @pytest.mark.asyncio
    async def test_user_response_includes_new_fields(self, admin_client):
        resp = await admin_client.post("/vault/admin/users", json={
            "name": "Full Fields",
            "email": "full@test.com",
        })
        data = resp.json()
        assert "auth_source" in data
        assert "ldap_dn" in data
        assert data["auth_source"] == "local"
        assert data["ldap_dn"] is None


# ── Setup SSO Endpoints ──────────────────────────────────────────────────────


class TestSetupSsoEndpoints:

    @pytest_asyncio.fixture
    async def setup_client(self, auth_app):
        auth_app.state.setup_complete = False
        transport = ASGITransport(app=auth_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
        auth_app.state.setup_complete = True

    @pytest.mark.asyncio
    async def test_skip_sso(self, setup_client):
        resp = await setup_client.post("/vault/setup/sso/skip")
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_configure_sso(self, setup_client):
        resp = await setup_client.post("/vault/setup/sso", json={
            "enabled": True,
            "url": "ldap://dc.test:389",
            "bind_dn": "cn=admin,dc=test",
            "bind_password": "secret",
            "user_search_base": "ou=Users,dc=test",
            "test_connection": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "configured"
        assert data["enabled"] is True
