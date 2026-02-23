from fastapi import APIRouter, Depends

from app.core.exceptions import NotFoundError, VaultError
from app.dependencies import require_admin
from app.schemas.admin import (
    DevModeConfigResponse,
    DevModeConfigUpdate,
    FullConfigResponse,
    FullConfigUpdate,
    KeyCreate,
    KeyCreateResponse,
    KeyResponse,
    KeyUpdate,
    ModelConfigResponse,
    ModelConfigUpdate,
    NetworkConfigResponse,
    NetworkConfigUpdate,
    SystemSettingsResponse,
    SystemSettingsUpdate,
    TlsInfoResponse,
    TlsUploadRequest,
    UserCreate,
    UserResponse,
    UserUpdate,
)
from app.schemas.ldap import (
    LdapConfig,
    LdapConfigUpdate,
    LdapGroupMappingCreate,
    LdapGroupMappingResponse,
    LdapGroupMappingUpdate,
    LdapSyncResult,
    LdapTestResult,
)
from app.services.admin import AdminService

router = APIRouter(dependencies=[Depends(require_admin)])


def _format_dt(dt) -> str | None:
    if dt is None:
        return None
    return dt.isoformat() + "Z"


# ── Users ───────────────────────────────────────────────────────────────────


@router.get("/vault/admin/users")
async def list_users(auth_source: str | None = None) -> list[UserResponse]:
    service = AdminService()
    users = await service.list_users(auth_source=auth_source)
    return [
        UserResponse(
            id=u.id,
            name=u.name,
            email=u.email,
            role=u.role,
            status=u.status,
            last_active=_format_dt(u.last_active),
            api_key_count=0,
            created_at=_format_dt(u.created_at),
            auth_source=getattr(u, "auth_source", "local"),
            ldap_dn=getattr(u, "ldap_dn", None),
        )
        for u in users
    ]


@router.post("/vault/admin/users", status_code=201)
async def create_user(body: UserCreate) -> UserResponse:
    service = AdminService()
    user = await service.create_user(
        name=body.name,
        email=body.email,
        role=body.role,
        password=body.password,
        auth_source=body.auth_source,
    )
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        status=user.status,
        last_active=_format_dt(user.last_active),
        api_key_count=0,
        created_at=_format_dt(user.created_at),
        auth_source=user.auth_source,
        ldap_dn=user.ldap_dn,
    )


@router.put("/vault/admin/users/{user_id}")
async def update_user(user_id: str, body: UserUpdate) -> UserResponse:
    service = AdminService()
    updates = body.model_dump(exclude_none=True)
    user = await service.update_user(user_id, **updates)
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        status=user.status,
        last_active=_format_dt(user.last_active),
        api_key_count=0,
        created_at=_format_dt(user.created_at),
        auth_source=getattr(user, "auth_source", "local"),
        ldap_dn=getattr(user, "ldap_dn", None),
    )


@router.delete("/vault/admin/users/{user_id}")
async def deactivate_user(user_id: str) -> UserResponse:
    service = AdminService()
    user = await service.deactivate_user(user_id)
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        status=user.status,
        last_active=_format_dt(user.last_active),
        api_key_count=0,
        created_at=_format_dt(user.created_at),
        auth_source=getattr(user, "auth_source", "local"),
        ldap_dn=getattr(user, "ldap_dn", None),
    )


# ── API Keys ────────────────────────────────────────────────────────────────


@router.get("/vault/admin/keys")
async def list_keys() -> list[KeyResponse]:
    service = AdminService()
    keys = await service.list_keys()
    return [
        KeyResponse(
            id=k.id,
            key_prefix=k.key_prefix,
            label=k.label,
            scope=k.scope,
            is_active=k.is_active,
            created_at=_format_dt(k.created_at),
            last_used_at=_format_dt(k.last_used_at),
        )
        for k in keys
    ]


@router.post("/vault/admin/keys", status_code=201)
async def create_key(body: KeyCreate) -> KeyCreateResponse:
    service = AdminService()
    raw_key, key_row = await service.create_key(
        label=body.label, scope=body.scope, notes=body.notes
    )
    return KeyCreateResponse(
        key=raw_key,
        id=key_row.id,
        key_prefix=key_row.key_prefix,
        label=key_row.label,
        scope=key_row.scope,
    )


