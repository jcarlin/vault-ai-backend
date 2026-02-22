from fastapi import APIRouter, Depends

from app.core.exceptions import NotFoundError
from app.dependencies import require_admin
from app.schemas.admin import (
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
from app.services.admin import AdminService

router = APIRouter(dependencies=[Depends(require_admin)])


def _format_dt(dt) -> str | None:
    if dt is None:
        return None
    return dt.isoformat() + "Z"


# ── Users ───────────────────────────────────────────────────────────────────


@router.get("/vault/admin/users")
async def list_users() -> list[UserResponse]:
    service = AdminService()
    users = await service.list_users()
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
        )
        for u in users
    ]


@router.post("/vault/admin/users", status_code=201)
async def create_user(body: UserCreate) -> UserResponse:
    service = AdminService()
    user = await service.create_user(name=body.name, email=body.email, role=body.role)
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        status=user.status,
        last_active=_format_dt(user.last_active),
        api_key_count=0,
        created_at=_format_dt(user.created_at),
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
