from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import ApiKey, async_session as default_session_factory
from app.core.security import generate_api_key, hash_api_key, get_key_prefix


class AuthService:
    def __init__(self, session_factory: async_sessionmaker | None = None):
        self._session_factory = session_factory or default_session_factory

    async def create_key(self, label: str, scope: str = "user", notes: str | None = None) -> tuple[str, ApiKey]:
        """Create a new API key. Returns (raw_key, key_row). The raw key is only available at creation time."""
        raw_key = generate_api_key()
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label=label,
            scope=scope,
            notes=notes,
            is_active=True,
        )
        async with self._session_factory() as session:
            session.add(key_row)
            await session.commit()
            await session.refresh(key_row)
        return raw_key, key_row

    async def list_keys(self) -> list[ApiKey]:
        """List all active API keys (never returns the hash directly -- only prefix)."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(ApiKey).where(ApiKey.is_active == True).order_by(ApiKey.created_at.desc())
            )
            return list(result.scalars().all())

    async def revoke_key(self, key_identifier: str) -> bool:
        """Revoke a key by prefix or full key. Returns True if found and revoked."""
        async with self._session_factory() as session:
            if key_identifier.startswith("vault_sk_") and len(key_identifier) > 20:
                # Full key -- hash and look up
                key_hash = hash_api_key(key_identifier)
                result = await session.execute(
                    select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)
                )
            else:
                # Prefix match
                result = await session.execute(
                    select(ApiKey).where(ApiKey.key_prefix == key_identifier, ApiKey.is_active == True)
                )

            key_row = result.scalar_one_or_none()
            if key_row is None:
                return False

            key_row.is_active = False
            await session.commit()
            return True

    async def validate_key(self, raw_key: str) -> ApiKey | None:
        """Validate a raw API key. Returns the key row if valid, None otherwise."""
        key_hash = hash_api_key(raw_key)
        async with self._session_factory() as session:
            result = await session.execute(
                select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)
            )
            return result.scalar_one_or_none()