@router.put("/vault/admin/keys/{key_id}")
async def update_key(key_id: int, body: KeyUpdate) -> KeyResponse:
    service = AdminService()
    updates = body.model_dump(exclude_none=True)
    key_row = await service.update_key_by_id(key_id, **updates)
    return KeyResponse(
        id=key_row.id,
        key_prefix=key_row.key_prefix,
        label=key_row.label,
        scope=key_row.scope,
        is_active=key_row.is_active,
        created_at=_format_dt(key_row.created_at),
        last_used_at=_format_dt(key_row.last_used_at),
    )


@router.delete("/vault/admin/keys/{key_id}")
async def revoke_key(key_id: int) -> dict:
    service = AdminService()
    await service.revoke_key_by_id(key_id)
    return {"status": "revoked"}


# ── Network Config ──────────────────────────────────────────────────────────


@router.get("/vault/admin/config/network")
async def get_network_config() -> NetworkConfigResponse:
    service = AdminService()
    config = await service.get_network_config()
    return NetworkConfigResponse(**config)


@router.put("/vault/admin/config/network")
async def update_network_config(body: NetworkConfigUpdate) -> NetworkConfigResponse:
    service = AdminService()
    updates = body.model_dump(exclude_none=True)
    config = await service.update_network_config(**updates)
    return NetworkConfigResponse(**config)


# ── System Settings ─────────────────────────────────────────────────────────


@router.get("/vault/admin/config/system")
async def get_system_settings() -> SystemSettingsResponse:
    service = AdminService()
    settings = await service.get_system_settings()
    return SystemSettingsResponse(**settings)


@router.put("/vault/admin/config/system")
async def update_system_settings(body: SystemSettingsUpdate) -> SystemSettingsResponse:
    service = AdminService()
    updates = body.model_dump(exclude_none=True)
    settings = await service.update_system_settings(**updates)
    return SystemSettingsResponse(**settings)


# ── Model Config ──────────────────────────────────────────────────────────


@router.get("/vault/admin/config/models")
async def get_model_config() -> ModelConfigResponse:
    service = AdminService()
    config = await service.get_model_config()
    return ModelConfigResponse(**config)


@router.put("/vault/admin/config/models")
async def update_model_config(body: ModelConfigUpdate) -> ModelConfigResponse:
    service = AdminService()
    updates = body.model_dump(exclude_none=True)
    config = await service.update_model_config(**updates)
    return ModelConfigResponse(**config)


# ── Full Config ────────────────────────────────────────────────────────────


@router.get("/vault/admin/config")
async def get_full_config() -> FullConfigResponse:
    service = AdminService()
    config = await service.get_full_config()
    return FullConfigResponse(**config)


@router.put("/vault/admin/config")
async def update_full_config(body: FullConfigUpdate) -> FullConfigResponse:
    service = AdminService()
    result = await service.update_full_config(body.model_dump(exclude_none=True))
    return FullConfigResponse(**result)


# ── TLS ────────────────────────────────────────────────────────────────────


@router.get("/vault/admin/config/tls")
async def get_tls_info() -> TlsInfoResponse:
    service = AdminService()
    info = await service.get_tls_info()
    return TlsInfoResponse(**info)


@router.post("/vault/admin/config/tls")
async def upload_tls_cert(body: TlsUploadRequest) -> TlsInfoResponse:
    service = AdminService()
    info = await service.upload_tls_cert(body.certificate, body.private_key)
    return TlsInfoResponse(**info)


# ── LDAP Config ───────────────────────────────────────────────────────────


@router.get("/vault/admin/config/ldap")
async def get_ldap_config() -> LdapConfig:
    service = AdminService()
    config = await service.get_ldap_config()
    return LdapConfig(**config)


@router.put("/vault/admin/config/ldap")
async def update_ldap_config(body: LdapConfigUpdate) -> LdapConfig:
    service = AdminService()
    updates = body.model_dump(exclude_none=True)
    config = await service.update_ldap_config(**updates)
    return LdapConfig(**config)


