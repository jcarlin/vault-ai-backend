import uuid

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import select

import app.core.database as db_module
from app.config import settings
from app.core.database import User
from app.core.exceptions import AuthenticationError, VaultError
from app.schemas.auth import AuthMeResponse, AuthUser, LoginRequest, LoginResponse
from app.services.jwt_service import JWTService
from app.services.ldap_service import LdapService

logger = structlog.get_logger()
router = APIRouter()

jwt_service = JWTService()


def _get_ldap_service_from_config(ldap_config: dict) -> LdapService:
    """Build an LdapService from the stored config dict."""
    return LdapService(
        url=ldap_config.get("url", settings.vault_ldap_url),
        bind_dn=ldap_config.get("bind_dn", settings.vault_ldap_bind_dn),
        bind_password=ldap_config.get("bind_password", settings.vault_ldap_bind_password),
        user_search_base=ldap_config.get("user_search_base", settings.vault_ldap_user_search_base),
        group_search_base=ldap_config.get("group_search_base", settings.vault_ldap_group_search_base),
        user_search_filter=ldap_config.get("user_search_filter", settings.vault_ldap_user_search_filter),
        use_ssl=ldap_config.get("use_ssl", settings.vault_ldap_use_ssl),
    )


async def _get_ldap_config() -> dict:
    """Load LDAP config from SystemConfig (DB) with env-var fallbacks."""
    from app.core.database import SystemConfig

    async with db_module.async_session() as session:
        result = await session.execute(
            select(SystemConfig).where(SystemConfig.key.startswith("ldap."))
        )
        rows = {r.key: r.value for r in result.scalars().all()}

    return {
        "enabled": (rows.get("ldap.enabled", str(settings.vault_ldap_enabled)).lower() == "true"),
        "url": rows.get("ldap.url", settings.vault_ldap_url),
        "bind_dn": rows.get("ldap.bind_dn", settings.vault_ldap_bind_dn),
        "bind_password": rows.get("ldap.bind_password", settings.vault_ldap_bind_password),
        "user_search_base": rows.get("ldap.user_search_base", settings.vault_ldap_user_search_base),
        "group_search_base": rows.get("ldap.group_search_base", settings.vault_ldap_group_search_base),
        "user_search_filter": rows.get("ldap.user_search_filter", settings.vault_ldap_user_search_filter),
        "use_ssl": (rows.get("ldap.use_ssl", str(settings.vault_ldap_use_ssl)).lower() == "true"),
        "default_role": rows.get("ldap.default_role", "user"),
    }


async def _resolve_role_from_groups(groups: list[str], default_role: str = "user") -> str:
    """Resolve vault role from LDAP group memberships using LdapGroupMapping table."""
    from app.core.database import LdapGroupMapping

    if not groups:
        return default_role

    async with db_module.async_session() as session:
        result = await session.execute(
            select(LdapGroupMapping).order_by(LdapGroupMapping.priority.desc())
        )
        mappings = list(result.scalars().all())

    if not mappings:
        return default_role

    # Find the highest-priority matching group
    for mapping in mappings:
        if mapping.ldap_group_dn in groups:
            return mapping.vault_role

    return default_role


async def _jit_provision_user(
    ldap_info: dict, role: str, auth_source: str = "ldap"
) -> User:
    """Just-In-Time provision: create or update a local User record from LDAP data."""
    async with db_module.async_session() as session:
        # Check if user exists by ldap_dn
        result = await session.execute(
            select(User).where(User.ldap_dn == ldap_info["dn"])
        )
        user = result.scalar_one_or_none()

        if user:
            # Update name/email/role if changed
            user.name = ldap_info["name"]
            user.email = ldap_info["email"]
            user.role = role
            user.status = "active"
            await session.commit()
            await session.refresh(user)
            return user

        # Also check by email (might exist as local user)
        result = await session.execute(
            select(User).where(User.email == ldap_info["email"])
        )
        user = result.scalar_one_or_none()

        if user:
            # Link existing user to LDAP
            user.ldap_dn = ldap_info["dn"]
            user.auth_source = auth_source
            user.name = ldap_info["name"]
            user.role = role
            user.status = "active"
            await session.commit()
            await session.refresh(user)
            return user

        # Create new user
        user = User(
            id=str(uuid.uuid4()),
            name=ldap_info["name"],
            email=ldap_info["email"],
            role=role,
            status="active",
            ldap_dn=ldap_info["dn"],
            auth_source=auth_source,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _authenticate_local(username: str, password: str) -> User | None:
    """Authenticate against local password hash (for local admin fallback)."""
    import bcrypt

    async with db_module.async_session() as session:
        # Look up by email or name
        result = await session.execute(
            select(User).where(
                (User.email == username) | (User.name == username),
                User.auth_source == "local",
                User.status == "active",
                User.password_hash.isnot(None),
            )
        )
        user = result.scalar_one_or_none()

        if user and bcrypt.checkpw(password.encode(), user.password_hash.encode()):
            return user
        return None


@router.post("/vault/auth/login")
async def login(body: LoginRequest) -> LoginResponse:
    """Authenticate via LDAP or local password and return a JWT."""
    ldap_config = await _get_ldap_config()

    user = None
    auth_source = "local"

    # Try LDAP first if enabled
    if ldap_config["enabled"]:
        ldap_svc = _get_ldap_service_from_config(ldap_config)
        ldap_info = await ldap_svc.authenticate(body.username, body.password)

        if ldap_info:
            role = await _resolve_role_from_groups(
                ldap_info.get("groups", []),
                default_role=ldap_config.get("default_role", "user"),
            )
            user = await _jit_provision_user(ldap_info, role, auth_source="ldap")
            auth_source = "ldap"

    # Fallback to local auth
    if user is None:
        user = await _authenticate_local(body.username, body.password)
        auth_source = "local"

    if user is None:
        raise AuthenticationError("Invalid username or password.")

    token = jwt_service.create_token(
        user_id=user.id,
        role=user.role,
        name=user.name,
        auth_source=auth_source,
    )

    logger.info("user_login", user_id=user.id, auth_source=auth_source)

    return LoginResponse(
        token=token,
        expires_in=settings.vault_jwt_expiry_seconds,
        user=AuthUser(
            id=user.id,
            name=user.name,
            email=user.email,
            role=user.role,
            auth_source=user.auth_source,
        ),
    )


@router.get("/vault/auth/me")
async def get_current_user(request: Request) -> AuthMeResponse:
    """Return the identity of the current authenticated user/key."""
    auth_type = getattr(request.state, "auth_type", "key")

    if auth_type == "jwt":
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            async with db_module.async_session() as session:
                result = await session.execute(
                    select(User).where(User.id == user_id)
                )
                user = result.scalar_one_or_none()
                if user:
                    return AuthMeResponse(
                        auth_type="jwt",
                        user=AuthUser(
                            id=user.id,
                            name=user.name,
                            email=user.email,
                            role=user.role,
                            auth_source=user.auth_source,
                        ),
                    )
        raise AuthenticationError("Invalid JWT session.")

    # API key auth
    return AuthMeResponse(
        auth_type="key",
        key_prefix=getattr(request.state, "api_key_prefix", None),
        key_scope=getattr(request.state, "api_key_scope", None),
    )


@router.get("/vault/auth/ldap-enabled")
async def check_ldap_enabled() -> dict:
    """Public endpoint to check if LDAP login is available (for frontend login page)."""
    ldap_config = await _get_ldap_config()
    return {"ldap_enabled": ldap_config["enabled"]}
