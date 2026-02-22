from pydantic import BaseModel


class UserCreate(BaseModel):
    name: str
    email: str
    role: str = "user"


class UserUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    role: str | None = None
    status: str | None = None


class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    role: str
    status: str
    last_active: str | None
    api_key_count: int = 0
    created_at: str


class KeyCreate(BaseModel):
    label: str
    scope: str = "user"
    notes: str | None = None


class KeyUpdate(BaseModel):
    label: str | None = None
    is_active: bool | None = None


class KeyResponse(BaseModel):
    id: int
    key_prefix: str
    label: str
    scope: str
    is_active: bool
    created_at: str
    last_used_at: str | None


class KeyCreateResponse(BaseModel):
    key: str  # Raw key — only available at creation time
    id: int
    key_prefix: str
    label: str
    scope: str


class NetworkConfigResponse(BaseModel):
    hostname: str
    ip_address: str
    subnet_mask: str
    gateway: str
    dns_servers: list[str]
    network_mode: str


class NetworkConfigUpdate(BaseModel):
    hostname: str | None = None
    dns_servers: list[str] | None = None


class SystemSettingsResponse(BaseModel):
    timezone: str
    language: str
    auto_update: bool
    telemetry: bool
    session_timeout: int
    max_upload_size: int
    debug_logging: bool
    diagnostics_enabled: bool


class SystemSettingsUpdate(BaseModel):
    timezone: str | None = None
    language: str | None = None
    auto_update: bool | None = None
    telemetry: bool | None = None
    session_timeout: int | None = None
    max_upload_size: int | None = None
    debug_logging: bool | None = None
    diagnostics_enabled: bool | None = None