@router.post("/vault/admin/config/ldap/test")
async def test_ldap_connection() -> LdapTestResult:
    service = AdminService()
    config = await service.get_ldap_config()

    if not config.get("enabled"):
        return LdapTestResult(success=False, message="LDAP is not enabled.")

    from app.services.ldap_service import LdapService
    ldap_svc = LdapService(
        url=config["url"],
        bind_dn=config["bind_dn"],
        bind_password=config["bind_password"],
        user_search_base=config["user_search_base"],
        group_search_base=config.get("group_search_base", ""),
        user_search_filter=config.get("user_search_filter", "(sAMAccountName={username})"),
        use_ssl=config.get("use_ssl", False),
    )
    result = await ldap_svc.test_connection()
    return LdapTestResult(**result)


@router.post("/vault/admin/ldap/sync")
async def trigger_ldap_sync() -> LdapSyncResult:
    service = AdminService()
    config = await service.get_ldap_config()

    if not config.get("enabled"):
        raise VaultError(code="ldap_not_enabled", message="LDAP is not enabled.", status=400)

    from app.services.ldap_service import LdapService
    from app.services.ldap_sync import LdapSyncService

    ldap_svc = LdapService(
        url=config["url"],
        bind_dn=config["bind_dn"],
        bind_password=config["bind_password"],
        user_search_base=config["user_search_base"],
        group_search_base=config.get("group_search_base", ""),
        user_search_filter=config.get("user_search_filter", "(sAMAccountName={username})"),
        use_ssl=config.get("use_ssl", False),
    )
    sync_svc = LdapSyncService(
        ldap_service=ldap_svc,
        default_role=config.get("default_role", "user"),
    )
    result = await sync_svc.full_sync()
    return LdapSyncResult(**result)


# ── LDAP Group Mappings ──────────────────────────────────────────────────


@router.get("/vault/admin/ldap/mappings")
async def list_ldap_mappings() -> list[LdapGroupMappingResponse]:
    service = AdminService()
    mappings = await service.list_ldap_mappings()
    return [
        LdapGroupMappingResponse(
            id=m.id,
            ldap_group_dn=m.ldap_group_dn,
            vault_role=m.vault_role,
            priority=m.priority,
            created_at=_format_dt(m.created_at),
        )
        for m in mappings
    ]


@router.post("/vault/admin/ldap/mappings", status_code=201)
async def create_ldap_mapping(body: LdapGroupMappingCreate) -> LdapGroupMappingResponse:
    service = AdminService()
    mapping = await service.create_ldap_mapping(
        ldap_group_dn=body.ldap_group_dn,
        vault_role=body.vault_role,
        priority=body.priority,
    )
    return LdapGroupMappingResponse(
        id=mapping.id,
        ldap_group_dn=mapping.ldap_group_dn,
        vault_role=mapping.vault_role,
        priority=mapping.priority,
        created_at=_format_dt(mapping.created_at),
    )


@router.put("/vault/admin/ldap/mappings/{mapping_id}")
async def update_ldap_mapping(mapping_id: int, body: LdapGroupMappingUpdate) -> LdapGroupMappingResponse:
    service = AdminService()
    updates = body.model_dump(exclude_none=True)
    mapping = await service.update_ldap_mapping(mapping_id, **updates)
    return LdapGroupMappingResponse(
        id=mapping.id,
        ldap_group_dn=mapping.ldap_group_dn,
        vault_role=mapping.vault_role,
        priority=mapping.priority,
        created_at=_format_dt(mapping.created_at),
    )


@router.delete("/vault/admin/ldap/mappings/{mapping_id}")
async def delete_ldap_mapping(mapping_id: int) -> dict:
    service = AdminService()
    await service.delete_ldap_mapping(mapping_id)
    return {"status": "deleted"}


# ── DevMode Config ────────────────────────────────────────────────────────


@router.get("/vault/admin/config/devmode")
async def get_devmode_config() -> DevModeConfigResponse:
    service = AdminService()
    config = await service.get_devmode_config()
    return DevModeConfigResponse(**config)


@router.put("/vault/admin/config/devmode")
async def update_devmode_config(body: DevModeConfigUpdate) -> DevModeConfigResponse:
    service = AdminService()
    updates = body.model_dump(exclude_none=True)
    config = await service.update_devmode_config(**updates)
    return DevModeConfigResponse(**config)
