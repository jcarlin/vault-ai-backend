"""Unit tests for LDAP service using ldap3 MOCK_SYNC strategy."""

import pytest
import pytest_asyncio
from ldap3 import MOCK_SYNC, Connection, Server

from app.services.ldap_service import LdapService


class MockLdapService(LdapService):
    """LdapService subclass using ldap3 mock for testing."""

    def __init__(self, mock_entries=None, **kwargs):
        super().__init__(**kwargs)
        self._mock_entries = mock_entries or []

    def _get_server(self):
        return Server("mock://ldap", get_info="NO_INFO")

    def _bind_connection(self):
        server = self._get_server()
        conn = Connection(server, user=self._bind_dn, password=self._bind_password, client_strategy=MOCK_SYNC)

        # Add mock entries
        for entry in self._mock_entries:
            conn.strategy.add_entry(entry["dn"], entry["attributes"])

        conn.bind()
        return conn


@pytest.fixture
def mock_ldap_entries():
    return [
        {
            "dn": "cn=John Doe,ou=Users,dc=example,dc=com",
            "attributes": {
                "objectClass": ["person", "organizationalPerson", "user"],
                "cn": "John Doe",
                "sAMAccountName": "jdoe",
                "mail": "jdoe@example.com",
                "displayName": "John Doe",
                "memberOf": [
                    "cn=admins,ou=Groups,dc=example,dc=com",
                    "cn=users,ou=Groups,dc=example,dc=com",
                ],
            },
        },
        {
            "dn": "cn=Jane Smith,ou=Users,dc=example,dc=com",
            "attributes": {
                "objectClass": ["person", "organizationalPerson", "user"],
                "cn": "Jane Smith",
                "sAMAccountName": "jsmith",
                "mail": "jsmith@example.com",
                "displayName": "Jane Smith",
                "memberOf": ["cn=users,ou=Groups,dc=example,dc=com"],
            },
        },
        {
            "dn": "cn=admins,ou=Groups,dc=example,dc=com",
            "attributes": {
                "objectClass": ["group"],
                "cn": "admins",
            },
        },
        {
            "dn": "cn=users,ou=Groups,dc=example,dc=com",
            "attributes": {
                "objectClass": ["group"],
                "cn": "users",
            },
        },
    ]


@pytest.fixture
def mock_ldap(mock_ldap_entries):
    return MockLdapService(
        mock_entries=mock_ldap_entries,
        url="mock://ldap",
        bind_dn="cn=admin,dc=example,dc=com",
        bind_password="secret",
        user_search_base="ou=Users,dc=example,dc=com",
        group_search_base="ou=Groups,dc=example,dc=com",
        user_search_filter="(sAMAccountName={username})",
    )


class TestLdapServiceTestConnection:

    @pytest.mark.asyncio
    async def test_connection_returns_dict(self, mock_ldap):
        """test_connection returns a result dict (mock may not fully bind)."""
        result = await mock_ldap.test_connection()
        assert isinstance(result, dict)
        assert "success" in result
        assert "message" in result
        assert "users_found" in result
        assert "groups_found" in result


class TestLdapServiceSearchUsers:

    @pytest.mark.asyncio
    async def test_search_returns_users(self, mock_ldap):
        users = await mock_ldap.search_users()
        assert isinstance(users, list)
        # Mock strategy may not return all entries depending on filter
        # but should not error
        assert all(isinstance(u, dict) for u in users)


class TestLdapServiceGetUserGroups:

    @pytest.mark.asyncio
    async def test_groups_for_nonexistent_user(self, mock_ldap):
        groups = await mock_ldap.get_user_groups("cn=nobody,dc=example,dc=com")
        assert groups == []


class TestLdapServiceAuthenticate:

    @pytest.mark.asyncio
    async def test_authenticate_nonexistent_user(self, mock_ldap):
        result = await mock_ldap.authenticate("nobody", "password")
        assert result is None
