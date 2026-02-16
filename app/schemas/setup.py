from pydantic import BaseModel


class SetupStatusResponse(BaseModel):
    status: str  # "pending", "in_progress", "complete"
    completed_steps: list[str] = []
    current_step: str | None = None


class SetupNetworkRequest(BaseModel):
    hostname: str
    ip_mode: str = "dhcp"  # "dhcp" or "static"
    ip_address: str | None = None
    subnet_mask: str | None = None
    gateway: str | None = None
    dns_servers: list[str] | None = None


class SetupAdminRequest(BaseModel):
    name: str
    email: str


class SetupAdminResponse(BaseModel):
    user_id: str
    api_key: str  # raw key, shown once
    key_prefix: str


class SetupTlsRequest(BaseModel):
    mode: str = "self_signed"  # "self_signed" or "custom"
    certificate: str | None = None  # PEM string
    private_key: str | None = None  # PEM string


class SetupModelRequest(BaseModel):
    model_id: str


class VerificationCheck(BaseModel):
    name: str
    passed: bool
    message: str
    latency_ms: float | None = None


class SetupVerifyResponse(BaseModel):
    status: str  # "pass" or "fail"
    checks: list[VerificationCheck]


class SetupCompleteResponse(BaseModel):
    status: str
    message: str
