from pydantic import BaseModel


class LdapConfig(BaseModel):
    enabled: bool = False
    url: str = "ldap://localhost:389"
    bind_dn: str = ""
    bind_password: str = ""
    user_search_base: str = ""
    group_search_base: str = ""
    user_search_filter: str = "(sAMAccountName={username})"
    use_ssl: bool = False
    default_role: str = "user"


class LdapConfigUpdate(BaseModel):
    enabled: bool | None = None
    url: str | None = None
    bind_dn: str | None = None
    bind_password: str | None = None
    user_search_base: str | None = None
    group_search_base: str | None = None
    user_search_filter: str | None = None
    use_ssl: bool | None = None
    default_role: str | None = None


class LdapTestResult(BaseModel):
    success: bool
    message: str
    users_found: int = 0
    groups_found: int = 0


class LdapSyncResult(BaseModel):
    success: bool
    users_created: int = 0
    users_updated: int = 0
    users_deactivated: int = 0
    errors: list[str] = []


class LdapGroupMappingCreate(BaseModel):
    ldap_group_dn: str
    vault_role: str = "user"
    priority: int = 0


class LdapGroupMappingUpdate(BaseModel):
    ldap_group_dn: str | None = None
    vault_role: str | None = None
    priority: int | None = None


class LdapGroupMappingResponse(BaseModel):
    id: int
    ldap_group_dn: str
    vault_role: str
    priority: int
    created_at: str
