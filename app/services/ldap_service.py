import asyncio

import structlog
from ldap3 import ALL, SUBTREE, Connection, Server
from ldap3.core.exceptions import LDAPException

logger = structlog.get_logger()


class LdapService:
    """LDAP client for authentication and user/group queries.

    Supports both Active Directory and OpenLDAP via configurable search filters.
    All ldap3 calls are synchronous and wrapped in asyncio.to_thread().
    """

    def __init__(
        self,
        url: str,
        bind_dn: str,
        bind_password: str,
        user_search_base: str,
        group_search_base: str = "",
        user_search_filter: str = "(sAMAccountName={username})",
        use_ssl: bool = False,
    ):
        self._url = url
        self._bind_dn = bind_dn
        self._bind_password = bind_password
        self._user_search_base = user_search_base
        self._group_search_base = group_search_base
        self._user_search_filter = user_search_filter
        self._use_ssl = use_ssl

    def _get_server(self) -> Server:
        return Server(self._url, use_ssl=self._use_ssl, get_info=ALL)

    def _bind_connection(self) -> Connection:
        """Create a bound connection using the service account."""
        server = self._get_server()
        conn = Connection(server, user=self._bind_dn, password=self._bind_password, auto_bind=True)
        return conn

    async def test_connection(self) -> dict:
        """Test LDAP connectivity: bind + count users and groups."""
        def _test():
            try:
                conn = self._bind_connection()
                users_found = 0
                groups_found = 0

                # Count users
                if self._user_search_base:
                    conn.search(
                        self._user_search_base,
                        "(objectClass=person)",
                        search_scope=SUBTREE,
                        size_limit=1000,
                    )
                    users_found = len(conn.entries)

                # Count groups
                if self._group_search_base:
                    conn.search(
                        self._group_search_base,
                        "(|(objectClass=group)(objectClass=groupOfNames)(objectClass=posixGroup))",
                        search_scope=SUBTREE,
                        size_limit=1000,
                    )
                    groups_found = len(conn.entries)

                conn.unbind()
                return {
                    "success": True,
                    "message": "LDAP connection successful.",
                    "users_found": users_found,
                    "groups_found": groups_found,
                }
            except LDAPException as e:
                return {
                    "success": False,
                    "message": f"LDAP connection failed: {e}",
                    "users_found": 0,
                    "groups_found": 0,
                }

        return await asyncio.to_thread(_test)

    async def authenticate(self, username: str, password: str) -> dict | None:
        """Authenticate user via LDAP bind. Returns user info dict or None."""
        def _auth():
            try:
                conn = self._bind_connection()

                # Search for user
                search_filter = self._user_search_filter.replace("{username}", username)
                conn.search(
                    self._user_search_base,
                    search_filter,
                    search_scope=SUBTREE,
                    attributes=["dn", "cn", "mail", "sAMAccountName", "uid", "memberOf", "displayName"],
                )

                if not conn.entries:
                    logger.debug("ldap_user_not_found", username=username)
                    conn.unbind()
                    return None

                user_entry = conn.entries[0]
                user_dn = str(user_entry.entry_dn)
                conn.unbind()

                # Attempt bind as the user to verify password
                server = self._get_server()
                user_conn = Connection(server, user=user_dn, password=password)
                if not user_conn.bind():
                    logger.debug("ldap_bind_failed", username=username)
                    return None

                user_conn.unbind()

                # Extract attributes
                name = str(user_entry.displayName) if hasattr(user_entry, "displayName") and user_entry.displayName else str(user_entry.cn) if hasattr(user_entry, "cn") and user_entry.cn else username
                email = str(user_entry.mail) if hasattr(user_entry, "mail") and user_entry.mail else f"{username}@local"
                groups = []
                if hasattr(user_entry, "memberOf") and user_entry.memberOf:
                    groups = [str(g) for g in user_entry.memberOf]

                return {
                    "dn": user_dn,
                    "username": username,
                    "name": name,
                    "email": email,
                    "groups": groups,
                }
            except LDAPException as e:
                logger.warning("ldap_auth_error", username=username, error=str(e))
                return None

        return await asyncio.to_thread(_auth)

    async def search_users(self) -> list[dict]:
        """Search all users in the directory."""
        def _search():
            try:
                conn = self._bind_connection()
                conn.search(
                    self._user_search_base,
                    "(objectClass=person)",
                    search_scope=SUBTREE,
                    attributes=["dn", "cn", "mail", "sAMAccountName", "uid", "memberOf", "displayName", "userAccountControl", "modifyTimestamp", "whenChanged"],
                    size_limit=5000,
                )
                users = []
                for entry in conn.entries:
                    username = ""
                    if hasattr(entry, "sAMAccountName") and entry.sAMAccountName:
                        username = str(entry.sAMAccountName)
                    elif hasattr(entry, "uid") and entry.uid:
                        username = str(entry.uid)

                    name = str(entry.displayName) if hasattr(entry, "displayName") and entry.displayName else str(entry.cn) if hasattr(entry, "cn") and entry.cn else username
                    email = str(entry.mail) if hasattr(entry, "mail") and entry.mail else ""

                    # Check if account is disabled (AD userAccountControl bit 1)
                    disabled = False
                    if hasattr(entry, "userAccountControl") and entry.userAccountControl:
                        uac = int(str(entry.userAccountControl))
                        disabled = bool(uac & 2)

                    groups = []
                    if hasattr(entry, "memberOf") and entry.memberOf:
                        groups = [str(g) for g in entry.memberOf]

                    users.append({
                        "dn": str(entry.entry_dn),
                        "username": username,
                        "name": name,
                        "email": email,
                        "groups": groups,
                        "disabled": disabled,
                    })
                conn.unbind()
                return users
            except LDAPException as e:
                logger.warning("ldap_search_error", error=str(e))
                return []

        return await asyncio.to_thread(_search)

    async def get_user_groups(self, user_dn: str) -> list[str]:
        """Get group DNs for a specific user."""
        def _groups():
            try:
                conn = self._bind_connection()
                conn.search(
                    self._user_search_base,
                    f"(distinguishedName={user_dn})",
                    search_scope=SUBTREE,
                    attributes=["memberOf"],
                )
                if not conn.entries:
                    conn.unbind()
                    return []
                groups = []
                if hasattr(conn.entries[0], "memberOf") and conn.entries[0].memberOf:
                    groups = [str(g) for g in conn.entries[0].memberOf]
                conn.unbind()
                return groups
            except LDAPException as e:
                logger.warning("ldap_groups_error", error=str(e))
                return []

        return await asyncio.to_thread(_groups)
