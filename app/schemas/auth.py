from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    expires_in: int
    user: "AuthUser"


class AuthUser(BaseModel):
    id: str
    name: str
    email: str
    role: str
    auth_source: str


class AuthMeResponse(BaseModel):
    auth_type: str  # "jwt" or "key"
    user: AuthUser | None = None
    key_prefix: str | None = None
    key_scope: str | None = None
