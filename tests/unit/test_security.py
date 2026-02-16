from app.core.security import generate_api_key, get_key_prefix, hash_api_key


def test_generate_api_key_format():
    key = generate_api_key()
    assert key.startswith("vault_sk_")
    assert len(key) == 9 + 48  # prefix + 48 hex chars


def test_generate_api_key_unique():
    keys = {generate_api_key() for _ in range(100)}
    assert len(keys) == 100


def test_hash_api_key_deterministic():
    key = generate_api_key()
    assert hash_api_key(key) == hash_api_key(key)


def test_hash_api_key_different_keys():
    k1, k2 = generate_api_key(), generate_api_key()
    assert hash_api_key(k1) != hash_api_key(k2)


def test_get_key_prefix():
    key = "vault_sk_abcdef1234567890"
    assert get_key_prefix(key) == "vault_sk_abc"
