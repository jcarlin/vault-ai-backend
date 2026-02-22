"""Unit tests for LDAP sync service."""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock

from app.core.database import Base, LdapGroupMapping, User
from app.services.ldap_sync import LdapSyncService


@pytest_asyncio.fixture
async def sync_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def sync_session_factory(sync_engine):
    return async_sessionmaker(sync_engine, class_=AsyncSession, expire_on_commit=False)


class TestLdapSyncFullSync:

    @pytest.mark.asyncio
    async def test_creates_new_users(self, sync_session_factory):
        mock_ldap = AsyncMock()
        mock_ldap.search_users.return_value = [
            {
                "dn": "cn=John,ou=Users,dc=test",
                "username": "john",
                "name": "John Doe",
                "email": "john@test.com",
                "groups": [],
                "disabled": False,
            },
            {
                "dn": "cn=Jane,ou=Users,dc=test",
                "username": "jane",
                "name": "Jane Smith",
                "email": "jane@test.com",
                "groups": [],
                "disabled": False,
            },
        ]

        sync_svc = LdapSyncService(
            ldap_service=mock_ldap,
            session_factory=sync_session_factory,
            default_role="user",
        )
        result = await sync_svc.full_sync()

        assert result["success"] is True
        assert result["users_created"] == 2
        assert result["users_updated"] == 0
        assert result["users_deactivated"] == 0

        # Verify in DB
        async with sync_session_factory() as session:
            users = (await session.execute(select(User))).scalars().all()
            assert len(users) == 2
            assert all(u.auth_source == "ldap" for u in users)

    @pytest.mark.asyncio
    async def test_updates_existing_users(self, sync_session_factory):
        # Pre-create user
        async with sync_session_factory() as session:
            user = User(
                id="existing-1",
                name="Old Name",
                email="john@test.com",
                role="user",
                status="active",
                ldap_dn="cn=John,ou=Users,dc=test",
                auth_source="ldap",
            )
            session.add(user)
            await session.commit()

        mock_ldap = AsyncMock()
        mock_ldap.search_users.return_value = [
            {
                "dn": "cn=John,ou=Users,dc=test",
                "username": "john",
                "name": "John Updated",
                "email": "john@test.com",
                "groups": [],
                "disabled": False,
            },
        ]

        sync_svc = LdapSyncService(
            ldap_service=mock_ldap,
            session_factory=sync_session_factory,
        )
        result = await sync_svc.full_sync()

        assert result["users_updated"] == 1
        assert result["users_created"] == 0

        async with sync_session_factory() as session:
            user = (await session.execute(select(User).where(User.id == "existing-1"))).scalar_one()
            assert user.name == "John Updated"

    @pytest.mark.asyncio
    async def test_deactivates_removed_users(self, sync_session_factory):
        # Pre-create LDAP user
        async with sync_session_factory() as session:
            user = User(
                id="removed-1",
                name="Removed User",
                email="removed@test.com",
                role="user",
                status="active",
                ldap_dn="cn=Removed,ou=Users,dc=test",
                auth_source="ldap",
            )
            session.add(user)
            await session.commit()

        # Sync returns empty (user no longer in directory)
        mock_ldap = AsyncMock()
        mock_ldap.search_users.return_value = []

        sync_svc = LdapSyncService(
            ldap_service=mock_ldap,
            session_factory=sync_session_factory,
        )
        result = await sync_svc.full_sync()

        assert result["users_deactivated"] == 1

        async with sync_session_factory() as session:
            user = (await session.execute(select(User).where(User.id == "removed-1"))).scalar_one()
            assert user.status == "inactive"

    @pytest.mark.asyncio
    async def test_role_resolution_from_group_mappings(self, sync_session_factory):
        # Create a group mapping
        async with sync_session_factory() as session:
            mapping = LdapGroupMapping(
                ldap_group_dn="cn=admins,ou=Groups,dc=test",
                vault_role="admin",
                priority=10,
            )
            session.add(mapping)
            await session.commit()

        mock_ldap = AsyncMock()
        mock_ldap.search_users.return_value = [
            {
                "dn": "cn=Admin User,ou=Users,dc=test",
                "username": "adminuser",
                "name": "Admin User",
                "email": "admin@test.com",
                "groups": ["cn=admins,ou=Groups,dc=test"],
                "disabled": False,
            },
        ]

        sync_svc = LdapSyncService(
            ldap_service=mock_ldap,
            session_factory=sync_session_factory,
            default_role="user",
        )
        result = await sync_svc.full_sync()

        assert result["users_created"] == 1

        async with sync_session_factory() as session:
            user = (await session.execute(
                select(User).where(User.email == "admin@test.com")
            )).scalar_one()
            assert user.role == "admin"

    @pytest.mark.asyncio
    async def test_disabled_users_get_inactive_status(self, sync_session_factory):
        mock_ldap = AsyncMock()
        mock_ldap.search_users.return_value = [
            {
                "dn": "cn=Disabled,ou=Users,dc=test",
                "username": "disabled",
                "name": "Disabled User",
                "email": "disabled@test.com",
                "groups": [],
                "disabled": True,
            },
        ]

        sync_svc = LdapSyncService(
            ldap_service=mock_ldap,
            session_factory=sync_session_factory,
        )
        result = await sync_svc.full_sync()

        async with sync_session_factory() as session:
            user = (await session.execute(
                select(User).where(User.email == "disabled@test.com")
            )).scalar_one()
            assert user.status == "inactive"

    @pytest.mark.asyncio
    async def test_sync_handles_errors_gracefully(self, sync_session_factory):
        mock_ldap = AsyncMock()
        mock_ldap.search_users.return_value = [
            {
                "dn": "",  # Empty DN — should be skipped
                "username": "nodn",
                "name": "No DN",
                "email": "nodn@test.com",
                "groups": [],
                "disabled": False,
            },
            {
                "dn": "cn=Good,ou=Users,dc=test",
                "username": "good",
                "name": "Good User",
                "email": "good@test.com",
                "groups": [],
                "disabled": False,
            },
        ]

        sync_svc = LdapSyncService(
            ldap_service=mock_ldap,
            session_factory=sync_session_factory,
        )
        result = await sync_svc.full_sync()

        # Empty DN is skipped, good user is created
        assert result["users_created"] == 1

    @pytest.mark.asyncio
    async def test_links_existing_local_user_by_email(self, sync_session_factory):
        """If a local user exists with the same email, link them to LDAP."""
        async with sync_session_factory() as session:
            user = User(
                id="local-1",
                name="Local User",
                email="shared@test.com",
                role="user",
                status="active",
                auth_source="local",
            )
            session.add(user)
            await session.commit()

        mock_ldap = AsyncMock()
        mock_ldap.search_users.return_value = [
            {
                "dn": "cn=LDAP User,ou=Users,dc=test",
                "username": "ldapuser",
                "name": "LDAP User",
                "email": "shared@test.com",
                "groups": [],
                "disabled": False,
            },
        ]

        sync_svc = LdapSyncService(
            ldap_service=mock_ldap,
            session_factory=sync_session_factory,
        )
        result = await sync_svc.full_sync()

        assert result["users_updated"] == 1
        assert result["users_created"] == 0

        async with sync_session_factory() as session:
            user = (await session.execute(select(User).where(User.id == "local-1"))).scalar_one()
            assert user.auth_source == "ldap"
            assert user.ldap_dn == "cn=LDAP User,ou=Users,dc=test"
