from pydantic import BaseModel


class UserCreate(BaseModel):
    name: str
    email: str
    role: str = "user"
    password: str | None = None
    auth_source: str = "local"


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
    auth_source: str = "local"
    ldap_dn: str | None = None


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


class TlsInfoResponse(BaseModel):
    enabled: bool
    self_signed: bool
    issuer: str | None = None
    expires: str | None = None
    serial: str | None = None


class TlsUploadRequest(BaseModel):
    certificate: str
    private_key: str


class ModelConfigResponse(BaseModel):
    default_model_id: str
    default_temperature: float
    default_max_tokens: int
    default_system_prompt: str


class ModelConfigUpdate(BaseModel):
    default_model_id: str | None = None
    default_temperature: float | None = None
    default_max_tokens: int | None = None
    default_system_prompt: str | None = None


class FullConfigResponse(BaseModel):
    network: NetworkConfigResponse
    system: SystemSettingsResponse
    tls: TlsInfoResponse
    restart_required: bool = False


class FullConfigUpdate(BaseModel):
    network: NetworkConfigUpdate | None = None
    system: SystemSettingsUpdate | None = None
