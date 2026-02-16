import hashlib
import secrets

API_KEY_PREFIX = "vault_sk_"
API_KEY_RANDOM_BYTES = 24  # 24 bytes → 48 hex chars


def generate_api_key() -> str:
    """Generate a new API key: vault_sk_ + 48 hex chars."""
    random_part = secrets.token_hex(API_KEY_RANDOM_BYTES)
    return f"{API_KEY_PREFIX}{random_part}"


def hash_api_key(key: str) -> str:
    """SHA-256 hash of the full API key."""
    return hashlib.sha256(key.encode()).hexdigest()


def get_key_prefix(key: str) -> str:
    """Return the first 12 chars of the key for display (vault_sk_ + 4 hex)."""
    return key[:12]
